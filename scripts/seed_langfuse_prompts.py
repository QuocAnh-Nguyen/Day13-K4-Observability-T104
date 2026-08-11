from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from app.audit import audit

from langfuse import get_client


V1_PROMPT = (
    "You are a careful AI assistant for the Day 13 observability lab.\n"
    "Feature: {{feature}}\n"
    "Context docs:\n{{docs}}\n"
    "User question: {{message}}\n"
    "Answer concisely. Use the docs when available."
)

V2_PROMPT = (
    "You are a careful AI assistant for the Day 13 observability lab.\n"
    "Feature: {{feature}}\n"
    "Retrieved evidence:\n{{docs}}\n"
    "Question to answer: {{message}}\n"
    "Reply in one short paragraph. Cite the relevant doc if any."
)


def _ensure_keys() -> None:
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        print("ERROR: LANGFUSE_PUBLIC_KEY/SECRET not set. Cannot create prompts.")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Langfuse with day13-chat v1/v2 prompts")
    parser.add_argument("--name", default="day13-chat")
    parser.add_argument("--rollback", action="store_true",
                        help="Move the production label from v2 back to v1")
    args = parser.parse_args()

    _ensure_keys()
    client = get_client()

    if args.rollback:
        candidate = client.get_prompt(args.name, label="candidate", type="text")
        promoted = client.update_prompt(
            name=args.name,
            version=candidate.version,
            new_labels=["production"],
        )
        client.update_prompt(
            name=args.name,
            version=1,
            new_labels=["baseline"],
        )
        current = client.get_prompt(args.name, label="production", type="text")
        audit(
            "prompt_label_change",
            prompt_name=args.name,
            production_label_moved_to=f"v{current.version}",
            from_version=candidate.version,
            action="rollback",
        )
        print(f"Rollback applied: production -> v{current.version} (was v{candidate.version})")
        return 0

    v1 = client.create_prompt(
        name=args.name,
        prompt=V1_PROMPT,
        labels=["production", "baseline"],
        tags=["lab", "k4"],
        type="text",
        commit_message="Day13 K4 baseline prompt v1 (production)",
    )
    print(f"Created v1 (labels=production,baseline) version={v1.version}")

    v2 = client.create_prompt(
        name=args.name,
        prompt=V2_PROMPT,
        labels=["candidate"],
        tags=["lab", "k4"],
        type="text",
        commit_message="Day13 K4 candidate prompt v2 (rollback drill)",
    )
    print(f"Created v2 (labels=candidate) version={v2.version}")

    current = client.get_prompt(args.name, label="production", type="text")
    candidate = client.get_prompt(args.name, label="candidate", type="text")
    print(f"production -> v{current.version}")
    print(f"candidate -> v{candidate.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
