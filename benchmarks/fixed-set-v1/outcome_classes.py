#!/usr/bin/env python3
"""Classify what a turn's answer text looks like, and say what is not verified.

The class this script used to call ``delivered`` ("the answer carries the
requested artifact") was renamed to ``suspect_complete``: it is a keyword rule
over the answer text, not a quality judgement. It does not check whether the
facts are right, whether citations point at the cited paragraph, or whether the
artifact could be reproduced. A turn is only counted as ``verified`` when a
human review file (the ``make_review.py`` skeleton, filled in) explicitly marks
that arm's answer as correct.

Classes per scored turn:

  suspect_complete  the answer text does not look timed out, errored, empty,
                    login-gated or refused; quality is still unverified
  needs_action      the answer stops and asks the user to log in, scan, authorize,
                    solve a captcha, or take over the browser
  cannot_see        the model states the linked material is unreadable / not there
  no_output         the turn produced no answer (timed out, interrupted, crash)
  timed_out         the wall-clock cap cut the turn (exit 124)
  infra_failed      process died for a reason unrelated to the arm (not scored)

Counting is rule-based over the answer text, so the rules are printed with the
counts and every class can be re-checked.

Usage:
  python3 outcome_classes.py --out /tmp/AB3
  python3 outcome_classes.py --out /tmp/AB3 --examples 3
  python3 outcome_classes.py --out /tmp/AB3 --review /tmp/AB3/report/review.json --json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

TURN_ROLES = ("initial", "followup-1", "followup-2")

NEEDS_ACTION = re.compile(
    r"(请(?:你)?(?:先)?(?:登录|扫码|授权)|需要(?:你)?(?:登录|扫码|授权|人工)|扫码登录|验证码|"
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

CLASSES = (
    "suspect_complete",
    "needs_action",
    "cannot_see",
    "timed_out",
    "no_output",
    "infra_failed",
)


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
    # Everything else only *looks* finished. The old label here was "delivered",
    # which implied a quality check this rule cannot make.
    return "suspect_complete", ""


def review_lookup(review_path: Path | None) -> dict[str, dict]:
    """task_id -> review row from the filled-in make_review.py skeleton."""
    if not review_path or not review_path.is_file():
        return {}
    data = json.loads(review_path.read_text(encoding="utf-8"))
    return {item["task_id"]: item for item in data.get("reviews", [])}


def _split_task_id(task_id: str) -> tuple[str, str] | None:
    for role in TURN_ROLES:
        suffix = f"-{role}"
        if task_id.endswith(suffix):
            return task_id[: -len(suffix)], role
    return None


def _verified(reviews: dict[str, dict], row: dict) -> bool:
    """True only when a human review marked this arm's answer correct.

    ``answer_correct`` / ``baseline_answer_correct`` are the two explicit
    verdicts the review worksheet asks for. Nothing in the answer text can set
    this flag.
    """
    review = reviews.get(f"{row['package_id']}-{row['role']}")
    if not review:
        return False
    field = "answer_correct" if row["arm"] == "envoy" else "baseline_answer_correct"
    return review.get(field) is True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="run root holding plan-s*/answers.jsonl")
    parser.add_argument("--examples", type=int, default=0, help="print N example answers per class")
    parser.add_argument("--json", action="store_true", help="emit machine-readable counts")
    parser.add_argument(
        "--review",
        type=Path,
        default=None,
        help="filled-in review skeleton (make_review.py). Without it, verified is 0.",
    )
    args = parser.parse_args(argv)

    rows = load(args.out)
    # Infrastructure deaths (proxy restarting, machine asleep) say nothing about
    # the arm; they are printed, not scored.
    rows = [row for row in rows if classify(row)[0] != "infra_failed"]
    reviews = review_lookup(args.review)
    counts = collections.Counter()
    verified = collections.Counter()
    for row in rows:
        label, _hit = classify(row)
        counts[(row["arm"], label)] += 1
        if label == "suspect_complete" and _verified(reviews, row):
            verified[row["arm"]] += 1

    result: dict[str, dict] = {}
    for arm in ARMS:
        total = sum(counts[(arm, name)] for name in CLASSES)
        if not total:
            continue
        row = {
            "turns": total,
            **{name: counts[(arm, name)] for name in CLASSES},
            "suspect_complete_pct": round(100 * counts[(arm, "suspect_complete")] / total, 1),
            "verified": verified[arm],
            "verified_pct": round(100 * verified[arm] / total, 1),
        }
        result[arm] = row

    note = (
        "suspect_complete is a text rule, not a quality check; verified counts "
        "only turns a filled-in review file marks answer_correct=true"
    )
    if args.json:
        json.dump(
            {
                "method": "rule-based over answer text",
                "claimable": False,
                "review_file": str(args.review) if args.review else None,
                "note": note,
                "arms": result,
            },
            sys.stdout,
            indent=2,
        )
        print()
    else:
        print(f"turns: {len(rows)}   (suspect_complete = 疑似完成，质量未验证)")
        header = f"{'arm':9}" + "".join(f"{name:>18}" for name in CLASSES) + f"{'verified':>10}"
        print(header)
        for arm, row in result.items():
            print(
                f"{arm:9}" + "".join(f"{row[name]:>18}" for name in CLASSES)
                + f"{row['verified']:>10}"
            )
        print("suspect_complete: " + ", ".join(
            f"{arm} {row['suspect_complete']}/{row['turns']} ({row['suspect_complete_pct']}%)"
            for arm, row in result.items()
        ))
        if not reviews:
            print("verified: 0 (no --review file given; keyword hits are not quality verification)")

    if args.examples:
        for name in CLASSES[1:]:
            shown = 0
            for row in rows:
                label, hit = classify(row)
                if label != name:
                    continue
                text = " ".join((row.get("answer") or "").split())[:220]
                print(f"\n[{name}] {row['package_id']}/{row['arm']}/{row['role']} hit={hit!r}\n{text}")
                shown += 1
                if shown >= args.examples:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
