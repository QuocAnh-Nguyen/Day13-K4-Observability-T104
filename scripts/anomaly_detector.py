from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio

LOG_PATH = Path("data/logs.jsonl")
SLO_PATH = Path("config/slo.yaml")

PII_DETECTORS = {
    "email": re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    "phone_vn": re.compile(r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)"),
    "cccd": re.compile(r"\b\d{12}\b"),
    "credit_card": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    "passport_vn": re.compile(r"\b[A-Z]\d{7,8}\b"),
}


def load_slo() -> dict:
    cfg = yaml.safe_load(SLO_PATH.read_text(encoding="utf-8"))["slis"]
    return {
        "latency_p95_ms": cfg["latency_p95_ms"]["objective"],
        "error_rate_pct": cfg["error_rate_pct"]["objective"],
        "quality": cfg["quality_score_avg"]["objective"],
    }


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


def detect(records: list[dict], latency_p95: int, error_rate_max: float) -> dict:
    anomalies: dict[str, list] = {"pii_leaks": [], "latency_breaches": []}

    for rec in records:
        raw = json.dumps(rec, ensure_ascii=False)
        for name, pattern in PII_DETECTORS.items():
            if pattern.search(raw):
                anomalies["pii_leaks"].append(
                    {"correlation_id": rec.get("correlation_id"), "event": rec.get("event"), "type": name}
                )
                break

    for rec in records:
        if rec.get("event") == "response_sent":
            latency = rec.get("latency_ms") or 0
            if latency > latency_p95:
                anomalies["latency_breaches"].append(
                    {
                        "correlation_id": rec.get("correlation_id"),
                        "latency_ms": latency,
                        "threshold": latency_p95,
                    }
                )

    return anomalies


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Phát hiện anomaly từ data/logs.jsonl: PII leak, latency vượt SLO, error rate."
    )
    parser.add_argument("--log-path", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--output", type=Path, default=None,
                        help="Ghi báo cáo JSON vào file (vd submission/evidence/anomaly_report.json)")
    args = parser.parse_args()

    global LOG_PATH
    LOG_PATH = args.log_path

    records = load_logs()
    if not records:
        print("No logs to analyze")
        return 2

    slo = load_slo()
    report = detect(records, slo["latency_p95_ms"], slo["error_rate_pct"])

    total = len(records)
    recv = sum(1 for r in records if r.get("event") == "request_received")
    failed = sum(1 for r in records if r.get("event") == "request_failed")
    error_rate = (failed / recv * 100) if recv else 0.0
    sent = [r for r in records if r.get("event") == "response_sent"]
    p95 = sorted((r.get("latency_ms", 0) for r in sent))[int(0.95 * max(len(sent) - 1, 0))] if sent else 0

    summary = {
        "records": total,
        "pii_leaks": len(report["pii_leaks"]),
        "latency_breaches": len(report["latency_breaches"]),
        "error_rate_pct": round(error_rate, 2),
        "error_rate_slo": slo["error_rate_pct"],
        "latency_p95_ms": p95,
        "latency_slo_ms": slo["latency_p95_ms"],
        "quality_slo": slo["quality"],
    }

    print("--- Anomaly Detector ---")
    print(f"source={LOG_PATH}  records={total}  pii_leaks={summary['pii_leaks']}  "
          f"latency_breaches={summary['latency_breaches']}  error_rate={summary['error_rate_pct']}% "
          f"(SLO {slo['error_rate_pct']}%)  p95={p95}ms (SLO {slo['latency_p95_ms']}ms)")

    healthy = True
    if report["pii_leaks"]:
        healthy = False
        print("  [!] PII LEAK detected:")
        for leak in report["pii_leaks"][:10]:
            print(f"      corr={leak['correlation_id']} event={leak['event']} type={leak['type']}")
    if report["latency_breaches"]:
        healthy = False
        print("  [!] LATENCY BREACH (SLO exceeded):")
        for b in report["latency_breaches"][:10]:
            print(f"      corr={b['correlation_id']} latency={b['latency_ms']}ms > {b['threshold']}ms")

    payload = {"healthy": healthy, "summary": summary, "anomalies": report}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")

    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
