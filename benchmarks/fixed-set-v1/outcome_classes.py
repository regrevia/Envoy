#!/usr/bin/env python3
"""Classify what a turn actually gave the user, instead of what it mentioned.

Four outcomes per scored turn, one per turn:

  delivered      the answer carries the requested artifact
  needs_action   the answer stops and asks the user to log in, scan, authorize,
                 solve a captcha, or take over the browser
  cannot_see     the model states the linked material is unreadable / not there
  no_output      the turn produced no answer (timed out, interrupted, crash)

Counting is rule-based over the answer text, so the rules are printed with the
counts and every class can be re-checked. Nothing here scores quality.

Usage:
  python3 outcome_classes.py --out /tmp/AB3
  python3 outcome_classes.py --out /tmp/AB3 --examples 3
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

NEEDS_ACTION = re.compile(
    r"(请(你先)?(登录|扫码|授权)|需要(你)?(登录|扫码|授权|人工)|扫码登录|验证码|"
    r"浏览器(操作|接管|中打开)|captcha|please (log|sign) in|"
    r"requires? (log ?in|authentication|authorization)|you.{0,20}(log|sign) in)",
    re.I,
)

CANNOT_SEE = re.compile(
    r"(我(无法|不能|没法)(给出|完成|访问|打开|读取|观看|播放)|"
    r"cannot (produce|answer|access|open|read the|view|play)|"
    r"can't (produce|answer|access|open|read the|view|play)|"
    r"unable to (produce|answer|access|open|read|view|play)|"
    r"renders? (as )?client-side|client-side shell|"
    r"no (readable|accessible) content|看不到|读不到)",
    re.I,
)

# A refusal only counts when the turn did not also hand over the artifact: the
# same answer often reports "the page cannot be read by a naive fetch, so I read
# the raw HTML" and then delivers. Short answers, or a refusal in the opening
# paragraph, are the ones that end there.
REFUSAL_WINDOW = 400
REFUSAL_MAX_CHARS = 3000


ARMS = ("baseline", "envoy")


def _latest(pattern: str, out: Path) -> dict[tuple, dict]:
    latest: dict[tuple, dict] = {}
    for path in sorted(glob.glob(str(out / pattern))):
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            latest[(row["package_id"], row["arm"], row["role"])] = row
    return latest


def load(out: Path) -> list[dict]:
    """Join the recorded exit code onto the answer text.

    ``answers.jsonl`` only says error/not-error, which merges "the cap cut it"
    with "the process died". The run log keeps the exit code, so a turn killed
    at the timeout is counted as such and an infrastructure death is dropped
    from the denominator instead of being scored against the arm.
    """
    answers = _latest("plan-s*/answers.jsonl", out)
    exits = _latest("plan-s*/run-log.jsonl", out)
    rows = []
    for key, row in answers.items():
        log = exits.get(key, {})
        row["exit_code"] = log.get("exit_code")
        row["elapsed_seconds"] = log.get("elapsed_seconds")
        rows.append(row)
    return rows


def classify(row: dict) -> tuple[str, str]:
    text = (row.get("answer") or "").strip()
    code = row.get("exit_code")
    if code == 124:
        return "timed_out", ""
    if code not in (0, None):
        return "infra_failed", ""
    if len(text) < 40:
        return "timed_out" if code == 124 else "no_output", ""
    # Ask first: an answer that stops to ask for a login or a captcha is
    # actionable for the user, even when it also explains why it got stuck.
    match = NEEDS_ACTION.search(text)
    if match:
        return "needs_action", match.group(0)
    match = CANNOT_SEE.search(text)
    if match and (len(text) < REFUSAL_MAX_CHARS or match.start() < REFUSAL_WINDOW):
        return "cannot_see", match.group(0)
    return "delivered", ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="run root holding plan-s*/answers.jsonl")
    parser.add_argument("--examples", type=int, default=0, help="print N example answers per class")
    parser.add_argument("--json", action="store_true", help="emit machine-readable counts")
    args = parser.parse_args(argv)

    rows = load(args.out)
    # Infrastructure deaths (proxy restarting, machine asleep) say nothing about
    # the arm; they are printed, not scored.
    rows_scored = [row for row in rows if classify(row)[0] != "infra_failed"]
    rows = rows_scored
    counts = collections.Counter()
    for row in rows:
        counts[(row["arm"], classify(row)[0])] += 1

    classes = ("delivered", "needs_action", "cannot_see", "timed_out", "no_output", "infra_failed")
    result: dict[str, dict] = {}
    for arm in ARMS:
        total = sum(counts[(arm, name)] for name in classes)
        if not total:
            continue
        result[arm] = {
            "turns": total,
            **{name: counts[(arm, name)] for name in classes},
            "delivered_pct": round(100 * counts[(arm, "delivered")] / total, 1),
        }

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        print(f"turns: {len(rows)}")
        header = f"{'arm':9}" + "".join(f"{name:>14}" for name in classes)
        print(header)
        for arm, row in result.items():
            print(f"{arm:9}" + "".join(f"{row[name]:>14}" for name in classes) + f"   delivered {row['delivered_pct']}%")

    if args.examples:
        for name in classes[1:]:
            shown = 0
            for row in rows:
                label, hit = classify(row)
                if label != name or shown >= args.examples:
                    continue
                shown += 1
                print(f"\n[{name}] {row['arm']} {row['package_id']} {row['role']} rule={hit!r}")
                print((row.get("answer") or "")[:300].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
