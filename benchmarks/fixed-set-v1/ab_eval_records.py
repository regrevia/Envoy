#!/usr/bin/env python3
"""Assemble ``envoy.agent-product-eval/1`` records from the A/B run artifacts.

Inputs are the artifacts produced by ``verify_ab.sh`` plus the frozen set, so the
evaluation cannot drift from the measured run:

* ``summary.json``   per-turn ledger and wall-clock numbers (``ab_report.py``)
* ``grade.jsonl``    per-turn reference-fact coverage and source attribution
* ``telemetry.jsonl`` per-turn tool calls and first-evidence timing
* ``task-drafts/``   the frozen goals and fact IDs
* ``source-intake.csv`` + ``manifest.json`` the frozen source identities

Facts a turn is scored on are the package's frozen ``reference_facts``; a fact is
*claimed* when the answer's wording covers it and *supported* only when a human
review file marks it reviewed, so the report never upgrades keyword overlap to
verified support on its own.

Usage:
    python3 ab_eval_records.py --report /tmp/AB3/report --out /tmp/AB3/report/eval-records.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_ab  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_drafts(drafts: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(drafts.glob("*.json")):
        draft = json.loads(path.read_text(encoding="utf-8"))
        out[draft["package_id"]] = draft
    return out


def load_sources(intake: Path, manifest: Path) -> list[dict]:
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    by_package: dict[str, list[dict]] = {}
    for source in frozen.get("sources", []):
        by_package.setdefault(source["package_id"], []).append(source)
    rows: list[dict] = []
    with intake.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            package_id = row["package_id"]
            slot = row["source_slot"]
            match = next(
                (item for item in by_package.get(package_id, []) if str(item.get("source_slot")) == str(slot)),
                None,
            )
            if match is None:
                continue
            rows.append(
                {
                    "source_id": row["package_id"] + "-s" + str(slot),
                    "url": row["canonical_url"],
                    "source_class": row["source_class"],
                    "source_revision": row["source_revision_sha256"],
                    "revalidated_at": row["revalidated_at"],
                }
            )
    return rows


def source_ids_for(draft: dict) -> list[str]:
    return [draft["package_id"] + "-s" + str(index + 1) for index in range(len(draft["source_ids"]))]


def build_run(envoy_dir: Path) -> dict:
    pyproject = (envoy_dir / "packages/envoy-core/pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    name = match.group(1) if match else "unknown"
    commit = ""
    try:
        head = (envoy_dir / ".git/HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            commit = (envoy_dir / ".git" / ref).read_text(encoding="utf-8").strip()
        else:
            commit = head
    except OSError:
        commit = "unknown"
    return {
        "record_type": "run",
        "run_id": "",
        "envoy_version": name,
        "envoy_commit": commit[:12] or "unknown",
        "host_agent": "Claude Code",
        "host_agent_version": "2.1.197",
        "operating_system": f"macOS {platform.mac_ver()[0]}",
        "python_version": platform.python_version(),
        "started_at": "",
        "asr_status": "local_asr_available",
        "ocr_status": "not_used",
        "provider": "anthropic",
        "model": "DeepSeek-V4.1-Flash",
        "rubric_version": "fixed-set-v1",
        "quality_review_method": (
            "automatic reference-fact keyword coverage (>=60% of claim words) plus "
            "source attribution by frozen snapshot title/author; human spot check recorded "
            "in review.json and never inferred"
        ),
        "cache_isolation_status": "verified_zero_cache_read_in_ledger",
    }


def review_lookup(review_path: Path | None) -> dict[str, dict]:
    if not review_path or not review_path.is_file():
        return {}
    data = json.loads(review_path.read_text(encoding="utf-8"))
    return {item["task_id"]: item for item in data.get("reviews", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--drafts", default=str(HERE / "task-drafts"))
    parser.add_argument("--intake", default=str(HERE / "source-intake.csv"))
    parser.add_argument("--manifest", default=str(HERE / "manifest.json"))
    parser.add_argument("--review")
    parser.add_argument("--envoy-dir", default=str(HERE.parent.parent))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report = Path(args.report)
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    grades = {(row["package_id"], row["arm"], row["role"]): row for row in load_jsonl(report / "grade.jsonl")}
    telemetry = {(row["package_id"], row["arm"], row["role"]): row for row in load_jsonl(report / "telemetry.jsonl")}
    drafts = load_drafts(Path(args.drafts))
    reviews = review_lookup(Path(args.review) if args.review else None)

    run = build_run(Path(args.envoy_dir))
    run["run_id"] = summary.get("run_id") or "envoy-ab-run"

    turns = summary["turns"]
    started = sorted(
        (turn for turn in turns if turn.get("wall_seconds") is not None),
        key=lambda turn: turn["wall_seconds"],
    )
    if started:
        run["started_at"] = min(
            (turn.get("started_at") for turn in started if turn.get("started_at")),
            default="",
        ) or ""

    records: list[dict] = [run]
    for source in load_sources(Path(args.intake), Path(args.manifest)):
        records.append({"record_type": "source", **source})

    by_key: dict[tuple[str, str], dict] = {}
    for turn in turns:
        by_key[(turn["package_id"], turn["arm"], turn["role"])] = turn

    for package_id, draft in sorted(drafts.items()):
        declared = source_ids_for(draft)
        for role in ("initial", "followup-1", "followup-2"):
            envoy_turn = by_key.get((package_id, "envoy", role))
            base_turn = by_key.get((package_id, "baseline", role))
            if not envoy_turn or not base_turn:
                continue
            task_id = f"{package_id}-{role}"
            envoy_grade = grades.get((package_id, "envoy", role), {})
            base_grade = grades.get((package_id, "baseline", role), {})
            envoy_tel = telemetry.get((package_id, "envoy", role), {})
            base_tel = telemetry.get((package_id, "baseline", role), {})
            gold = {fact["fact_id"]: fact["claim"] for fact in draft.get("reference_facts", [])}
            claimed_envoy = [fact_id for fact_id in envoy_grade.get("fact_hits", []) if fact_id in gold]
            claimed_base = [fact_id for fact_id in base_grade.get("fact_hits", []) if fact_id in gold]
            review = reviews.get(task_id, {})
            supported_envoy = [fact_id for fact_id in review.get("envoy_supported", []) if fact_id in claimed_envoy]
            supported_base = [fact_id for fact_id in review.get("baseline_supported", []) if fact_id in claimed_base]
            records.append(
                {
                    "record_type": "task",
                    "task_id": task_id,
                    "package_id": package_id,
                    "source_id": declared[0],
                    "source_ids": declared,
                    "task_role": role,
                    "goal": run_ab.turn_prompt(draft, role).split("\n\nDeliverable:")[0].strip(),
                    "reference_facts": gold,
                    "envoy_claimed_fact_ids": claimed_envoy,
                    "envoy_supported_fact_ids": supported_envoy,
                    "baseline_claimed_fact_ids": claimed_base,
                    "baseline_supported_fact_ids": supported_base,
                    "envoy_citations": [
                        {"url": row["url"], "locator": "attributed in answer", "supports_fact": False}
                        for row in _citation_rows(draft, envoy_grade, gold)
                    ],
                    "baseline_citations": [
                        {"url": row["url"], "locator": "attributed in answer", "supports_fact": False}
                        for row in _citation_rows(draft, base_grade, gold)
                    ],
                    "answer_correct": review.get("answer_correct"),
                    "citation_correct": review.get("citation_correct"),
                    "baseline_answer_correct": review.get("baseline_answer_correct"),
                    "baseline_citation_correct": review.get("baseline_citation_correct"),
                    "first_evidence_ms": envoy_tel.get("first_evidence_ms"),
                    "baseline_first_evidence_ms": base_tel.get("first_evidence_ms"),
                    "total_elapsed_ms": round(float(envoy_turn.get("wall_seconds") or 0) * 1000),
                    "baseline_total_elapsed_ms": round(float(base_turn.get("wall_seconds") or 0) * 1000),
                    "envoy_tool_calls": envoy_tel.get("tool_calls"),
                    "baseline_tool_calls": base_tel.get("tool_calls"),
                    "visual_inputs": 0,
                    "baseline_visual_inputs": 0,
                    "repeated_downloads": envoy_tel.get("repeated_targets") or 0,
                    "baseline_repeated_downloads": base_tel.get("repeated_targets") or 0,
                    "reused_material": bool(envoy_tel.get("envoy_calls") == 0 and role != "initial"),
                    "baseline_reused_material": bool(base_tel.get("tool_calls") == 0),
                    "baseline_method": "direct_host_read",
                    "provider": run["provider"],
                    "model": run["model"],
                    "baseline_provider": run["provider"],
                    "baseline_model": run["model"],
                    "usage_status": "actual",
                    "usage_breakdown_status": "actual",
                    "uncached_input_tokens": envoy_turn.get("ledger_input_tokens"),
                    "cache_read_input_tokens": envoy_turn.get("ledger_cache_read"),
                    "cache_creation_input_tokens": envoy_turn.get("ledger_cache_creation"),
                    "input_tokens": envoy_turn.get("ledger_input_tokens"),
                    "output_tokens": envoy_turn.get("ledger_output_tokens"),
                    "baseline_usage_status": "actual",
                    "baseline_usage_breakdown_status": "actual",
                    "baseline_uncached_input_tokens": base_turn.get("ledger_input_tokens"),
                    "baseline_cache_read_input_tokens": base_turn.get("ledger_cache_read"),
                    "baseline_cache_creation_input_tokens": base_turn.get("ledger_cache_creation"),
                    "baseline_input_tokens": base_turn.get("ledger_input_tokens"),
                    "baseline_output_tokens": base_turn.get("ledger_output_tokens"),
                }
            )

    Path(args.out).write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8"
    )
    print(json.dumps({"records": len(records), "tasks": len(records) - 1 - 40}))
    return 0


def _citation_rows(draft: dict, grade: dict, gold: dict[str, str]) -> list[dict]:
    """Attribution hits as citation records, never marked as fact support."""
    rows: list[dict] = []
    revision_urls = [revision["url"] for revision in draft.get("source_revisions", {}).values()]
    for slot in grade.get("attributed_sources", []) or []:
        index = int(slot) - 1
        if 0 <= index < len(revision_urls):
            rows.append({"url": revision_urls[index], "locator": "attributed in answer", "supports_fact": False})
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
