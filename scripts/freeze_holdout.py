#!/usr/bin/env python3
"""Freeze the holdout sample for the experiment matrix without touching the web.

The holdout must be drawn once, recorded, and never re-drawn after results are
seen. Sampling itself needs a live network (candidate URLs come from a real
search/site pass), so this script only does the deterministic, offline half:
given a candidate list collected during the human-present run, it sorts,
de-duplicates, takes the first ``--size`` rows with the declared seed, records a
sha256 of the frozen rows and the row count, and refuses to overwrite an
existing frozen file unless ``--force`` is passed.

Usage:
    python scripts/freeze_holdout.py --input candidates.txt --size 12 \
        --id H12-2026-10-28 --out holdout.json
    python scripts/freeze_holdout.py --check holdout.json
    python scripts/freeze_holdout.py --input candidates.txt --out holdout.json \
        --apply-plan EXPERIMENT-PLAN.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "envoy.holdout/1"
URL = re.compile(r"^https?://[^\s]+$")


class FrozenFileError(Exception):
    pass


def read_candidates(path: Path) -> list[str]:
    """Read one URL per line, ignoring blanks and ``#`` comments."""
    rows: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not URL.match(line):
            raise FrozenFileError(f"not an http(s) URL: {line!r}")
        rows.append(line)
    if not rows:
        raise FrozenFileError(f"no candidate URLs in {path}")
    return rows


def frozen_rows(candidates: list[str], *, size: int, seed: str) -> list[str]:
    unique = sorted(set(candidates))
    if size > len(unique):
        raise FrozenFileError(
            f"asked for {size} rows but only {len(unique)} distinct candidates were supplied"
        )
    # Sorting before sampling makes the result independent of input order and
    # reproducible from the candidate set plus the seed alone.
    picked = random.Random(seed).sample(unique, size)
    return sorted(picked)


def digest(rows: list[str]) -> str:
    payload = "\n".join(rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build(rows: list[str], *, holdout_id: str, seed: str, size: int) -> dict:
    return {
        "schema": SCHEMA,
        "id": holdout_id,
        "status": "frozen",
        "size": size,
        "seed": seed,
        "sha256": digest(rows),
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
    }


def check(path: Path) -> list[str]:
    """Return the problems found in an existing frozen file (empty means ok)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read {path}: {exc}"]
    problems: list[str] = []
    if data.get("schema") != SCHEMA:
        problems.append(f"schema is {data.get('schema')!r}, expected {SCHEMA!r}")
    rows = data.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
        return problems + ["rows is not a list of strings"]
    if data.get("size") != len(rows):
        problems.append(f"size {data.get('size')!r} != {len(rows)} rows")
    if data.get("status") != "frozen":
        problems.append(f"status is {data.get('status')!r}, expected 'frozen'")
    if data.get("sha256") != digest(rows):
        problems.append("sha256 does not match the recorded rows")
    duplicates = sorted({row for row in rows if rows.count(row) > 1})
    if duplicates:
        problems.append(f"duplicate rows: {', '.join(duplicates)}")
    bad = [row for row in rows if not URL.match(row)]
    if bad:
        problems.append(f"non-URL rows: {', '.join(bad)}")
    return problems


def apply_plan(plan_path: Path, data: dict, *, force: bool = False) -> None:
    """Record a frozen holdout in the experiment plan, keeping its prose."""
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrozenFileError(f"cannot read {plan_path}: {exc}") from exc
    holdout = plan.get("holdout")
    if not isinstance(holdout, dict):
        raise FrozenFileError(f"{plan_path} has no holdout block")
    if holdout.get("status") == "frozen" and not force:
        raise FrozenFileError(
            f"{plan_path} already records a frozen holdout "
            f"({str(holdout.get('sha256'))[:12]}); pass --force to replace it"
        )
    # Freezing a different count or id than the plan was written for would
    # quietly move the goalposts for the entries waiting on this holdout, so
    # the mismatch is refused unless the operator says --force.
    if not force:
        planned_size = holdout.get("size")
        if isinstance(planned_size, int) and planned_size != data["size"]:
            raise FrozenFileError(
                f"{plan_path} plans {planned_size} rows but the frozen file has "
                f"{data['size']}; pass --force to change the plan's holdout size"
            )
        planned_id = holdout.get("id")
        if planned_id and planned_id != data["id"]:
            raise FrozenFileError(
                f"{plan_path} is written for holdout {planned_id} but the frozen file is "
                f"{data['id']}; pass --force to change the plan's holdout id"
            )
    updated = dict(holdout)
    updated.update(
        {
            "id": data["id"],
            "status": "frozen",
            "size": data["size"],
            "sha256": data["sha256"],
            "rows": data["rows"],
        }
    )
    updated.pop("frozen_at", None)
    plan["holdout"] = updated
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def plan_mismatches(plan: dict, data: dict) -> list[str]:
    """Return how the plan's recorded holdout disagrees with the frozen file."""
    holdout = plan.get("holdout")
    if not isinstance(holdout, dict):
        return ["the plan has no holdout block"]
    problems: list[str] = []
    if holdout.get("id") != data.get("id"):
        problems.append(
            f"plan holdout id is {holdout.get('id')!r}, frozen file is {data.get('id')!r}"
        )
    if holdout.get("status") != "frozen":
        problems.append(f"plan holdout status is {holdout.get('status')!r}, expected 'frozen'")
    if holdout.get("sha256") != data.get("sha256"):
        problems.append("plan holdout sha256 does not match the frozen file")
    if holdout.get("rows") != data.get("rows"):
        problems.append("plan holdout rows do not match the frozen file")
    if holdout.get("size") != data.get("size"):
        problems.append("plan holdout size does not match the frozen file")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify an Envoy holdout sample.")
    parser.add_argument("--input", type=Path, help="candidate URL list, one per line")
    parser.add_argument("--out", type=Path, help="where to write the frozen JSON")
    parser.add_argument("--id", default="H12", help="holdout id recorded in the file")
    parser.add_argument("--size", type=int, default=12, help="rows to freeze (default 12)")
    parser.add_argument("--seed", default="envoy-holdout", help="sampling seed")
    parser.add_argument("--force", action="store_true", help="overwrite an existing frozen file")
    parser.add_argument("--check", type=Path, help="verify an existing frozen file")
    parser.add_argument("--plan", type=Path, help="with --check: require the plan to match the file")
    parser.add_argument(
        "--apply-plan", type=Path, help="record the frozen rows in an EXPERIMENT-PLAN.json"
    )
    args = parser.parse_args(argv)

    if args.check:
        problems = check(args.check)
        if problems:
            for problem in problems:
                print(f"FAIL {problem}")
            return 1
        data = json.loads(args.check.read_text(encoding="utf-8"))
        if args.plan:
            try:
                plan = json.loads(args.plan.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"FAIL cannot read {args.plan}: {exc}")
                return 1
            blocked = plan_mismatches(plan, data)
            if blocked:
                for problem in blocked:
                    print(f"FAIL {problem}")
                return 1
        print(f"holdout ok: {data.get('id')} ({data.get('size')} rows, {data.get('sha256')[:12]})")
        return 0

    if not args.input or not args.out:
        parser.error("either --check PATH or both --input and --out are required")
    if args.size < 1:
        parser.error("--size must be positive")
    if args.out.exists() and not args.force:
        print(f"{args.out} already exists; pass --force to re-freeze", file=sys.stderr)
        return 1
    try:
        rows = frozen_rows(read_candidates(args.input), size=args.size, seed=args.seed)
    except (OSError, FrozenFileError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    data = build(rows, holdout_id=args.id, seed=args.seed, size=args.size)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"froze {len(rows)} rows -> {args.out} (sha256 {data['sha256'][:12]})")
    if args.apply_plan:
        try:
            apply_plan(args.apply_plan, data, force=args.force)
        except FrozenFileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"recorded the frozen rows in {args.apply_plan} (status frozen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
