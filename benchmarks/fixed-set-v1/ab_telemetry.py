#!/usr/bin/env python3
"""Extract per-turn tool telemetry from the isolated Claude Code transcripts.

Each profile keeps its own CLAUDE_CONFIG_DIR, so a session transcript is a plain
JSONL file under ``projects/<slug>/<session_uuid>.jsonl``. Resumed turns append
to the same file, so a turn is delimited by the user message carrying that turn's
prompt; ``run_ab.turn_prompt`` is the single source of truth for the text.

Usage:
    python3 ab_telemetry.py --root /tmp/AB2/s0 [--root /tmp/AB2/s1 ...] --out telemetry.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_ab  # noqa: E402


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def prompt_fingerprint(text: str) -> str:
    return " ".join(text.split())[:120]


def load_prompts(drafts: Path) -> dict[tuple[str, str], str]:
    prompts: dict[tuple[str, str], str] = {}
    for draft_file in sorted(drafts.glob("*.json")):
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
        package_id = draft.get("package_id") or draft_file.stem
        for role in ("initial", "followup-1", "followup-2"):
            prompts[(package_id, role)] = prompt_fingerprint(run_ab.turn_prompt(draft, role))
    return prompts


def transcript_for(profile: Path, session_uuid: str) -> Path | None:
    hits = glob.glob(str(profile / "claude-config" / "projects" / "*" / f"{session_uuid}.jsonl"))
    return Path(hits[0]) if hits else None


def read_entries(path: Path) -> list[dict]:
    entries: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def entry_text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def segment_entries(entries: list[dict], prompts: dict[str, str]) -> list[list[dict]]:
    starts: list[int] = []
    for index, entry in enumerate(entries):
        if entry.get("type") != "user":
            continue
        if prompt_fingerprint(entry_text(entry)) in prompts:
            starts.append(index)
    if not starts:
        return [entries]
    segments: list[list[dict]] = []
    for position, start in enumerate(starts):
        stop = starts[position + 1] if position + 1 < len(starts) else len(entries)
        segments.append(entries[start:stop])
    return segments


def target_of(tool_input: dict) -> str | None:
    for key in ("url", "source_url", "locator", "path", "file_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    command = tool_input.get("command")
    if isinstance(command, str):
        for token in command.split():
            if token.startswith("http://") or token.startswith("https://"):
                return token
    return None


def summarise(segment: list[dict]) -> dict:
    names: list[str] = []
    targets: list[str] = []
    first_result_ms: int | None = None
    start_stamp: datetime | None = None
    for entry in segment:
        stamp = parse_ts(entry.get("timestamp") or "")
        if stamp and start_stamp is None:
            start_stamp = stamp
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                names.append(block.get("name") or "")
                target = target_of(block.get("input") or {})
                if target:
                    targets.append(target)
            elif block.get("type") == "tool_result" and first_result_ms is None and stamp and start_stamp:
                first_result_ms = round((stamp - start_stamp).total_seconds() * 1000)
    return {
        "tool_calls": len(names),
        "tool_names": names,
        "envoy_calls": sum(
            1 for name in names if name.startswith("mcp__") and "envoy" in name
        ),
        "unique_targets": len(set(targets)),
        "repeated_targets": len(targets) - len(set(targets)),
        "first_evidence_ms": first_result_ms,
        "transcript_entries": len(segment),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--drafts", default=str(HERE / "task-drafts"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    prompts = load_prompts(Path(args.drafts))
    fingerprints_by_package: dict[str, set[str]] = {}
    for (package_id, _role), fingerprint in prompts.items():
        fingerprints_by_package.setdefault(package_id, set()).add(fingerprint)

    output: list[dict] = []
    for root in args.root:
        root_path = Path(root)
        log = root_path / "run-log.jsonl"
        if not log.is_file():
            continue
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault((row["package_id"], row["arm"]), []).append(row)
        for (package_id, arm), turns in grouped.items():
            profile = root_path / "profiles" / package_id / arm
            path = transcript_for(profile, turns[0]["session_uuid"])
            if not path:
                for row in turns:
                    output.append({**row, "telemetry_missing": True})
                continue
            segments = segment_entries(read_entries(path), fingerprints_by_package.get(package_id, set()))
            for row in turns:
                fingerprint = prompts.get((package_id, row["role"]))
                matched = None
                for segment in segments:
                    if fingerprint and prompt_fingerprint(entry_text(segment[0])) == fingerprint:
                        matched = segment
                        break
                if matched is None:
                    output.append({**row, "telemetry_missing": True})
                else:
                    output.append({**row, "telemetry_missing": False, **summarise(matched)})

    Path(args.out).write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in output) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "turns": len(output),
                "transcript_missing": sum(1 for item in output if item.get("telemetry_missing")),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
