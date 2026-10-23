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
import ipaddress
import json
import math
from pathlib import Path
import re
import sys
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse


SCHEMA_VERSION = "envoy.agent-product-eval/1"
MIN_SOURCES = 20
MIN_TASKS = 40
PLACEHOLDER_HOSTS = {"example.org", "example.com", "example.invalid", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
SENSITIVE_QUERY_KEYS = {
    "access_token", "auth", "authorization", "cookie", "key", "signature",
    "sig", "token", "policy", "key-pair-id", "expires", "auth_key",
    "wstime", "wssecret", "x-amz-credential", "x-amz-signature",
}
TRACKING_QUERY_KEYS = {"from", "ref", "referrer", "share_source", "source"}
REQUIRED_SOURCE_FIELDS = {
    "source_id",
    "url",
    "source_class",
    "source_revision",
    "revalidated_at",
}
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
    "rubric_version",
    "quality_review_method",
}
REQUIRED_TASK_FIELDS = {
    "task_id",
    "source_id",
    "task_role",
    "goal",
    "reference_facts",
    "envoy_claimed_fact_ids",
    "envoy_supported_fact_ids",
    "baseline_claimed_fact_ids",
    "baseline_supported_fact_ids",
    "envoy_citations",
    "baseline_citations",
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


def _load_records(path: Path) -> tuple[dict, list[dict], list[dict]]:
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


def _require_non_negative(
    item: dict,
    field: str,
    record_id: str,
    *,
    allow_unknown: bool = False,
) -> None:
    value = item.get(field)
    if value is None:
        if not allow_unknown:
            raise ValueError(f"{record_id}: {field} is required for a strict evaluation")
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{record_id}: {field} must be a non-negative number")


def _canonical_source_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host if port is None or (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80) else f"{host}:{port}"
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    )
    encoded_query = urlencode(query, doseq=True)
    return f"{parsed.scheme.lower()}://{netloc}{parsed.path.rstrip('/') or '/'}{f'?{encoded_query}' if encoded_query else ''}"


def _is_public_hostname(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    if normalized.endswith((".local", ".internal", ".lan", ".localhost")):
        return False
    try:
        address = ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError:
        return True
    return address.is_global


def _valid_id_list(value) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value))
    )


def _arm_fact_metrics(task: dict, arm: str) -> tuple[float, float, float] | None:
    facts = task.get("reference_facts")
    gold = set(facts) if isinstance(facts, dict) else set()
    raw_claimed = task.get(f"{arm}_claimed_fact_ids")
    raw_supported = task.get(f"{arm}_supported_fact_ids")
    raw_citations = task.get(f"{arm}_citations")
    if (
        not gold
        or not _valid_id_list(raw_claimed)
        or not _valid_id_list(raw_supported)
        or not isinstance(raw_citations, list)
        or task.get("answer_correct" if arm == "envoy" else "baseline_answer_correct") is None
        or task.get("citation_correct" if arm == "envoy" else "baseline_citation_correct") is None
    ):
        return None
    claimed = set(raw_claimed) if isinstance(raw_claimed, list) else set()
    supported = set(raw_supported) if isinstance(raw_supported, list) else set()
    true_claims = len(claimed & supported & gold)
    recall = true_claims / len(gold) if gold else 0.0
    precision = true_claims / len(claimed) if claimed else 0.0
    citations = raw_citations
    citation_rate = (
        sum(
            bool(item.get("supports_fact"))
            for item in citations
            if isinstance(item, dict)
        ) / len(citations)
        if citations else 0.0
    )
    return recall, precision, citation_rate


def _mean(values) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 6) if values else None


def _reviewed_accuracy(items: list[dict], field: str) -> float | None:
    values = [item.get(field) for item in items]
    if not values or any(not isinstance(value, bool) for value in values):
        return None
    return sum(values) / len(values)


def _reviewed_count(items: list[dict], field: str) -> int | None:
    if not items:
        return None
    return sum(isinstance(item.get(field), bool) for item in items)


def _complete_sum(items: list[dict], field: str) -> int | float | None:
    values = [item.get(field) for item in items]
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        return None
    return sum(values)


def _complete_median(items: list[dict], field: str) -> float | None:
    values = [item.get(field) for item in items]
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        return None
    return _median(values)


def validate_records(
    run: dict,
    sources: list[dict],
    tasks: list[dict],
    *,
    require_minimum: bool = True,
    allow_unknown_usage: bool = False,
) -> list[str]:
    errors: list[str] = []
    missing_run = REQUIRED_RUN_FIELDS - run.keys()
    if missing_run and not allow_unknown_usage:
        errors.append(f"run: missing fields {sorted(missing_run)}")
    unknown_markers = {"unknown", "draft", "not exposed"}
    for field in ("envoy_version", "envoy_commit", "host_agent", "host_agent_version", "operating_system", "python_version"):
        raw_value = run.get(field)
        value = str(raw_value or "").strip().lower()
        if raw_value is not None and (
            not value
            or value.startswith("replace-with")
            or (value in unknown_markers and not allow_unknown_usage)
        ):
            errors.append(f"run: {field} must identify the actual evaluation runtime")
        elif raw_value is None and not allow_unknown_usage:
            errors.append(f"run: {field} must identify the actual evaluation runtime")
    commit = str(run.get("envoy_commit") or "").lower()
    if commit and commit in unknown_markers and not allow_unknown_usage:
        errors.append("run: envoy_commit must be a Git commit hash or null when not exposed")
    elif commit and commit not in unknown_markers and not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        errors.append("run: envoy_commit must be a Git commit hash or null when not exposed")
    quality_review_method = run.get("quality_review_method")
    if quality_review_method is not None and quality_review_method not in {
        "dual_human", "single_human", "reference_rubric"
    }:
        errors.append("run: quality_review_method must identify how answers and citations were reviewed")
    elif quality_review_method is None and not allow_unknown_usage:
        errors.append("run: quality_review_method must identify how answers and citations were reviewed")
    for field in ("provider", "model"):
        raw_value = run.get(field)
        value = str(raw_value or "").strip().lower()
        if raw_value is not None and (
            not value
            or value.startswith("replace-with")
            or (value in unknown_markers and not allow_unknown_usage)
        ):
            errors.append(f"run: {field} must identify the actual evaluation runtime")
        elif raw_value is None and not allow_unknown_usage:
            errors.append("run: strict evaluation requires the actual provider and model")
    try:
        started_at = datetime.fromisoformat(str(run.get("started_at") or "").replace("Z", "+00:00"))
        if started_at.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError:
        if run.get("started_at") is not None or not allow_unknown_usage:
            errors.append("run: started_at must be an ISO-8601 timestamp with timezone")
    source_ids = set()
    source_urls = set()
    for source in sources:
        missing = REQUIRED_SOURCE_FIELDS - source.keys()
        source_id = str(source.get("source_id") or "<unknown-source>")
        if missing and not allow_unknown_usage:
            errors.append(f"{source_id}: missing source fields {sorted(missing)}")
        if source_id in source_ids:
            errors.append(f"{source_id}: duplicate source_id")
        source_ids.add(source_id)
        source_url = source.get("url")
        if source_url is None or source_url == "":
            if not allow_unknown_usage:
                errors.append(f"{source_id}: url is required for a strict evaluation")
        else:
            parsed = urlparse(str(source_url))
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or not _is_public_hostname(parsed.hostname)
            ):
                errors.append(f"{source_id}: url must be a public HTTP(S) URL")
            elif (
                parsed.hostname.lower() in PLACEHOLDER_HOSTS
                or parsed.hostname.lower().endswith(PLACEHOLDER_SUFFIXES)
            ):
                errors.append(f"{source_id}: placeholder URL is not real acceptance evidence")
            canonical = _canonical_source_url(str(source_url))
            if canonical in source_urls:
                errors.append(f"{source_id}: duplicate source URL does not count as a new source")
            source_urls.add(canonical)
            query_keys = {
                key.lower()
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            }
            if query_keys & SENSITIVE_QUERY_KEYS:
                errors.append(f"{source_id}: source URL contains sensitive query parameters")
        accepted_state = source.get("accepted_state")
        if accepted_state is not None and accepted_state not in {
            "accepted", "partial", "blocked", "unverified", "failed"
        }:
            errors.append(f"{source_id}: accepted_state must describe the observed source")
        if accepted_state is None and not allow_unknown_usage:
            errors.append(f"{source_id}: accepted_state is required for a strict evaluation")
        revision = source.get("source_revision")
        if revision is not None and (
            not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision.lower())
        ):
            errors.append(f"{source_id}: source_revision must be a SHA-256 hex digest or null if unavailable")
        if revision is None and not allow_unknown_usage:
            errors.append(f"{source_id}: source_revision is required for a strict evaluation")
        try:
            revalidated_at = datetime.fromisoformat(str(source.get("revalidated_at") or "").replace("Z", "+00:00"))
            if revalidated_at.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError:
            if source.get("revalidated_at") is not None or not allow_unknown_usage:
                errors.append(f"{source_id}: revalidated_at must be an ISO-8601 timestamp with timezone")

    task_ids = set()
    tasks_by_source: dict[str, list[dict]] = defaultdict(list)
    run_identity = (str(run.get("provider") or ""), str(run.get("model") or ""))
    for task in tasks:
        task_id = str(task.get("task_id") or "<unknown-task>")
        missing = REQUIRED_TASK_FIELDS - task.keys()
        if missing and not allow_unknown_usage:
            errors.append(f"{task_id}: missing task fields {sorted(missing)}")
        if task_id in task_ids:
            errors.append(f"{task_id}: duplicate task_id")
        task_ids.add(task_id)
        source_id = str(task.get("source_id") or "")
        tasks_by_source[source_id].append(task)
        goal = str(task.get("goal") or "").strip()
        if task.get("goal") is not None and not goal:
            errors.append(f"{task_id}: goal must be a concrete task question")
        if task.get("goal") is None and not allow_unknown_usage:
            errors.append(f"{task_id}: goal must be a concrete task question")
        task_role = task.get("task_role")
        if task_role is not None and task_role not in {"initial", "followup"}:
            errors.append(f"{task_id}: task_role must be initial or followup")
        if task_role is None and not allow_unknown_usage:
            errors.append(f"{task_id}: task_role must be initial or followup")
        if task.get("source_id") is not None and source_id not in source_ids:
            errors.append(f"{task_id}: references unknown source_id {source_id}")
        if task.get("source_id") is None and not allow_unknown_usage:
            errors.append(f"{task_id}: references unknown source_id {source_id}")
        if task.get("answer_correct") is not None and not isinstance(task.get("answer_correct"), bool):
            errors.append(f"{task_id}: answer_correct must be boolean")
        if task.get("answer_correct") is None and not allow_unknown_usage:
            errors.append(f"{task_id}: answer_correct is required for a strict evaluation")
        if task.get("citation_correct") is not None and not isinstance(task.get("citation_correct"), bool):
            errors.append(f"{task_id}: citation_correct must be boolean")
        if task.get("citation_correct") is None and not allow_unknown_usage:
            errors.append(f"{task_id}: citation_correct is required for a strict evaluation")
        reference_facts = task.get("reference_facts")
        if reference_facts is not None and (
            not isinstance(reference_facts, dict)
            or not reference_facts
            or any(not str(key).strip() or not str(value).strip() for key, value in (reference_facts or {}).items())
        ):
            errors.append(f"{task_id}: reference_facts must map fact IDs to non-empty reviewed claims")
        if reference_facts is None and not allow_unknown_usage:
            errors.append(f"{task_id}: reference_facts is required for a strict evaluation")
        for arm in ("envoy", "baseline"):
            claimed = task.get(f"{arm}_claimed_fact_ids")
            supported = task.get(f"{arm}_supported_fact_ids")
            citations = task.get(f"{arm}_citations")
            if claimed is not None and not _valid_id_list(claimed):
                errors.append(f"{task_id}: {arm}_claimed_fact_ids must be a unique list of fact IDs")
                claimed = []
            elif claimed is None:
                if not allow_unknown_usage:
                    errors.append(f"{task_id}: {arm}_claimed_fact_ids is required for a strict evaluation")
                claimed = []
            if supported is not None and not _valid_id_list(supported):
                errors.append(f"{task_id}: {arm}_supported_fact_ids must be a unique list of fact IDs")
                supported = []
            elif supported is None:
                if not allow_unknown_usage:
                    errors.append(f"{task_id}: {arm}_supported_fact_ids is required for a strict evaluation")
                supported = []
            if not set(supported).issubset(set(claimed)):
                errors.append(f"{task_id}: {arm} supported facts must be among claimed facts")
            reference_facts = task.get("reference_facts")
            known_fact_ids = set(reference_facts) if isinstance(reference_facts, dict) else set()
            if not set(supported).issubset(known_fact_ids):
                errors.append(f"{task_id}: {arm} supported facts must be in the reference fact set")
            if citations is not None and not isinstance(citations, list):
                errors.append(f"{task_id}: {arm}_citations must be a list")
                citations = []
            elif citations is None:
                if not allow_unknown_usage:
                    errors.append(f"{task_id}: {arm}_citations is required for a strict evaluation")
                citations = []
            for citation in citations:
                if not isinstance(citation, dict):
                    errors.append(f"{task_id}: {arm} citation record must be an object")
                    continue
                fact_id = citation.get("fact_id")
                locator = citation.get("locator")
                if fact_id not in set(claimed):
                    errors.append(f"{task_id}: {arm} citation references an unclaimed fact")
                if not isinstance(locator, (str, dict)) or not locator:
                    errors.append(f"{task_id}: {arm} citation locator must be non-empty")
                if not isinstance(citation.get("supports_fact"), bool):
                    errors.append(f"{task_id}: {arm} citation supports_fact must be reviewed boolean")
                if citation.get("supports_fact") and fact_id not in set(supported):
                    errors.append(f"{task_id}: {arm} supporting citation must map to a supported fact")
            arm_citation_correct = task.get(
                "citation_correct" if arm == "envoy" else "baseline_citation_correct"
            )
            if arm_citation_correct is True and not any(
                isinstance(item, dict)
                and item.get("supports_fact") is True
                and str(item.get("locator") or "").strip()
                for item in citations
            ) and arm_citation_correct is True:
                errors.append(f"{task_id}: {arm} citation_correct requires a reviewed supporting locator")
        for field in (
            "baseline_answer_correct",
            "baseline_citation_correct",
            "baseline_reused_material",
        ):
            value = task.get(field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"{task_id}: {field} must be boolean")
            elif value is None and not allow_unknown_usage:
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
            if task.get(field) is None and allow_unknown_usage:
                continue
            _require_non_negative(
                task, field, task_id, allow_unknown=allow_unknown_usage
            )
        baseline_method = task.get("baseline_method")
        if baseline_method is not None and baseline_method not in {
            "direct_host_read",
            "host_read_with_fair_cache",
            "host_native_media",
        }:
            errors.append(f"{task_id}: baseline_method must identify the comparison path")
        elif baseline_method is None and not allow_unknown_usage:
            errors.append(f"{task_id}: baseline_method must identify the comparison path")
        provider = str(task.get("provider") or "")
        model = str(task.get("model") or "")
        baseline_provider = str(task.get("baseline_provider") or "")
        baseline_model = str(task.get("baseline_model") or "")
        for arm, identity in (
            ("Envoy", (provider, model)),
            ("baseline", (baseline_provider, baseline_model)),
        ):
            for index, field_name in enumerate(("provider", "model")):
                value = identity[index]
                expected = run_identity[index]
                if (
                    value
                    and value.lower() not in unknown_markers
                    and expected
                    and expected.lower() not in unknown_markers
                    and value != expected
                ):
                    errors.append(f"{task_id}: {arm} must match run provider/model")
                if not allow_unknown_usage and value != expected:
                    errors.append(f"{task_id}: Envoy and baseline must match run provider/model")
        usage_status = task.get("usage_status")
        if usage_status is not None and usage_status not in {"actual", "unknown"}:
            errors.append(f"{task_id}: usage_status must be actual or unknown")
        if usage_status is None and not allow_unknown_usage:
            errors.append(f"{task_id}: usage_status must be actual or unknown")
        if usage_status != "actual" and not allow_unknown_usage:
            errors.append(f"{task_id}: Envoy arm requires actual provider usage")
        if usage_status == "actual":
            for field in ("input_tokens", "output_tokens"):
                _require_non_negative(
                    task, field, task_id
                )
        baseline_usage_status = task.get("baseline_usage_status")
        if baseline_usage_status is not None and baseline_usage_status not in {"actual", "unknown"}:
            errors.append(f"{task_id}: baseline_usage_status must be actual or unknown")
        if baseline_usage_status is None and not allow_unknown_usage:
            errors.append(f"{task_id}: baseline_usage_status must be actual or unknown")
        if baseline_usage_status != "actual" and not allow_unknown_usage:
            errors.append(f"{task_id}: baseline_usage_status must be actual")
        if baseline_usage_status == "actual":
            for field in ("baseline_input_tokens", "baseline_output_tokens"):
                _require_non_negative(
                    task, field, task_id
                )
        evidence_locators = task.get("evidence_locators")
        if evidence_locators is not None and not isinstance(evidence_locators, list):
            errors.append(f"{task_id}: evidence_locators must be a list")
        elif evidence_locators is None and not allow_unknown_usage:
            errors.append(f"{task_id}: evidence_locators must be a list")
        elif task.get("citation_correct") is True and not evidence_locators:
            errors.append(f"{task_id}: a correct citation requires at least one evidence locator")
        reused_material = task.get("reused_material")
        if reused_material is not None and not isinstance(reused_material, bool):
            errors.append(f"{task_id}: reused_material must be boolean")
        elif reused_material is None and not allow_unknown_usage:
            errors.append(f"{task_id}: reused_material must be boolean")

    for source_id in source_ids:
        source_tasks = tasks_by_source.get(source_id, [])
        roles = [task.get("task_role") for task in source_tasks]
        if (roles.count("initial") != 1 or roles.count("followup") < 1) and not allow_unknown_usage:
            errors.append(
                f"{source_id}: requires exactly one initial task and at least one followup task"
            )
        initial_goals = {
            str(task.get("goal") or "").strip().casefold()
            for task in source_tasks
            if task.get("task_role") == "initial"
        }
        if any(
            str(task.get("goal") or "").strip().casefold() in initial_goals
            for task in source_tasks
            if task.get("task_role") == "followup"
        ) and not allow_unknown_usage:
            errors.append(f"{source_id}: followup question must differ from the initial task")
        for task in source_tasks:
            if task.get("task_role") == "initial" and (
                task.get("reused_material") is True
                or task.get("baseline_reused_material") is True
            ):
                errors.append(f"{task.get('task_id')}: initial task cannot count as followup material reuse")
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
    answer_accuracy = _reviewed_accuracy(tasks, "answer_correct")
    baseline_answer_accuracy = _reviewed_accuracy(tasks, "baseline_answer_correct")
    citation_accuracy = _reviewed_accuracy(tasks, "citation_correct")
    baseline_citation_accuracy = _reviewed_accuracy(tasks, "baseline_citation_correct")
    envoy_fact_metrics = [
        _arm_fact_metrics(task, "envoy")
        for task in tasks
    ]
    baseline_fact_metrics = [
        _arm_fact_metrics(task, "baseline")
        for task in tasks
    ]
    metric_pairs = list(zip(envoy_fact_metrics, baseline_fact_metrics))
    metrics_complete = bool(metric_pairs) and all(
        envoy is not None and baseline is not None
        for envoy, baseline in metric_pairs
    )
    envoy_available = [item for item in envoy_fact_metrics if item is not None]
    baseline_available = [item for item in baseline_fact_metrics if item is not None]
    envoy_metrics_complete = len(envoy_available) == len(tasks) and bool(tasks)
    baseline_metrics_complete = len(baseline_available) == len(tasks) and bool(tasks)
    envoy_fact_recall = _mean(item[0] for item in envoy_available) if envoy_metrics_complete else None
    baseline_fact_recall = _mean(item[0] for item in baseline_available) if baseline_metrics_complete else None
    envoy_fact_precision = _mean(item[1] for item in envoy_available) if envoy_metrics_complete else None
    baseline_fact_precision = _mean(item[1] for item in baseline_available) if baseline_metrics_complete else None
    envoy_citation_support = _mean(item[2] for item in envoy_available) if envoy_metrics_complete else None
    baseline_citation_support = _mean(item[2] for item in baseline_available) if baseline_metrics_complete else None
    quality_reviewed_tasks = [
        task for task in tasks
        if isinstance(task.get("answer_correct"), bool)
        and isinstance(task.get("citation_correct"), bool)
        and isinstance(task.get("baseline_answer_correct"), bool)
        and isinstance(task.get("baseline_citation_correct"), bool)
    ]
    quality_inputs_complete = (
        len(quality_reviewed_tasks) == len(tasks)
        and bool(tasks)
        and metrics_complete
    )
    quality_non_inferior = None
    if quality_inputs_complete:
        quality_non_inferior = all(
            task["answer_correct"] >= task["baseline_answer_correct"]
            and task["citation_correct"] >= task["baseline_citation_correct"]
            and envoy_fact_metrics[index] is not None
            and baseline_fact_metrics[index] is not None
            and envoy_fact_metrics[index][0] >= baseline_fact_metrics[index][0]
            and envoy_fact_metrics[index][1] >= baseline_fact_metrics[index][1]
            and envoy_fact_metrics[index][2] >= baseline_fact_metrics[index][2]
            for index, task in enumerate(tasks)
        )
    envoy_input_tokens = _complete_sum(usage_actual, "input_tokens")
    baseline_input_tokens = _complete_sum(usage_actual, "baseline_input_tokens")
    savings_eligible = (
        len(usage_actual) == len(tasks)
        and quality_non_inferior is True
        and envoy_input_tokens is not None
        and baseline_input_tokens is not None
    )
    followups = [task for task in tasks if task.get("task_role") == "followup"]
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
        "answer_accuracy": answer_accuracy if run.get("quality_review_method") else None,
        "answer_reviewed_task_count": _reviewed_count(tasks, "answer_correct"),
        "baseline_answer_accuracy": baseline_answer_accuracy if run.get("quality_review_method") else None,
        "baseline_answer_reviewed_task_count": _reviewed_count(tasks, "baseline_answer_correct"),
        "citation_accuracy": citation_accuracy if run.get("quality_review_method") else None,
        "citation_reviewed_task_count": _reviewed_count(tasks, "citation_correct"),
        "baseline_citation_accuracy": baseline_citation_accuracy if run.get("quality_review_method") else None,
        "baseline_citation_reviewed_task_count": _reviewed_count(tasks, "baseline_citation_correct"),
        "fact_recall": envoy_fact_recall,
        "baseline_fact_recall": baseline_fact_recall,
        "fact_precision": envoy_fact_precision,
        "baseline_fact_precision": baseline_fact_precision,
        "citation_support_rate": envoy_citation_support,
        "baseline_citation_support_rate": baseline_citation_support,
        "quality_non_inferior": quality_non_inferior,
        "median_first_evidence_ms": _complete_median(tasks, "first_evidence_ms"),
        "baseline_median_first_evidence_ms": _complete_median(tasks, "baseline_first_evidence_ms"),
        "median_total_elapsed_ms": _complete_median(tasks, "total_elapsed_ms"),
        "baseline_median_total_elapsed_ms": _complete_median(tasks, "baseline_total_elapsed_ms"),
        "total_envoy_tool_calls": _complete_sum(tasks, "envoy_tool_calls"),
        "total_baseline_tool_calls": _complete_sum(tasks, "baseline_tool_calls"),
        "total_visual_inputs": _complete_sum(tasks, "visual_inputs"),
        "total_baseline_visual_inputs": _complete_sum(tasks, "baseline_visual_inputs"),
        "total_repeated_downloads": _complete_sum(tasks, "repeated_downloads"),
        "total_baseline_repeated_downloads": _complete_sum(tasks, "baseline_repeated_downloads"),
        "followup_reuse_rate": _reviewed_accuracy(followups, "reused_material"),
        "baseline_followup_reuse_rate": (
            _reviewed_accuracy(followups, "baseline_reused_material")
        ),
        "actual_usage_status": "paired_actual" if len(usage_actual) == len(tasks) else "incomplete",
        "actual_usage_task_count": len(usage_actual),
        "envoy_actual_input_tokens": (
            envoy_input_tokens if len(usage_actual) == len(tasks) else None
        ),
        "envoy_actual_output_tokens": (
            _complete_sum(usage_actual, "output_tokens")
            if len(usage_actual) == len(tasks) else None
        ),
        "baseline_actual_input_tokens": (
            baseline_input_tokens if len(usage_actual) == len(tasks) else None
        ),
        "baseline_actual_output_tokens": (
            _complete_sum(usage_actual, "baseline_output_tokens")
            if len(usage_actual) == len(tasks) else None
        ),
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
            allow_unknown_usage=args.allow_incomplete,
        )
        report = summarize(run, sources, tasks)
        release_eval_ready = (
            not errors
            and len(sources) >= MIN_SOURCES
            and len(tasks) >= MIN_TASKS
            and report["actual_usage_status"] == "paired_actual"
            and report["quality_non_inferior"]
            and run.get("quality_review_method") in {"dual_human", "reference_rubric"}
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
