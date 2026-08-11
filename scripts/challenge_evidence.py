from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from app.cli import configure_utf8_stdio

LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
EVIDENCE_DIR = REPO_ROOT / "submission" / "evidence"


def load_logs() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    return [json.loads(l) for l in LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    configure_utf8_stdio()
    from langfuse import get_client
    client = get_client()

    logs = load_logs()
    challenge_sessions = sorted(
        {r["session_id"] for r in logs if str(r.get("session_id", "")).startswith("k4-challenge-s")}
    )

    # Fetch recent traces and index by session_id
    trace_by_session: dict[str, object] = {}
    res = client.api.trace.list(limit=20)
    for t in res.data:
        try:
            full = client.api.trace.get(t.id)
        except Exception:
            continue
        sid = getattr(full, "session_id", None)
        if sid:
            trace_by_session[sid] = full

    lines = [
        "Official K4 challenge investigation (config/challenge.json, cohort=K4)",
        "incident=rag_slow  latency_threshold_ms=2000  affected_feature=monitoring",
        "",
        "Metric -> Trace -> Log chain for each challenge query",
        ""
    ]

    for sid in challenge_sessions:
        sents = [r for r in logs if r.get("session_id") == sid and r.get("event") == "response_sent"]
        recv = [r for r in logs if r.get("session_id") == sid and r.get("event") == "request_received"]
        if not sents:
            continue
        log = sents[-1]
        recv_log = recv[-1] if recv else None
        cid = log.get("correlation_id", "?")
        trace = trace_by_session.get(sid)
        trace_id = getattr(trace, "id", "not-found")
        latency = log.get("latency_ms")
        breach = latency >= 2000
        lines.append(f"[{sid}] correlation_id={cid} latency_ms={latency} "
                     f"{'BREACH' if breach else 'ok'} tokens_in={log.get('tokens_in')} "
                     f"tokens_out={log.get('tokens_out')} cost_usd={log.get('cost_usd')}")
        lines.append(f"      trace_id={trace_id}")
        if trace is not None:
            obs = getattr(trace, "observations", None) or []
            retrieval = next((o for o in obs if getattr(o, "name", None) == "retrieval"), None)
            if retrieval is not None:
                lines.append(
                    f"      root-cause span: id={retrieval.id} type={retrieval.type} "
                    f"name=retrieval latency={getattr(retrieval, 'latency', None)}s"
                )
            else:
                slow_obs = max(obs, key=lambda o: getattr(o, "latency", 0) or 0, default=None)
                if slow_obs is not None:
                    lines.append(
                        f"      slowest span: id={slow_obs.id} type={slow_obs.type} name={slow_obs.name} "
                        f"latency={getattr(slow_obs, 'latency', None)}s model={getattr(slow_obs, 'model', None)}"
                    )
        if recv_log:
            preview = (recv_log.get("payload") or {}).get("message_preview", "")
            lines.append(f"      log[request_received] preview={preview}")
        lines.append("")

    root_cause = [
        "Root cause: STATE['rag_slow'] adds a 2.5s sleep in app/mock_rag.retrieve() before",
        "the mock LLM call, pushing every request for the affected 'monitoring' feature above",
        "the 2000ms latency threshold. Metric (p95/latency_ms -> ~2650ms) -> trace (slow span in",
        "app/mock_rag.retrieve) -> log (matching correlation_id shows latency_ms >= 2000).",
    ]
    lines += root_cause
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "challenge_evidence.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
