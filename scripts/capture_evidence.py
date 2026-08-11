from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from app.cli import configure_utf8_stdio

EVIDENCE_DIR = REPO_ROOT / "submission" / "evidence"
LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
PROMPT_NAME = "day13-chat"


def _run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    ).stdout


def _write(name: str, content: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / name
    out.write_text(content, encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return out


def _load_logs() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def capture_validate_logs() -> None:
    out = _run([sys.executable, str(REPO_ROOT / "scripts" / "validate_logs.py")])
    _write("validate_logs.txt", out)


def capture_validate_dashboard() -> None:
    out = _run([sys.executable, str(REPO_ROOT / "scripts" / "validate_dashboard.py")])
    _write("validate_dashboard.txt", out)


def capture_trace_ids(client) -> None:
    res = client.api.trace.list(limit=10)
    lines = [f"trace_id={t.id}  name={getattr(t,'name',None)}  ts={getattr(t,'timestamp',None)}  tag=lab"
             for t in res.data]
    _write("trace_ids.txt", "\n".join(lines) + "\n")


def _clean_metadata(metadata) -> dict:
    if not isinstance(metadata, dict):
        return metadata
    cleaned = dict(metadata)
    cleaned.pop("resourceAttributes", None)
    cleaned.pop("scope", None)
    return cleaned


def capture_trace_waterfall(client, trace_id: str | None = None) -> None:
    if trace_id is None:
        res = client.api.trace.list(limit=1)
        if not res.data:
            print("no traces found")
            return
        trace_id = res.data[0].id
    tr = client.api.trace.get(trace_id)
    payload = {
        "trace_id": tr.id,
        "name": tr.name,
        "timestamp": str(getattr(tr, "timestamp", "")),
        "latency_s": getattr(tr, "latency", None),
        "total_cost_usd": getattr(tr, "total_cost", None),
        "user_id": getattr(tr, "user_id", None),
        "metadata": _clean_metadata(getattr(tr, "metadata", None)),
        "observations": [
            {
                "id": o.id,
                "type": o.type,
                "name": o.name,
                "parent_observation_id": getattr(o, "parent_observation_id", None),
                "start_time": str(getattr(o, "start_time", "")),
                "end_time": str(getattr(o, "end_time", "")),
                "latency_s": getattr(o, "latency", None),
                "model": getattr(o, "model", None),
                "prompt_name": getattr(o, "prompt_name", None),
                "prompt_version": getattr(o, "prompt_version", None),
                "usage": getattr(o, "usage", None),
                "metadata": _clean_metadata(getattr(o, "metadata", None)),
                "cost_details": getattr(o, "cost_details", None),
            }
            for o in (getattr(tr, "observations", None) or [])
        ],
    }
    _write("trace_waterfall.json", json.dumps(payload, indent=2, default=str) + "\n")


def _scan_traces(client, limit: int = 40) -> list[dict]:
    """Return one entry per trace that carries prompt metadata."""
    res = client.api.trace.list(limit=limit)
    out = []
    for t in res.data:
        try:
            full = client.api.trace.get(t.id)
        except Exception:
            continue
        for o in (getattr(full, "observations", None) or []):
            md = getattr(o, "metadata", None) or {}
            if "prompt_name" in md:
                out.append({
                    "trace_id": full.id,
                    "name": md["prompt_name"],
                    "label": md["prompt_label"],
                    "version": md["prompt_version"],
                    "user_id": getattr(full, "user_id", None),
                    "latency_s": getattr(full, "latency", None),
                })
                break
    return out


def capture_prompt_versions(client) -> None:
    prod = client.get_prompt(PROMPT_NAME, label="production", type="text")
    cand = client.get_prompt(PROMPT_NAME, label="candidate", type="text")
    lines = [
        "Prompt versions (Langfuse, name=%s)" % PROMPT_NAME,
        "",
        "production -> version %s" % prod.version,
        "candidate  -> version %s" % cand.version,
        "",
    ]

    traces = _scan_traces(client)
    prod_traces = [t for t in traces if t["label"] == "production"]
    cand_traces = [t for t in traces if t["label"] == "candidate"]

    lines.append("Trace chứng minh version/label khác nhau (PROMPT_VERSIONING.md):")
    for t in sorted(prod_traces, key=lambda x: x["trace_id"])[:3]:
        lines.append(
            "  [production] trace=%s name=%s label=%s version=%s" % (
                t["trace_id"], t["name"], t["label"], t["version"])
        )
    for t in sorted(cand_traces, key=lambda x: x["trace_id"])[:2]:
        lines.append(
            "  [candidate ] trace=%s name=%s label=%s version=%s" % (
                t["trace_id"], t["name"], t["label"], t["version"])
        )
    if not cand_traces:
        lines.append("  !!! CHƯA CÓ trace candidate v2 - chạy app với LANGFUSE_PROMPT_LABEL=candidate !!!")
    _write("prompt_versions.txt", "\n".join(lines) + "\n")


def capture_prompt_rollback(client) -> None:
    def prod_version() -> int:
        return client.get_prompt(PROMPT_NAME, label="production", type="text").version

    before = prod_version()

    cand = client.get_prompt(PROMPT_NAME, label="candidate", type="text")
    client.update_prompt(name=PROMPT_NAME, version=cand.version, new_labels=["production"])
    client.update_prompt(name=PROMPT_NAME, version=1, new_labels=["baseline"])
    after_switch = prod_version()

    client.update_prompt(name=PROMPT_NAME, version=1, new_labels=["production"])
    client.update_prompt(name=PROMPT_NAME, version=cand.version, new_labels=["candidate"])
    after_rollback = prod_version()

    content = "\n".join([
        "Prompt label rollback drill (name=%s, 'production' label)" % PROMPT_NAME,
        "",
        f"step 1 BEFORE          : production -> v{before}",
        f"step 2 promote v{cand.version} to production : production -> v{after_switch}",
        f"step 3 rollback to v1  : production -> v{after_rollback}",
        "",
        "Versions are immutable; only the 'production' label assignment moves,",
        "so a bad candidate can be rolled back instantly without a redeploy.",
        f"Final state: production=v{after_rollback}, candidate=v{cand.version}.",
    ])
    _write("prompt_rollback.txt", content + "\n")


def capture_pii_log() -> None:
    import re

    records = _load_logs()
    seen: set[str] = set()
    selected: list[dict] = []
    for r in reversed(records):
        if r.get("event") != "request_received":
            continue
        preview = (r.get("payload") or {}).get("message_preview", "")
        if not isinstance(preview, str):
            continue
        for pii_type in sorted(set(re.findall(r"REDACTED_[A-Z_]+", preview))):
            if pii_type not in seen:
                seen.add(pii_type)
                selected.append(r)
                break
        if len(seen) >= 3:
            break
    if selected:
        _write(
            "pii_log_line.jsonl",
            "\n".join(json.dumps(r, ensure_ascii=False) for r in selected) + "\n",
        )


def capture_correlation_id_logs() -> None:
    records = _load_logs()
    from collections import Counter
    counts = Counter(r.get("correlation_id", "") for r in records if r.get("service") == "api")
    cid = next((k for k, v in counts.items() if v >= 2 and k and k != "MISSING"), None)
    if not cid:
        print("no correlation id with >=2 log lines found; run load_test first")
        return
    matched = [r for r in records if r.get("correlation_id") == cid]
    _write("correlation_id_logs.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in matched) + "\n")


def capture_pii_redaction() -> None:
    from app.pii import scrub_text, summarize_text
    seeds = [
        "Contact my email student@vinuni.edu.vn",
        "My phone is 090 123 4567",
        "Passport B1234567 issued at the airport",
        "Verify credit card 4111 1111 1111 1111",
    ]
    lines = ["PII redaction proof (raw input -> scrubbed log value):", ""]
    for seed in seeds:
        lines.append(f"raw : {seed}")
        lines.append(f"log : {summarize_text(seed)}")
        lines.append("")
    _write("pii_redaction.txt", "\n".join(lines) + "\n")


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Capture Day 13 evidence files")
    parser.add_argument("--trace-id", default=None)
    parser.add_argument("--only", choices=[
        "validate", "traces", "waterfall", "prompts", "rollback", "correlation", "pii"
    ])
    args = parser.parse_args()

    from langfuse import get_client
    client = get_client()

    if args.only in (None, "validate", "traces", "waterfall", "prompts", "rollback"):
        if args.only in (None, "validate"):
            capture_validate_logs()
            capture_validate_dashboard()
        if args.only in (None, "traces"):
            capture_trace_ids(client)
        if args.only in (None, "waterfall"):
            capture_trace_waterfall(client, args.trace_id)
        if args.only in (None, "prompts"):
            capture_prompt_versions(client)
        if args.only in (None, "rollback"):
            capture_prompt_rollback(client)

    if args.only in (None, "correlation", "pii"):
        capture_correlation_id_logs()
        capture_pii_log()
        if args.only == "pii" or args.only is None:
            capture_pii_redaction()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
