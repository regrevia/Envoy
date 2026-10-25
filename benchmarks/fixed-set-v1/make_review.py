#!/usr/bin/env python3
"""Build the human/agent review worksheet for the A/B run.

`agent_product_eval.py` refuses to call token savings "eligible" until each task
carries a reviewed verdict, because keyword overlap is not correctness. This
script emits the material a reviewer needs — the question, the reference facts,
and what each arm actually answered — plus an empty `reviews` skeleton in the
exact shape `ab_eval_records.py --review` reads.

Two steps, deliberately separate so nothing can be auto-filled:

    python3 make_review.py --report /tmp/AB3/report --skeleton review-skeleton.json
    # a reviewer fills envoy_supported / baseline_supported with fact ids they
    # checked in the answers, then writes review.json
    python3 make_review.py --report /tmp/AB3/report --worksheet review.md

`task_id` is `<package_id>-<role>`, matching ab_eval_records.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_ab  # noqa: E402  (sibling module, needs the path above)


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--drafts", default=str(HERE / "task-drafts"))
    parser.add_argument("--skeleton", type=Path)
    parser.add_argument("--worksheet", type=Path)
    parser.add_argument("--snippet", type=int, default=1600)
    args = parser.parse_args()

    summary = json.loads((args.report / "summary.json").read_text(encoding="utf-8"))
    grades = {
        (row["package_id"], row["arm"], row["role"]): row
        for row in load_jsonl(args.report / "grade.jsonl")
    }
    answers = {
        (row["package_id"], row["arm"], row["role"]): row
        for row in load_jsonl(args.report / "answers-recovered.jsonl")
    }
    draft_dir = Path(args.drafts)
    drafts = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(draft_dir.glob("*.json"))
    }

    tasks: dict[tuple[str, str], dict[str, dict]] = {}
    for turn in summary["turns"]:
        tasks.setdefault((turn["package_id"], turn["role"]), {})[turn["arm"]] = turn

    reviews: list[dict] = []
    sections: list[str] = ["# 评分工作表", ""]
    for (package_id, role), arms in sorted(tasks.items()):
        if "baseline" not in arms or "envoy" not in arms:
            continue
        draft = drafts.get(package_id) or {}
        gold = {fact["fact_id"]: fact for fact in draft.get("reference_facts", [])}
        envoy_grade = grades.get((package_id, "envoy", role), {})
        base_grade = grades.get((package_id, "baseline", role), {})

        reviews.append(
            {
                "task_id": f"{package_id}-{role}",
                "envoy_supported": [],
                "baseline_supported": [],
                "answer_correct": None,
                "baseline_answer_correct": None,
                "citation_correct": None,
                "baseline_citation_correct": None,
            }
        )

        sections.append(f"## {package_id} / {role}")
        sections.append("")
        question = run_ab.turn_prompt(draft, role).split("\n\nDeliverable:")[0].strip()
        sections.append(f"问题：{question[:600]}")
        sections.append("")
        sections.append("参考事实（只勾选答案里真的说清楚的）：")
        for fact_id, fact in gold.items():
            sections.append(f"- `{fact_id}` — {fact['claim']}")
        sections.append("")
        for arm, grade in (("baseline", base_grade), ("envoy", envoy_grade)):
            text = (answers.get((package_id, arm, role)) or {}).get("answer") or ""
            text = " ".join(text.split())
            clipped = text[: args.snippet] + ("…" if len(text) > args.snippet else "")
            hit = grade.get("facts_hit", 0)
            total = grade.get("facts_total", len(gold))
            sections.append(
                f"**{arm}** — 终态 {arms[arm]['outcome']}，关键词命中 {hit}/{total}，"
                f"答案 {arms[arm]['answer_chars']:,} 字符"
            )
            sections.append("")
            sections.append(f"> {clipped or '（无答案）'}")
            sections.append("")
        sections.append("填写：`envoy_supported` / `baseline_supported` = 上面事实里被答案真正说清的 id 列表；")
        sections.append("`answer_correct` / `baseline_answer_correct` = true/false；引号事实缺失则 citation 记 false。")
        sections.append("")

    if args.skeleton:
        args.skeleton.write_text(
            json.dumps({"reviews": reviews}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.worksheet:
        extra = args.worksheet.with_suffix(".md")
        extra.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(reviews)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
