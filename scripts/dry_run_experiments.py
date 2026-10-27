#!/usr/bin/env python3
"""Offline dry-run reader for EXPERIMENT-PLAN.json.

The plan's entries can only be executed with a human present and a live
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
FLAG = re.compile(r"(?<![\w-])--([a-z][a-z0-9-]*)")
PLACEHOLDER = re.compile(r"<[^<>]+>")
RUNNABLE = re.compile(r"^(?:envoy|python3?|sh|bash|zsh|powershell|pwsh|uv|PATH=|HOME=|TMPDIR=|ENVOY_[A-Z0-9_]+=)")
# Subcommands and flags envoy actually accepts, mirroring
# packages/envoy-cli/src/envoy_cli/cli.py. A plan command naming a flag the CLI
# never had reads like a runnable measurement but dies in argparse the moment a
# human copies it out, so the dry run refuses it instead.
CLI_SURFACE = {
    "read": {"--request-json"},
    "read-local-video": {"--request-json"},
    "observe-visual": {"--request-json"},
    "expand": {"--request-json"},
    "replay": {"--request-json"},
    "search": {"--request-json"},
    "collection-checkpoint": {"--request-json"},
    "discover": {"--cursor", "--limit", "--max-work-seconds", "--url"},
    "collect": {
        "--collection-id",
        "--goal",
        "--json",
        "--limit",
        "--list",
        "--max-work-seconds",
        "--out",
        "--retry-blocked",
        "--retry-reason",
        "--run-id",
        "--url",
    },
    "doctor": {"--json", "--verify-asr"},
    "browser": {"--help"},
    "asr": {"--help"},
    "hook": {"--help"},
    "ocr": {"--help"},
    "cache": {"--help"},
    "auth": {"--help"},
    "mcp": {"--toolset"},
}
ENVOY_CALL = re.compile(r"(?:^|[\s'\"])envoy\s+([A-Za-z0-9-]+)([^|;]*)")


FROZEN_TOOL_SURFACE = {
    "--input",
    "--out",
    "--id",
    "--size",
    "--seed",
    "--force",
    "--check",
    "--plan",
    "--apply-plan",
}
FROZEN_TOOL_FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9-]+")


def holdout_problems(plan):
    """Name every freeze/verify command that would die in argparse when pasted.

    The holdout commands are the two steps a human runs first when they next
    have the machine to themselves, so an unknown flag there costs the whole
    session rather than one measurement.
    """
    holdout = plan.get("holdout")
    if not isinstance(holdout, dict):
        return []
    problems = []
    for field in ("freeze_command", "verify_command"):
        command = str(holdout.get(field, "")).strip()
        if not command:
            continue
        if "freeze_holdout.py" not in command:
            problems.append(f"holdout {field} does not run freeze_holdout.py")
        for flag in FROZEN_TOOL_FLAG.findall(command):
            if flag not in FROZEN_TOOL_SURFACE:
                problems.append(
                    f"holdout {field} names unknown freeze_holdout.py flag {flag} "
                    f"(known: {', '.join(sorted(FROZEN_TOOL_SURFACE))})"
                )
    return problems


def cli_problems(entry_id, command):
    """Name every envoy subcommand/flag and unquoted placeholder a command gets wrong."""
    problems = []
    for match in ENVOY_CALL.finditer(command):
        subcommand, rest = match.group(1), match.group(2)
        flags = CLI_SURFACE.get(subcommand)
        if flags is None:
            problems.append(
                f"{entry_id}: envoy {subcommand} is not a CLI subcommand "
                f"(known: {', '.join(sorted(CLI_SURFACE))})"
            )
            continue
        for token in rest.split():
            if not token.startswith("--"):
                continue
            flag = token.split("=", 1)[0].rstrip("'\"")
            if flag not in flags:
                problems.append(
                    f"{entry_id}: envoy {subcommand} has no {flag} flag "
                    f"(known: {', '.join(sorted(flags))})"
                )
    for match in PLACEHOLDER.finditer(command):
        before = command[match.start() - 1] if match.start() else ""
        if before not in "\"'":
            problems.append(
                f"{entry_id}: placeholder {match.group(0)} must be quoted so the shell "
                "does not read it as a redirect"
            )
    return problems


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
    problems.extend(dry_run_block_problems(plan))

    for axis, block in plan["axes"].items():
        if not isinstance(block, dict):
            continue
        count = block.get("count")
        aligned = block.get("entries")
        if count is not None and isinstance(aligned, list) and count != len(aligned):
            problems.append(
                f"axis {axis!r}: count says {count} but it has {len(aligned)} entries"
            )

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
        if command:
            problems.extend(cli_problems(str(entry_id), str(command)))
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
                listed = set(dependent)
                placeholders = {
                    entry.get("id")
                    for entry in rows
                    if "<public-" in str(entry.get("command", ""))
                }
                for entry_id in sorted(placeholders - listed):
                    problems.append(
                        f"{entry_id}: command reads a public URL but is not in "
                        "holdout dependent_entries"
                    )
                for entry_id in sorted(listed - placeholders):
                    problems.append(
                        f"{entry_id}: is a holdout dependent entry but its command "
                        "has no <public-*> placeholder"
                    )
        problems.extend(holdout_problems(plan))
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and dry-run the Envoy experiment plan.")
    parser.add_argument("--plan", type=Path, default=Path("EXPERIMENT-PLAN.json"))
    parser.add_argument("--check", action="store_true", help="validate the plan and print counts")
    parser.add_argument("--list", action="store_true", help="one line per entry")
    parser.add_argument("--render", metavar="ID", help="print the command for one entry")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


TOOL_FLAGS = frozenset(
    option for action in _parser()._actions for option in action.option_strings
)


def dry_run_block_problems(plan: dict) -> list[str]:
    """The plan hands the human its own commands; they must run this tool."""
    block = plan.get("dry_run")
    if not isinstance(block, dict):
        return []
    problems: list[str] = []
    for field in ("check", "list", "render"):
        command = block.get(field)
        if not isinstance(command, str) or "dry_run_experiments.py" not in command:
            continue
        for flag in FLAG.findall(command):
            if f"--{flag}" not in TOOL_FLAGS:
                known = ", ".join(sorted(TOOL_FLAGS))
                problems.append(
                    f"dry run {field} names unknown dry_run_experiments.py flag "
                    f"--{flag} (known: {known})"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
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
