from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio

LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
SLO_CONFIG = REPO_ROOT / "config" / "slo.yaml"
OUT = REPO_ROOT / "submission" / "evidence" / "dashboard_6panels.png"

WINDOW_MINUTES = 60


def load_logs() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, round((p / 100) * len(s)) - 1))
    return s[idx]


def load_slo() -> dict:
    cfg = yaml.safe_load(SLO_CONFIG.read_text(encoding="utf-8"))["slis"]
    return {
        "latency": cfg["latency_p95_ms"]["objective"],
        "error": cfg["error_rate_pct"]["objective"],
        "cost": cfg["daily_cost_usd"]["objective"],
        "quality": cfg["quality_score_avg"]["objective"],
    }


def main() -> int:
    configure_utf8_stdio()
    rows = load_logs()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    logs = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            logs.append(r)

    slo = load_slo()
    window = f"last {WINDOW_MINUTES} min (UTC)"

    sent = [r for r in logs if r.get("event") == "response_sent"]
    recv = [r for r in logs if r.get("event") == "request_received"]
    failed = [r for r in logs if r.get("event") == "request_failed"]

    latencies = [r.get("latency_ms", 0) for r in sent]
    p50, p95, p99 = (
        percentile(latencies, 50), percentile(latencies, 95), percentile(latencies, 99)
    )
    error_rate = (len(failed) / len(recv) * 100) if recv else 0.0
    total_cost = sum(r.get("cost_usd", 0) for r in sent)
    tok_in = sum(r.get("tokens_in", 0) for r in sent)
    tok_out = sum(r.get("tokens_out", 0) for r in sent)
    quality = (sum(r.get("quality_score", 0) for r in sent) / len(sent)) if sent else 0.0

    fig, axs = plt.subplots(2, 3, figsize=(16, 9))
    fig.suptitle(f"Day 13 AI Observability — {window}", fontsize=14, fontweight="bold")

    # 1) Latency
    ax = axs[0, 0]
    if sent:
        times = [datetime.fromisoformat(r["ts"]) for r in sent]
        ax.plot(times, [r.get("latency_ms", 0) for r in sent], "o-", ms=4)
    ax.axhline(slo["latency"], color="red", ls="--", lw=1, label=f"SLO p95 ≤ {slo['latency']:.0f}ms")
    ax.set_title(f"1. Latency p50={p50:.0f} p95={p95:.0f} p99={p99:.0f} ms")
    ax.set_ylabel("ms"); ax.legend(fontsize=8); ax.tick_params(axis='x', rotation=45, labelsize=7)

    # 2) Traffic
    ax = axs[0, 1]
    rpm = {}
    for r in recv:
        t = datetime.fromisoformat(r["ts"]).replace(second=0, microsecond=0)
        rpm[t] = rpm.get(t, 0) + 1
    if rpm:
        ax.bar(list(rpm.keys()), list(rpm.values()))
    ax.set_title(f"2. Traffic: {len(recv)} req in window")
    ax.set_ylabel("requests/min"); ax.tick_params(axis='x', rotation=45, labelsize=7)

    # 3) Errors
    ax = axs[0, 2]
    names = []
    counts = []
    for r in failed:
        et = r.get("error_type", "unknown")
        if et in names:
            counts[names.index(et)] += 1
        else:
            names.append(et); counts.append(1)
    if names:
        ax.bar(names, counts)
    else:
        ax.text(0.3, 0.5, "no failures", fontsize=10)
    ax.axhline(slo["error"], color="red", ls="--", lw=1, label=f"SLO error ≤ {slo['error']}%")
    ax.set_title(f"3. Error rate {error_rate:.2f}%")
    ax.set_ylabel("count"); ax.legend(fontsize=8); ax.tick_params(axis='x', rotation=45, labelsize=7)

    # 4) Cost
    ax = axs[1, 0]
    if sent:
        cost_by_min = {}
        for r in sent:
            t = datetime.fromisoformat(r["ts"]).replace(second=0, microsecond=0)
            cost_by_min[t] = cost_by_min.get(t, 0) + r.get("cost_usd", 0)
        ax.plot(list(cost_by_min.keys()), list(cost_by_min.values()), "-o", ms=4)
    ax.axhline(slo["cost"], color="red", ls="--", lw=1, label=f"SLO ≤ ${slo['cost']:.2f}")
    ax.set_title(f"4. Cost: ${total_cost:.4f} total")
    ax.set_ylabel("USD"); ax.legend(fontsize=8); ax.tick_params(axis='x', rotation=45, labelsize=7)

    # 5) Tokens
    ax = axs[1, 1]
    ax.bar(["input", "output"], [tok_in, tok_out])
    ax.set_title(f"5. Tokens in={tok_in:,} out={tok_out:,}")
    ax.set_ylabel("tokens"); ax.tick_params(axis='x', rotation=45, labelsize=7)

    # 6) Quality
    ax = axs[1, 2]
    if sent:
        ax.plot([datetime.fromisoformat(r["ts"]) for r in sent],
                [r.get("quality_score", 0) for r in sent], "o-", ms=4)
    ax.axhline(slo["quality"], color="red", ls="--", lw=1, label=f"SLO ≥ {slo['quality']}")
    ax.set_title(f"6. Quality mean={quality:.2f}")
    ax.set_ylabel("score"); ax.legend(fontsize=8); ax.tick_params(axis='x', rotation=45, labelsize=7)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    REPO_ROOT.joinpath("submission", "evidence").mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=120)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
