#!/usr/bin/env python3
"""Find the turns where the arms were not equivalent.

Averages hide the interesting case: the same link, the same question, one arm
finishes and the other does not. This prints those contrasts with the evidence
that backs them, so a reader can check a claim against a transcript instead of
trusting a median.

Usage: contrast_report.py --report /tmp/AB3/report --markdown contrast.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--snippet", type=int, default=220)
    args = parser.parse_args()

    summary = json.loads((args.report / "summary.json").read_text(encoding="utf-8"))
    answers: dict[tuple[str, str, str], dict] = {}
    for name in ("answers-recovered.jsonl",):
        for row in load_jsonl(args.report / name):
            answers[(row["package_id"], row["arm"], row["role"])] = row

    turns = {
        (turn["package_id"], turn["arm"], turn["role"]): turn for turn in summary["turns"]
    }
    tasks: dict[tuple[str, str], dict[str, dict]] = {}
    for (package_id, arm, role), turn in turns.items():
        tasks.setdefault((package_id, role), {})[arm] = turn

    lines: list[str] = ["# 同一链接、同一问题下的两臂差异", ""]
    lines.append(
        "只列出两臂都跑过、且终态或得分不同的轮次。`blocked` = 撞 900s 上限，"
        "`failed` = 进程退出码非 0/124，`completed` = 正常结束。"
    )
    lines.append("")
    header = (
        "| package | role | baseline | envoy | baseline 命中 facts | envoy 命中 facts "
        "| baseline 答案字符 | envoy 答案字符 |"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---:|---:|---:|---:|")
    contrasts: list[tuple] = []
    for (package_id, role), arms in sorted(tasks.items()):
        base = arms.get("baseline")
        env = arms.get("envoy")
        if not base or not env:
            continue
        if base["outcome"] == env["outcome"] and base["facts_hit"] == env["facts_hit"]:
            continue
        contrasts.append((package_id, role, base, env))
        lines.append(
            f"| {package_id} | {role} | {base['outcome']} | {env['outcome']} "
            f"| {base['facts_hit']}/{base['facts_total']} | {env['facts_hit']}/{env['facts_total']} "
            f"| {base['answer_chars']:,} | {env['answer_chars']:,} |"
        )
    lines.append("")

    lines.append("## 证据摘录")
    lines.append("")
    shown = 0
    for package_id, role, base, env in contrasts:
        winner = env if (env["facts_hit"], env["answer_chars"]) > (base["facts_hit"], base["answer_chars"]) else base
        loser = base if winner is env else env
        row = answers.get((package_id, winner["arm"], role))
        snippet = ""
        if row:
            text = " ".join((row.get("answer") or "").split())
            snippet = text[: args.snippet] + ("…" if len(text) > args.snippet else "")
        lines.append(
            f"- **{package_id} / {role}** — {winner['arm']} {winner['outcome']}，"
            f"命中 {winner['facts_hit']}/{winner['facts_total']} facts；"
            f"{loser['arm']} {loser['outcome']}，命中 {loser['facts_hit']}/{loser['facts_total']}。"
        )
        if snippet:
            source = (row or {}).get("answer_source") or "cli_result"
            lines.append(f"  - 摘录（{winner['arm']}，来源 {source}）：{snippet}")
        shown += 1
        if shown >= 12:
            lines.append("")
            lines.append(f"其余 {len(contrasts) - shown} 轮见 summary.json。")
            break
    lines.append("")
    lines.append(f"共 {len(contrasts)} 轮两臂不同；轮次总数 {len(turns)}。")

    text = "\n".join(lines) + "\n"
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    else:
        print(text)
    print(json.dumps({"contrasts": len(contrasts), "turns": len(turns)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
