#!/usr/bin/env python3
"""Record one experiment result into EXPERIMENT-PLAN.json.

The plan's entries start as ``not_run``. After a human-present run, the result
has to land somewhere traceable: this tool writes ``status`` plus ``evidence``
and ``observed_at`` for exactly one entry, refuses a result without evidence,
refuses to silently un-record a finished entry, and keeps the plan-level status
in sync with its entries.

It never executes an experiment and never guesses a value: everything written
comes from the command line.

Usage:
    python scripts/record_experiment.py --id V02 --status passed \\
        --evidence "envoy read <url> --budget-json ... -> partial 412/600" --write
    python scripts/record_experiment.py --id V02 --status passed --evidence "..."   # dry
    python scripts/record_experiment.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dry_run_experiments import (  # noqa: E402
    STATUSES,
    PlanError,
    entries,
    load_plan,
    validate,
)

RECORDED = {"passed", "failed", "blocked", "skipped"}
TERMINAL = {"passed", "failed", "blocked", "skipped"}


class RecordError(Exception):
    """Raised when a result cannot be recorded; message says why."""


def find_entry(plan: dict, entry_id: str) -> dict:
    for entry in entries(plan):
        if entry.get("id") == entry_id:
            return entry
    raise RecordError(f"no entry with id {entry_id!r}")


def apply_result(
    plan: dict,
    entry_id: str,
    status: str,
    evidence: str,
    observed: dict | None = None,
    force: bool = False,
    now: str | None = None,
) -> dict:
    if status not in STATUSES:
        raise RecordError(f"status {status!r} is not in {sorted(STATUSES)}")
    entry = find_entry(plan, entry_id)
    previous = entry.get("status")
    if previous in TERMINAL and status not in TERMINAL and not force:
        raise RecordError(
            f"{entry_id} is already {previous}; recording {status} would un-record a "
            f"finished experiment (pass --force only if the earlier record was wrong)"
        )
    if status in RECORDED:
        text = (evidence or "").strip()
        if len(text) < 8:
            raise RecordError(f"{status} needs an evidence line that names what was observed")
        entry["evidence"] = text
    if observed is not None:
        entry["observed"] = observed
    entry["status"] = status
    if status in RECORDED:
        entry["observed_at"] = now or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    plan["status"] = aggregate(entries(plan))
    return entry


def aggregate(rows: list[dict]) -> str:
    statuses = [row.get("status", "not_run") for row in rows]
    if not statuses or all(status == "not_run" for status in statuses):
        return "not_run"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status in {"failed", "blocked"} for status in statuses):
        return "failed"
    if all(status in {"passed", "skipped"} for status in statuses):
        return "passed"
    return "running"


def write_plan(path: Path, plan: dict) -> None:
    text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record one experiment result into the plan.")
    parser.add_argument("--plan", type=Path, default=Path("EXPERIMENT-PLAN.json"))
    parser.add_argument("--id", help="entry id, e.g. V02")
    parser.add_argument("--status", help=f"one of {sorted(STATUSES)}")
    parser.add_argument("--evidence", default="", help="what was observed; required for recorded results")
    parser.add_argument("--observed", help="JSON object with the numbers that were read")
    parser.add_argument("--write", action="store_true", help="apply; without it nothing changes on disk")
    parser.add_argument("--force", action="store_true", help="allow changing an entry that is already finished")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
        problems = validate(plan)
        if problems:
            raise RecordError("plan is invalid: " + "; ".join(problems))
        if not args.id and not args.json:
            raise RecordError("give --id and --status, or --json to read the plan statuses")
    except (PlanError, RecordError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1

    if not args.id:
        rows = entries(plan)
        counts = {status: 0 for status in sorted(STATUSES)}
        for row in rows:
            counts[row.get("status", "not_run")] = counts.get(row.get("status", "not_run"), 0) + 1
        print(
            json.dumps(
                {
                    "plan_status": plan.get("status"),
                    "entries": len(rows),
                    "by_status": counts,
                    "holdout": plan.get("holdout", {}).get("status"),
                },
                indent=2,
            )
            if args.json
            else f"plan {plan.get('status')}: {len(rows)} entries; "
            + ", ".join(f"{status} {count}" for status, count in counts.items() if count)
        )
        return 0

    if not args.status:
        print("FAIL --status is required with --id", file=sys.stderr)
        return 1
    observed: dict | None = None
    if args.observed:
        try:
            parsed = json.loads(args.observed)
        except json.JSONDecodeError as exc:
            print(f"FAIL --observed is not JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(parsed, dict):
            print("FAIL --observed must be a JSON object", file=sys.stderr)
            return 1
        observed = parsed
    try:
        entry = apply_result(
            plan,
            args.id,
            args.status,
            args.evidence,
            observed=observed,
            force=args.force,
        )
    except RecordError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1

    line = f"{args.id} -> {entry['status']}"
    if args.write:
        write_plan(args.plan, plan)
        print(f"recorded {line} ({args.plan}, plan status {plan['status']})")
    else:
        print(f"dry run: would record {line} in {args.plan} (nothing written; add --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
