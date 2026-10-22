#!/usr/bin/env python3
"""Validate and summarize a real Envoy agent product evaluation.

This tool never invents source, answer, citation, or provider usage data. The
input is a JSONL file with one source record and two or more task records per
source. The evaluator records evidence; the host agent remains responsible for
answering the task.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from urllib.parse import urlparse


SCHEMA_VERSION = "envoy.agent-product-eval/1"
MIN_SOURCES = 20
MIN_TASKS = 40
PLACEHOLDER_HOSTS = {"example.org", "example.com", "example.invalid", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
SENSITIVE_QUERY_KEYS = {
    "access_token", "auth", "authorization", "cookie", "key", "signature",
    "sig", "token", "x-amz-credential", "x-amz-signature",
}
REQUIRED_SOURCE_FIELDS = {"source_id", "url", "source_class"}
REQUIRED_RUN_FIELDS = {
    "run_id",
    "envoy_version",
    "envoy_commit",
    "host_agent",
    "host_agent_version",
    "operating_system",
    "python_version",
    "started_at",
    "asr_status",
    "ocr_status",
    "provider",
    "model",
}
REQUIRED_TASK_FIELDS = {
    "task_id",
    "source_id",
    "task_role",
    "goal",
    "answer_correct",
    "citation_correct",
    "baseline_answer_correct",
    "baseline_citation_correct",
    "first_evidence_ms",
    "baseline_first_evidence_ms",
    "total_elapsed_ms",
    "baseline_total_elapsed_ms",
    "envoy_tool_calls",
    "baseline_tool_calls",
    "visual_inputs",
    "baseline_visual_inputs",
    "repeated_downloads",
    "baseline_repeated_downloads",
    "reused_material",
    "baseline_reused_material",
    "baseline_method",
}


def _load_records(path: Path) -> tuple[dict | None, list[dict], list[dict]]:
    runs: list[dict] = []
    sources: list[dict] = []
    tasks: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON ({exc.msg})") from exc
        if not isinstance(item, dict) or item.get("record_type") not in {"run", "source", "task"}:
            raise ValueError(
                f"{path}:{line_number}: record_type must be run, source, or task"
            )
        if item["record_type"] == "run":
            runs.append(item)
        else:
            (sources if item["record_type"] == "source" else tasks).append(item)
    if len(runs) != 1:
        raise ValueError(f"evaluation file must contain exactly one run record; got {len(runs)}")
    return runs[0], sources, tasks


def _require_non_negative(item: dict, field: str, record_id: str) -> None:
    value = item.get(field)
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{record_id}: {field} must be a non-negative number")


def _canonical_source_url(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}{parsed.path.rstrip('/') or '/'}?{parsed.query}"


def validate_records(
    run: dict,
    sources: list[dict],
    tasks: list[dict],
    *,
    require_minimum: bool = True,
) -> list[str]:
    errors: list[str] = []
    missing_run = REQUIRED_RUN_FIELDS - run.keys()
    if missing_run:
        errors.append(f"run: missing fields {sorted(missing_run)}")
    for field in ("envoy_version", "envoy_commit", "host_agent", "host_agent_version", "operating_system", "python_version"):
        value = str(run.get(field) or "").strip().lower()
        if not value or value.startswith("replace-with") or value in {"unknown", "draft"}:
            errors.append(f"run: {field} must identify the actual evaluation runtime")
    source_ids = set()
    source_urls = set()
    for source in sources:
        missing = REQUIRED_SOURCE_FIELDS - source.keys()
        source_id = str(source.get("source_id") or "<unknown-source>")
        if missing:
            errors.append(f"{source_id}: missing source fields {sorted(missing)}")
        if source_id in source_ids:
            errors.append(f"{source_id}: duplicate source_id")
        source_ids.add(source_id)
        parsed = urlparse(str(source.get("url") or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append(f"{source_id}: url must be a public HTTP(S) URL")
        elif (
            parsed.hostname.lower() in PLACEHOLDER_HOSTS
            or parsed.hostname.lower().endswith(PLACEHOLDER_SUFFIXES)
        ):
            errors.append(f"{source_id}: placeholder URL is not real acceptance evidence")
        else:
            canonical = _canonical_source_url(str(source.get("url") or ""))
            if canonical in source_urls:
                errors.append(f"{source_id}: duplicate source URL does not count as a new source")
            source_urls.add(canonical)
            query_keys = {
                pair.partition("=")[0].lower()
                for pair in parsed.query.split("&")
                if pair
            }
            if query_keys & SENSITIVE_QUERY_KEYS:
                errors.append(f"{source_id}: source URL contains sensitive query parameters")
        if source.get("accepted_state") not in {
            "accepted", "partial", "blocked", "unverified", "failed"
        }:
            errors.append(
                f"{source_id}: accepted_state must describe the observed source"
            )

    task_ids = set()
    tasks_by_source: dict[str, list[dict]] = defaultdict(list)
    run_identity = (str(run.get("provider") or ""), str(run.get("model") or ""))
    for task in tasks:
        task_id = str(task.get("task_id") or "<unknown-task>")
        missing = REQUIRED_TASK_FIELDS - task.keys()
        if missing:
            errors.append(f"{task_id}: missing task fields {sorted(missing)}")
        if task_id in task_ids:
            errors.append(f"{task_id}: duplicate task_id")
        task_ids.add(task_id)
        source_id = str(task.get("source_id") or "")
        tasks_by_source[source_id].append(task)
        if task.get("task_role") not in {"initial", "followup"}:
            errors.append(f"{task_id}: task_role must be initial or followup")
        if source_id not in source_ids:
            errors.append(f"{task_id}: references unknown source_id {source_id}")
        if not isinstance(task.get("answer_correct"), bool):
            errors.append(f"{task_id}: answer_correct must be boolean")
        if not isinstance(task.get("citation_correct"), bool):
            errors.append(f"{task_id}: citation_correct must be boolean")
        for field in (
            "baseline_answer_correct",
            "baseline_citation_correct",
            "baseline_reused_material",
        ):
            if not isinstance(task.get(field), bool):
                errors.append(f"{task_id}: {field} must be boolean")
        for field in (
            "first_evidence_ms",
            "baseline_first_evidence_ms",
            "total_elapsed_ms",
            "baseline_total_elapsed_ms",
            "envoy_tool_calls",
            "baseline_tool_calls",
            "visual_inputs",
            "baseline_visual_inputs",
            "repeated_downloads",
            "baseline_repeated_downloads",
        ):
            _require_non_negative(task, field, task_id)
        if task.get("baseline_method") not in {
            "direct_host_read",
            "host_read_with_fair_cache",
            "host_native_media",
        }:
            errors.append(f"{task_id}: baseline_method must identify the comparison path")
        provider = str(task.get("provider") or "")
        model = str(task.get("model") or "")
        baseline_provider = str(task.get("baseline_provider") or "")
        baseline_model = str(task.get("baseline_model") or "")
        if (provider, model) != run_identity or (baseline_provider, baseline_model) != run_identity:
            errors.append(f"{task_id}: Envoy and baseline must match run provider/model")
        if task.get("usage_status") != "actual":
            errors.append(f"{task_id}: Envoy arm requires actual provider usage")
        if task.get("usage_status") == "actual":
            for field in ("input_tokens", "output_tokens"):
                _require_non_negative(task, field, task_id)
        if task.get("baseline_usage_status") != "actual":
            errors.append(f"{task_id}: baseline_usage_status must be actual")
        if task.get("baseline_usage_status") == "actual":
            for field in ("baseline_input_tokens", "baseline_output_tokens"):
                _require_non_negative(task, field, task_id)
        if not isinstance(task.get("evidence_locators"), list):
            errors.append(f"{task_id}: evidence_locators must be a list")
        elif task.get("citation_correct") and not task["evidence_locators"]:
            errors.append(f"{task_id}: a correct citation requires at least one evidence locator")
        if not isinstance(task.get("reused_material"), bool):
            errors.append(f"{task_id}: reused_material must be boolean")

    for source_id in source_ids:
        source_tasks = tasks_by_source.get(source_id, [])
        roles = [task.get("task_role") for task in source_tasks]
        if roles.count("initial") != 1 or roles.count("followup") < 1:
            errors.append(
                f"{source_id}: requires exactly one initial task and at least one followup task"
            )
    if require_minimum and len(sources) < MIN_SOURCES:
        errors.append(f"minimum source gate is {MIN_SOURCES}; got {len(sources)}")
    if require_minimum and len(tasks) < MIN_TASKS:
        errors.append(f"minimum task gate is {MIN_TASKS}; got {len(tasks)}")
    return errors


def summarize(run: dict, sources: list[dict], tasks: list[dict]) -> dict:
    usage_actual = [
        task for task in tasks
        if task.get("usage_status") == "actual"
        and task.get("baseline_usage_status") == "actual"
    ]
    answer_accuracy = (
        sum(bool(item["answer_correct"]) for item in tasks) / len(tasks)
        if tasks else None
    )
    baseline_answer_accuracy = (
        sum(bool(item["baseline_answer_correct"]) for item in tasks) / len(tasks)
        if tasks else None
    )
    citation_accuracy = (
        sum(bool(item["citation_correct"]) for item in tasks) / len(tasks)
        if tasks else None
    )
    baseline_citation_accuracy = (
        sum(bool(item["baseline_citation_correct"]) for item in tasks) / len(tasks)
        if tasks else None
    )
    quality_non_inferior = bool(tasks) and all(
        (not task["baseline_answer_correct"] or task["answer_correct"])
        and (not task["baseline_citation_correct"] or task["citation_correct"])
        for task in tasks
    )
    envoy_input_tokens = sum(task["input_tokens"] for task in usage_actual)
    baseline_input_tokens = sum(task["baseline_input_tokens"] for task in usage_actual)
    savings_eligible = len(usage_actual) == len(tasks) and quality_non_inferior
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_scope": "real sources and host-agent tasks supplied by evaluator",
        "run": run,
        "source_count": len(sources),
        "task_count": len(tasks),
        "minimum_source_gate": MIN_SOURCES,
        "minimum_task_gate": MIN_TASKS,
        "scale_gate_status": "passed" if len(sources) >= MIN_SOURCES and len(tasks) >= MIN_TASKS else "incomplete",
        "gate_status": "pending_validation",
        "answer_accuracy": answer_accuracy,
        "baseline_answer_accuracy": baseline_answer_accuracy,
        "citation_accuracy": citation_accuracy,
        "baseline_citation_accuracy": baseline_citation_accuracy,
        "quality_non_inferior": quality_non_inferior,
        "median_first_evidence_ms": _median(task["first_evidence_ms"] for task in tasks),
        "baseline_median_first_evidence_ms": _median(task["baseline_first_evidence_ms"] for task in tasks),
        "median_total_elapsed_ms": _median(task["total_elapsed_ms"] for task in tasks),
        "baseline_median_total_elapsed_ms": _median(task["baseline_total_elapsed_ms"] for task in tasks),
        "total_envoy_tool_calls": sum(task["envoy_tool_calls"] for task in tasks),
        "total_baseline_tool_calls": sum(task["baseline_tool_calls"] for task in tasks),
        "total_visual_inputs": sum(task["visual_inputs"] for task in tasks),
        "total_baseline_visual_inputs": sum(task["baseline_visual_inputs"] for task in tasks),
        "total_repeated_downloads": sum(task["repeated_downloads"] for task in tasks),
        "total_baseline_repeated_downloads": sum(task["baseline_repeated_downloads"] for task in tasks),
        "followup_reuse_rate": sum(bool(task["reused_material"]) for task in tasks) / len(tasks) if tasks else None,
        "actual_usage_status": "paired_actual" if len(usage_actual) == len(tasks) else "incomplete",
        "actual_usage_task_count": len(usage_actual),
        "envoy_actual_input_tokens": envoy_input_tokens if len(usage_actual) == len(tasks) else None,
        "envoy_actual_output_tokens": sum(task["output_tokens"] for task in usage_actual) if len(usage_actual) == len(tasks) else None,
        "baseline_actual_input_tokens": baseline_input_tokens if len(usage_actual) == len(tasks) else None,
        "baseline_actual_output_tokens": sum(task["baseline_output_tokens"] for task in usage_actual) if len(usage_actual) == len(tasks) else None,
        "input_token_savings_fraction": None,
        "source_classes": dict(Counter(str(source["source_class"]) for source in sources)),
    }


def _median(values) -> float | None:
    values = sorted(float(value) for value in values)
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return round(values[midpoint], 3)
    return round((values[midpoint - 1] + values[midpoint]) / 2, 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    try:
        run, sources, tasks = _load_records(args.records)
        errors = validate_records(
            run,
            sources,
            tasks,
            require_minimum=not args.allow_incomplete,
        )
        report = summarize(run, sources, tasks)
        release_eval_ready = (
            not errors
            and len(sources) >= MIN_SOURCES
            and len(tasks) >= MIN_TASKS
            and report["actual_usage_status"] == "paired_actual"
            and report["quality_non_inferior"]
        )
        report["gate_status"] = "passed" if release_eval_ready else "incomplete"
        if (
            release_eval_ready
            and report["baseline_actual_input_tokens"] is not None
            and report["baseline_actual_input_tokens"] > 0
        ):
            report["input_token_savings_fraction"] = round(
                (
                    report["baseline_actual_input_tokens"]
                    - report["envoy_actual_input_tokens"]
                ) / report["baseline_actual_input_tokens"],
                6,
            )
        report["validation_status"] = "passed" if not errors else "failed"
        report["validation_errors"] = errors
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "validation_status": "failed", "validation_errors": [str(exc)]}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
