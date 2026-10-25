#!/usr/bin/env python3
"""Summarise an A/B run from the server ledger export plus the run log.

Usage:
    python3 ab_report.py --ledger run.ndjson --runlog /tmp/ab-run/run-log.jsonl \
        [--runlog /tmp/ab-run-s1/run-log.jsonl ...] --out summary.json [--markdown out.md]

The ledger is the authority for token counts: every request row is summed per
(package_id, arm, turn_correlation_id). Turn outcome comes from the run log
(exit code 124 means the wall-clock cap was hit and the turn counts as blocked).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

CAP_SECONDS = 900


def load_ledger(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_runlog(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _dedupe_attempts(rows: list[dict]) -> list[dict]:
    """Keep one row per (package, arm, role): completed, then capped, then failed.

    A dead gateway makes the CLI exit 1 within minutes; those rows are real
    ledger traffic but not model results, so a later successful attempt for the
    same key replaces them. The number of replaced attempts is reported.
    """
    rank = {0: 0, 124: 1}
    best: dict[tuple[str, str, str], dict] = {}
    replaced = 0
    for row in rows:
        key = (row["package_id"], row["arm"], row["role"])
        score = rank.get(int(row.get("exit_code") or 0), 2)
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        current_score = rank.get(int(current.get("exit_code") or 0), 2)
        replaced += 1
        if score <= current_score:
            best[key] = row
    if replaced:
        print(f"note: replaced {replaced} superseded turn attempt(s)", file=sys.stderr)
    return list(best.values())


def turn_key(row: dict) -> tuple[str, str, str]:
    return (row["package_id"], row["arm"], row["turn_correlation_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--runlog", action="append", required=True)
    parser.add_argument("--telemetry", action="append", default=[])
    parser.add_argument("--grade", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--markdown")
    args = parser.parse_args()

    ledger = load_ledger(Path(args.ledger))
    telemetry: dict[tuple[str, str, str], dict] = {}
    for path in args.telemetry:
        for row in load_runlog([Path(path)]):
            telemetry[(row["package_id"], row["arm"], row["role"])] = row
    grades: dict[tuple[str, str, str], dict] = {}
    for path in args.grade:
        for row in load_runlog([Path(path)]):
            grades[(row["package_id"], row["arm"], row["role"])] = row
    runlog = _dedupe_attempts(load_runlog([Path(p) for p in args.runlog]))

    requests: dict[tuple[str, str, str], dict] = {}
    for row in ledger:
        key = turn_key(row)
        bucket = requests.setdefault(
            key,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "request_count": 0,
                "failure_count": 0,
                "salts": set(),
                "server_ids": set(),
                "local_ids": set(),
            },
        )
        bucket["input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["output_tokens"] += int(row.get("output_tokens") or 0)
        bucket["cache_read_input_tokens"] += int(row.get("cache_read_input_tokens") or 0)
        bucket["cache_creation_input_tokens"] += int(row.get("cache_creation_input_tokens") or 0)
        bucket["request_count"] += 1
        failed = row.get("status") == "failure"
        if failed:
            bucket["failure_count"] += 1
        if row.get("salt_sha256"):
            bucket["salts"].add(row["salt_sha256"])
        # A failed upstream call carries no server id and costs no tokens, so it
        # is excluded from the counted-request identity check; the count of
        # excluded rows is reported instead of being silently dropped.
        if not failed:
            if row.get("server_request_id"):
                bucket["server_ids"].add(row["server_request_id"])
            if row.get("local_request_id"):
                bucket["local_ids"].add(row["local_request_id"])

    turns = []
    for row in runlog:
        key = turn_key(row)
        stats = requests.get(key, {})
        outcome = "completed"
        if int(row.get("exit_code") or 0) == 124:
            outcome = "blocked"
        elif int(row.get("exit_code") or 0) != 0 or row.get("is_error"):
            outcome = "failed"
        turns.append(
            {
                "package_id": row["package_id"],
                "arm": row["arm"],
                "role": row["role"],
                "turn_correlation_id": row["turn_correlation_id"],
                "outcome": outcome,
                "exit_code": row.get("exit_code"),
                "started_at": row.get("started_at"),
                "wall_seconds": round(float(row.get("elapsed_seconds") or 0), 1),
                "cli_input_tokens": row.get("input_tokens"),
                "cli_output_tokens": row.get("output_tokens"),
                "ledger_input_tokens": stats.get("input_tokens", 0),
                "ledger_output_tokens": stats.get("output_tokens", 0),
                "ledger_cache_read": stats.get("cache_read_input_tokens", 0),
                "ledger_cache_creation": stats.get("cache_creation_input_tokens", 0),
                "request_count": stats.get("request_count", 0),
                "failure_count": stats.get("failure_count", 0),
                "salt_count": len(stats.get("salts", ())),
                "server_id_count": len(stats.get("server_ids", ())),
                "server_ids_unique": len(stats.get("server_ids", ())) == len(stats.get("local_ids", ())),
                "counted_requests": len(stats.get("server_ids", ())),
                "tool_calls": telemetry.get(key, {}).get("tool_calls"),
                "envoy_calls": telemetry.get(key, {}).get("envoy_calls"),
                "repeated_targets": telemetry.get(key, {}).get("repeated_targets"),
                "first_evidence_ms": telemetry.get(key, {}).get("first_evidence_ms"),
                "facts_total": grades.get(key, {}).get("facts_total"),
                "facts_hit": grades.get(key, {}).get("facts_hit"),
                "attributed_sources": grades.get(key, {}).get("attributed_sources"),
                "sources_total": grades.get(key, {}).get("sources_total"),
                "answer_chars": grades.get(key, {}).get("answer_chars"),
            }
        )

    by_arm: dict[str, list[dict]] = {}
    for turn in turns:
        by_arm.setdefault(turn["arm"], []).append(turn)

    summary: dict = {
        "turn_count": len(turns),
        "turn_outcomes": {
            arm: {
                "turns": len(items),
                "completed": sum(1 for t in items if t["outcome"] == "completed"),
                "blocked": sum(1 for t in items if t["outcome"] == "blocked"),
                "failed": sum(1 for t in items if t["outcome"] == "failed"),
            }
            for arm, items in by_arm.items()
        },
        "arms": {},
        "paired": [],
        "turns": turns,
    }

    for arm, items in by_arm.items():
        inputs = [t["ledger_input_tokens"] for t in items]
        summary["arms"][arm] = {
            "turns": len(items),
            "blocked": sum(1 for t in items if t["outcome"] == "blocked"),
            "total_input_tokens": sum(inputs),
            "input_tokens_median": statistics.median(inputs) if inputs else None,
            "total_output_tokens": sum(t["ledger_output_tokens"] for t in items),
            "total_wall_seconds": round(sum(t["wall_seconds"] for t in items), 1),
            "cache_read_total": sum(t["ledger_cache_read"] for t in items),
            "requests": sum(t["request_count"] for t in items),
            "failures": sum(t["failure_count"] for t in items),
            "tool_calls_total": sum(t["tool_calls"] or 0 for t in items),
            "tool_calls_median": statistics.median([t["tool_calls"] or 0 for t in items]) if items else None,
            "first_evidence_median_ms": statistics.median(
                [t["first_evidence_ms"] for t in items if t["first_evidence_ms"] is not None]
            )
            if any(t["first_evidence_ms"] is not None for t in items)
            else None,
            "repeated_targets_total": sum(t["repeated_targets"] or 0 for t in items),
            "facts_hit_total": sum(t["facts_hit"] or 0 for t in items),
            "facts_total": sum(t["facts_total"] or 0 for t in items),
            "attributed_sources_total": sum(
                len(t["attributed_sources"] or []) for t in items
            ),
            "answer_chars_median": statistics.median([t["answer_chars"] or 0 for t in items]) if items else None,
        }

    indexed = {(t["package_id"], t["role"], t["arm"]): t for t in turns}
    for (package_id, role, arm) in sorted(indexed):
        if arm != "baseline":
            continue
        other = indexed.get((package_id, role, "envoy"))
        if not other:
            continue
        base = indexed[(package_id, role, "baseline")]
        summary["paired"].append(
            {
                "package_id": package_id,
                "role": role,
                "baseline_input_tokens": base["ledger_input_tokens"],
                "envoy_input_tokens": other["ledger_input_tokens"],
                "baseline_outcome": base["outcome"],
                "envoy_outcome": other["outcome"],
                "delta_tokens": other["ledger_input_tokens"] - base["ledger_input_tokens"],
                "baseline_tool_calls": base["tool_calls"],
                "envoy_tool_calls": other["tool_calls"],
                "baseline_facts_hit": base["facts_hit"],
                "envoy_facts_hit": other["facts_hit"],
                "facts_total": base["facts_total"],
            }
        )

    gate = {
        "every_request_has_metadata": all(t["request_count"] > 0 for t in turns),
        "cache_read_zero": all(t["ledger_cache_read"] == 0 for t in turns),
        "salts_present": all(t["salt_count"] >= 1 for t in turns),
        "server_ids_unique": all(t["server_ids_unique"] for t in turns),
        "failed_requests_excluded": sum(t["failure_count"] for t in turns),
        "cap_seconds": CAP_SECONDS,
    }
    gate["passed"] = all(v for k, v in gate.items() if isinstance(v, bool))
    summary["gate"] = gate

    completed_pairs = [p for p in summary["paired"] if p["baseline_outcome"] == "completed" and p["envoy_outcome"] == "completed"]
    summary["paired_completed"] = len(completed_pairs)
    if completed_pairs and gate["passed"]:
        base_total = sum(p["baseline_input_tokens"] for p in completed_pairs)
        envoy_total = sum(p["envoy_input_tokens"] for p in completed_pairs)
        summary["paired_input_tokens"] = {"baseline": base_total, "envoy": envoy_total}
        if base_total:
            summary["paired_input_reduction"] = round(1 - envoy_total / base_total, 4)

    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.markdown:
        lines = ["# A/B 结果（服务端账本口径）", "", f"轮次：{summary['turn_count']}，每轮上限 {CAP_SECONDS}s。", ""]
        lines.append("| 臂 | 轮次 | completed | blocked | 账本 input tokens | 中位 input | 账本 output | 墙钟合计 s |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for arm, stats in summary["arms"].items():
            lines.append(
                f"| {arm} | {stats['turns']} | {summary['turn_outcomes'][arm]['completed']} | "
                f"{stats['blocked']} | {stats['total_input_tokens']} | {stats['input_tokens_median']} | "
                f"{stats['total_output_tokens']} | {stats['total_wall_seconds']} |"
            )
        lines.append("")
        lines.append("")
        lines.append("| 臂 | 工具调用合计 | 工具调用中位 | 首次证据中位 ms | 重复抓取 | facts 命中/总数 | 归属来源命中 | 答案中位字符 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for arm, stats in summary["arms"].items():
            lines.append(
                f"| {arm} | {stats['tool_calls_total']} | {stats['tool_calls_median']} | "
                f"{stats['first_evidence_median_ms']} | {stats['repeated_targets_total']} | "
                f"{stats['facts_hit_total']}/{stats['facts_total']} | "
                f"{stats['attributed_sources_total']} | {stats['answer_chars_median']} |"
            )
        lines.append("")
        lines.append(f"门禁：{json.dumps(gate, ensure_ascii=False)}")
        if "paired_input_reduction" in summary:
            lines.append(
                f"配对（均 completed，{summary['paired_completed']} 轮）：baseline {summary['paired_input_tokens']['baseline']} → "
                f"envoy {summary['paired_input_tokens']['envoy']}，变化 {summary['paired_input_reduction'] * 100:.1f}%。"
            )
        else:
            lines.append("未产出节省比例：配对 completed 轮次不足或门禁未通过。")
        Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({k: summary[k] for k in ("turn_count", "turn_outcomes", "gate", "paired_completed")}, ensure_ascii=False))
    if "paired_input_reduction" in summary:
        print("paired_input_reduction", summary["paired_input_reduction"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
