#!/usr/bin/env python3
"""Offline dry-run reader for EXPERIMENT-PLAN.json.

The plan's 32 entries can only be executed with a human present and a live
network. This tool never executes them: it validates the plan, lists the
entries and their status, and renders the exact command a run would use so the
human can copy one out. Anything that still needs a value is printed as a
``<placeholder>`` so a template can never be mistaken for a runnable command.

Usage:
    python scripts/dry_run_experiments.py --check
    python scripts/dry_run_experiments.py --list
    python scripts/dry_run_experiments.py --render V02
    python scripts/dry_run_experiments.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "envoy.experiment-plan/1"
STATUSES = {"not_run", "running", "passed", "failed", "blocked", "skipped"}
REQUIREMENTS = {
    "user_present",
    "network",
    "model_download",
    "browser_setup",
    "windows_host",
    "linux_host",
    "public_site",
    "docker",
    "auth",
    "network_or_local",
}
PLACEHOLDER = re.compile(r"<[^<>]+>")
RUNNABLE = re.compile(r"^(?:envoy|python3?|sh|bash|zsh|powershell|pwsh|uv|PATH=|HOME=|TMPDIR=|ENVOY_[A-Z0-9_]+=)")


class PlanError(Exception):
    """Raised when the plan itself is unusable; message names every problem."""


def load_plan(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlanError(f"{path}: top level is not an object")
    return data


def entries(plan: dict) -> list[dict]:
    axes = plan.get("axes")
    if not isinstance(axes, dict) or not axes:
        raise PlanError("axes is missing or empty")
    rows: list[dict] = []
    for axis, block in axes.items():
        if not isinstance(block, dict) or not isinstance(block.get("entries"), list):
            raise PlanError(f"axis {axis!r} has no entries list")
        for entry in block["entries"]:
            if not isinstance(entry, dict):
                raise PlanError(f"axis {axis!r} has a non-object entry")
            rows.append(entry)
    return rows


def validate(plan: dict) -> list[str]:
    problems: list[str] = []
    if plan.get("schema") != SCHEMA:
        problems.append(f"schema is {plan.get('schema')!r}, expected {SCHEMA!r}")
    if not plan.get("version"):
        problems.append("version is missing")
    if plan.get("status") not in STATUSES:
        problems.append(f"plan status {plan.get('status')!r} is not in {sorted(STATUSES)}")

    seen: set[str] = set()
    try:
        rows = entries(plan)
    except PlanError as exc:
        return problems + [str(exc)]
    for entry in rows:
        entry_id = entry.get("id")
        if not entry_id:
            problems.append("an entry has no id")
            continue
        if entry_id in seen:
            problems.append(f"duplicate entry id {entry_id!r}")
        seen.add(entry_id)
        if entry.get("status") not in STATUSES:
            problems.append(f"{entry_id}: status {entry.get('status')!r} is not in {sorted(STATUSES)}")
        for field in ("command", "expected"):
            if not entry.get(field):
                problems.append(f"{entry_id}: {field} is empty")
        command = entry.get("command")
        if command and not RUNNABLE.match(str(command)):
            problems.append(
                f"{entry_id}: command must start with a runnable program "
                f"(envoy/sh/bash/powershell/uv or a VAR=value prefix), got {str(command)[:40]!r}"
            )
        requires = entry.get("requires")
        if not isinstance(requires, str) or not requires.strip():
            problems.append(f"{entry_id}: requires is empty")
        else:
            unknown = {
                token.strip() for token in requires.split(",") if token.strip()
            } - REQUIREMENTS
            if unknown:
                problems.append(f"{entry_id}: unknown requirement(s) {sorted(unknown)}")
        if entry.get("axis") != entry.get("axis", "").strip():
            problems.append(f"{entry_id}: axis has stray whitespace")

    holdout = plan.get("holdout")
    if not isinstance(holdout, dict):
        problems.append("holdout is missing")
    else:
        if holdout.get("status") not in {"pending_sample", "frozen"}:
            problems.append(f"holdout status {holdout.get('status')!r} is not pending_sample/frozen")
        if not isinstance(holdout.get("size"), int) or holdout["size"] < 1:
            problems.append("holdout size must be a positive integer")
        if holdout.get("status") == "frozen":
            for field in ("sha256", "rows"):
                if not holdout.get(field):
                    problems.append(f"frozen holdout is missing {field}")
        dependent = holdout.get("dependent_entries")
        if dependent is not None:
            if not isinstance(dependent, list):
                problems.append("holdout dependent_entries must be a list of entry ids")
            else:
                known = {entry.get("id") for entry in rows}
                for entry_id in dependent:
                    if entry_id not in known:
                        problems.append(f"holdout dependent entry {entry_id!r} is not in the plan")
    return problems


def summary(rows: list[dict]) -> dict[str, int]:
    counts = {status: 0 for status in sorted(STATUSES)}
    for entry in rows:
        counts[entry.get("status", "not_run")] = counts.get(entry.get("status", "not_run"), 0) + 1
    return counts


def render(plan: dict, entry_id: str) -> str:
    for entry in entries(plan):
        if entry.get("id") != entry_id:
            continue
        lines = [
            f"{entry['id']} [{entry.get('axis')}] {entry.get('title', '')}",
            f"  status:   {entry.get('status')}",
            f"  requires: {entry.get('requires')}",
            f"  command:  {entry['command']}",
        ]
        placeholders = sorted(set(PLACEHOLDER.findall(str(entry["command"]))))
        if placeholders:
            lines.append(f"  fill in:  {', '.join(placeholders)}")
        lines.append(f"  expect:   {entry.get('expected')}")
        if entry.get("notes"):
            lines.append(f"  notes:    {entry['notes']}")
        lines.append("  (dry run: nothing was executed)")
        return "\n".join(lines)
    raise PlanError(f"no entry with id {entry_id!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and dry-run the Envoy experiment plan.")
    parser.add_argument("--plan", type=Path, default=Path("EXPERIMENT-PLAN.json"))
    parser.add_argument("--check", action="store_true", help="validate the plan and print counts")
    parser.add_argument("--list", action="store_true", help="one line per entry")
    parser.add_argument("--render", metavar="ID", help="print the command for one entry")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
        problems = validate(plan)
        rows = entries(plan)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if problems and not (args.list or args.render):
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    if args.render:
        if problems:
            for problem in problems:
                print(f"FAIL {problem}", file=sys.stderr)
            return 1
        try:
            print(render(plan, args.render))
        except PlanError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.list:
        if problems:
            for problem in problems:
                print(f"FAIL {problem}", file=sys.stderr)
            return 1
        for entry in rows:
            print(f"{entry['id']:<5} {entry.get('status', '?'):<9} {entry.get('axis', ''):<12} {entry.get('title', '')}")
        return 0
    counts = summary(rows)
    if args.json:
        print(json.dumps({"entries": len(rows), "by_status": counts, "problems": problems}, indent=2))
    else:
        detail = ", ".join(f"{status} {count}" for status, count in counts.items() if count)
        print(f"plan ok: {len(rows)} entries ({detail}); holdout {plan.get('holdout', {}).get('status')}")
        print("dry run only: no entry was executed")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
