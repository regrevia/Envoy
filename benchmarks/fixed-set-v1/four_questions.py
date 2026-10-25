#!/usr/bin/env python3
"""Render the four user questions from a finished A/B report directory.

Every number is computed from the report artifacts; anything the artifacts do
not carry is printed as N/A with the sample size that produced it. No estimate
is ever substituted for a missing field.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def count(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return int(value)


def median(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def fmt(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    text = f"{value:,.{digits}f}"
    return f"{text}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="directory holding summary.json/grade.jsonl/telemetry.jsonl")
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--ledger-report", help="usage-ledger.json (default: <report>/usage-ledger.json)")
    args = parser.parse_args()

    report = Path(args.report)
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    ledger_path = Path(args.ledger_report) if args.ledger_report else report / "usage-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}

    turns: list[dict] = summary.get("turns", [])
    by_arm: dict[str, list[dict]] = {}
    for turn in turns:
        by_arm.setdefault(turn["arm"], []).append(turn)

    # Pairing is by (package, role) so an arm that capped still pairs with an
    # arm that completed; the outcome is reported next to the delta.
    keyed: dict[tuple[str, str], dict[str, dict]] = {}
    for turn in turns:
        keyed.setdefault((turn["package_id"], turn["role"]), {})[turn["arm"]] = turn
    pairs = [(k, v) for k, v in keyed.items() if len(v) == 2]
    both_ok = [(k, v) for k, v in pairs if all(t["outcome"] == "completed" for t in v.values())]

    lines: list[str] = []
    lines.append("# Envoy 固定评测集 v1：四个问题的实测回答")
    lines.append("")
    lines.append(f"- run_id: `{summary.get('run_id') or 'N/A'}`")
    lines.append(f"- 有效轮次：{len(turns)}（计划 {ledger.get('planned_turn_count', 'N/A')}）")
    lines.append(f"- 双覆盖配对：{len(pairs)}（两臂都 completed：{len(both_ok)}）")
    lines.append(f"- 缓存隔离：`{ledger.get('cache_isolation_status', 'N/A')}`")
    lines.append("")

    # Q1: without Envoy, can a linked source be read?
    lines.append("## 1. 不装 Envoy 时链接能否读取")
    lines.append("")
    lines.append("| 臂 | 轮次 | 归属来源命中 | 来源总数 | 答案中位字符 | 零来源轮次 | 空答案 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for arm in ("baseline", "envoy"):
        items = by_arm.get(arm, [])
        attributed = sum(count(t.get("attributed_sources")) for t in items)
        declared = sum(count(t.get("sources_total")) for t in items)
        empty = sum(1 for t in items if not count(t.get("attributed_sources")))
        lines.append(
            f"| {arm} | {len(items)} | {attributed} | {declared} | "
            f"{fmt(median([int(t.get('answer_chars') or 0) for t in items if int(t.get('answer_chars') or 0) > 0]))} | {empty} | "
            f"{sum(1 for t in items if not int(t.get('answer_chars') or 0))} |"
        )
    lines.append("")
    lines.append("来源命中＝答案里出现了该 package 声明的 source 定位（URL/ID）。")
    lines.append("")

    # Q2: token cost
    lines.append("## 2. token 消耗对比（服务端账本口径）")
    lines.append("")
    lines.append("| 臂 | 计入请求 | 输入 token 合计 | 输入中位 | 输出 token 合计 | cache_read |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for arm in ("baseline", "envoy"):
        items = by_arm.get(arm, [])
        lines.append(
            f"| {arm} | {sum(int(t.get('counted_requests') or 0) for t in items)} | "
            f"{fmt(sum(int(t.get('ledger_input_tokens') or 0) for t in items))} | "
            f"{fmt(median([t.get('ledger_input_tokens') for t in items]))} | "
            f"{fmt(sum(int(t.get('ledger_output_tokens') or 0) for t in items))} | "
            f"{fmt(sum(int(t.get('ledger_cache_read') or 0) for t in items))} |"
        )
    lines.append("")
    if both_ok:
        deltas = []
        for _, pair in both_ok:
            base = int(pair["baseline"].get("ledger_input_tokens") or 0)
            envoy = int(pair["envoy"].get("ledger_input_tokens") or 0)
            if base > 0 and envoy > 0:
                deltas.append((envoy - base) / base * 100)
        if deltas:
            lines.append(
                f"两臂都 completed 且两侧账本均非零的 {len(deltas)} 对：envoy 输入 token 相对变化中位数 "
                f"{fmt(median(deltas), 1, '%')}（正数＝envoy 更贵）。"
            )
        else:
            lines.append("两侧账本均非零的配对为 0，不给 token 对比结论。")
    else:
        lines.append("两臂都 completed 的配对为 0，不给 token 对比结论。")
    lines.append("")

    # Q3: speed
    lines.append("## 3. 处理速度")
    lines.append("")
    lines.append("| 臂 | 轮次 | 墙钟中位 s | 墙钟 p90 s | 首次证据中位 s | 撞上限 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for arm in ("baseline", "envoy"):
        items = by_arm.get(arm, [])
        walls = sorted(float(t.get("wall_seconds") or 0) for t in items)
        p90 = walls[max(0, int(len(walls) * 0.9) - 1)] if walls else None
        first = median([t.get("first_evidence_ms") / 1000 if t.get("first_evidence_ms") else None for t in items])
        capped = sum(1 for t in items if int(t.get("exit_code") or 0) == 124)
        lines.append(
            f"| {arm} | {len(items)} | {fmt(median(walls), 1)} | {fmt(p90, 1)} | {fmt(first, 1)} | {capped} |"
        )
    lines.append("")

    # Q4: long / capped tasks
    lines.append("## 4. 长任务（撞上限、多跳）")
    lines.append("")
    lines.append("| 臂 | completed | blocked(124) | failed | 工具调用中位 | 重复抓取合计 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for arm in ("baseline", "envoy"):
        items = by_arm.get(arm, [])
        lines.append(
            f"| {arm} | {sum(1 for t in items if t['outcome'] == 'completed')} | "
            f"{sum(1 for t in items if t['outcome'] == 'blocked')} | "
            f"{sum(1 for t in items if t['outcome'] == 'failed')} | "
            f"{fmt(median([t.get('tool_calls') for t in items]), 1)} | "
            f"{sum(int(t.get('repeated_targets') or 0) for t in items)} |"
        )
    lines.append("")
    multi = [
        (k, v)
        for k, v in pairs
        if v["envoy"].get("envoy_calls") and int(v["envoy"]["envoy_calls"]) > 0
    ]
    capped_pairs = [(k, v) for k, v in pairs if any(int(t.get("exit_code") or 0) == 124 for t in v.values())]
    lines.append(
        f"多跳（envoy 侧至少 1 次 envoy 调用）配对 {len(multi)}；含撞上限的配对 {len(capped_pairs)}。"
    )
    lines.append("")

    gate = summary.get("gate", {})
    lines.append("## 门禁")
    lines.append("")
    lines.append(f"```json\n{json.dumps(gate, ensure_ascii=False, indent=2)}\n```")
    lines.append("")
    if not ledger.get("valid"):
        lines.append(
            f"账本门禁未通过：observed {ledger.get('observed_turn_count')}/"
            f"{ledger.get('planned_turn_count')}，缺 {ledger.get('missing_turn_count')}。"
            "以上数字只代表当前样本量，不作效率结论。"
        )

    Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"turns": len(turns), "pairs": len(pairs), "both_completed": len(both_ok)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
