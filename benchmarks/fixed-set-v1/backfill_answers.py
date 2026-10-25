#!/usr/bin/env python3
"""Recover answers the CLI exited 0 without printing.

A turn can end with `terminal_reason: completed` and an empty `result`: the
model stopped after a tool call instead of writing a final message. The
transcript still holds whatever it did say, so the last assistant text blocks
are the best available answer. Every recovered row is labelled so a reader can
tell a printed answer from a recovered one.

Usage: backfill_answers.py --shard /tmp/AB3/plan-s0-baseline [--apply]
Without --apply it only reports what it would change.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Lines the CLI or the transport injects in place of model output. Recovering
# one of these would record a non-answer as an answer.
STUB_PREFIXES = (
    "No response requested.",
    "Request timed out",
    "API Error",
    "Connection refused",
    "Invalid API key",
)


def transcript_text(path: Path) -> str:
    """Concatenate assistant text blocks in order; tool calls are ignored."""
    texts: list[str] = []
    for entry in load_jsonl(path):
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        role = (entry.get("message") or {}).get("role") or entry.get("type")
        if role not in (None, "assistant"):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if text and not text.startswith(STUB_PREFIXES):
                    texts.append(text)
    return "\n\n".join(texts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", action="append", required=True)
    parser.add_argument("--apply", action="store_true", help="rewrite answers.jsonl in place (keeps a .bak)")
    parser.add_argument("--out", type=Path, help="write the recovered rows here instead of editing in place")
    args = parser.parse_args()

    recovered: list[dict] = []
    written: list[dict] = []
    for root in args.shard:
        shard = Path(root)
        answers_path = shard / "answers.jsonl"
        rows = load_jsonl(answers_path)
        if not rows:
            continue
        changed = False
        for row in rows:
            if (row.get("answer") or "").strip():
                continue
            exit_code = int(row.get("exit_code") or 0)
            if exit_code not in (0, 124):
                continue
            uuid = row.get("session_uuid") or ""
            candidates = sorted(shard.glob(f"profiles/{row['package_id']}/{row['arm']}/claude-config/projects/*/{uuid}.jsonl"))
            if not candidates:
                continue
            text = transcript_text(candidates[0])
            if not text:
                continue
            row["answer"] = text
            # A turn killed at the wall-clock cap still left text in the
            # transcript; it is reported, and labelled, as partial evidence.
            row["answer_source"] = (
                "transcript_partial_capped" if exit_code == 124 else "transcript_last_assistant_text"
            )
            changed = True
            written.append(row)
            recovered.append(
                {
                    "shard": shard.name,
                    "package_id": row["package_id"],
                    "arm": row["arm"],
                    "role": row["role"],
                    "exit_code": exit_code,
                    "chars": len(text),
                }
            )
        if changed and args.apply:
            shutil.copy2(answers_path, answers_path.with_suffix(".jsonl.bak"))
            answers_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + "\n",
                encoding="utf-8",
            )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            for row in written:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {"recovered": len(recovered), "applied": bool(args.apply), "out": str(args.out) if args.out else None,
             "rows": recovered},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
