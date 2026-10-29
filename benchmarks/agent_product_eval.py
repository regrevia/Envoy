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
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import random
import re
import sys
from datetime import datetime
from typing import Any
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
    "cache_isolation_status",
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

USAGE_BREAKDOWN_FIELDS = {
    "envoy": (
        "usage_breakdown_status",
        "uncached_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "input_tokens",
    ),
    "baseline": (
        "baseline_usage_breakdown_status",
        "baseline_uncached_input_tokens",
        "baseline_cache_read_input_tokens",
        "baseline_cache_creation_input_tokens",
        "baseline_input_tokens",
    ),
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


def _complete_percentile(items: list[dict], field: str, probability: float) -> float | None:
    values = [item.get(field) for item in items]
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        return None
    return round(_percentile(sorted(float(value) for value in values), probability), 3)


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
    task_package_schema_version = run.get("task_package_schema_version")
    if task_package_schema_version not in {None, "envoy.task-package/1"}:
        errors.append("run: unsupported task_package_schema_version")
    package_schema = task_package_schema_version == "envoy.task-package/1"
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
    cache_isolation_status = run.get("cache_isolation_status")
    if cache_isolation_status is None and not allow_unknown_usage:
        errors.append("run: cache_isolation_status is required for a strict evaluation")
    elif cache_isolation_status is not None and cache_isolation_status not in {
        "verified_disabled",
        "verified_isolated",
        "verified_per_request_salt",
        "per_request_salt",
        "cache_confounded",
        "unknown",
    }:
        errors.append(
            "run: cache_isolation_status must be verified_disabled, "
            "verified_isolated, verified_per_request_salt, cache_confounded, or unknown"
        )
    if cache_isolation_status == "verified_per_request_salt":
        evidence = run.get("cache_isolation_evidence")
        if not isinstance(evidence, dict):
            errors.append(
                "run: verified_per_request_salt requires cache_isolation_evidence from the validated server ledger"
            )
        else:
            report_hash = evidence.get("ledger_report_sha256")
            counts = (
                evidence.get("server_request_count"),
                evidence.get("salt_hash_count"),
                evidence.get("unique_salt_hash_count"),
            )
            if (
                evidence.get("verified") is not True
                or evidence.get("status") != "verified_per_request_salt"
                or evidence.get("cache_read_input_tokens") != 0
                or evidence.get("cache_read_count_complete") is not True
                or not isinstance(report_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", report_hash)
                or not all(isinstance(value, int) and value > 0 for value in counts)
                or len(set(counts)) != 1
            ):
                errors.append(
                    "run: cache_isolation_evidence must reference a validated report with complete unique salt hashes"
                )
    elif cache_isolation_status == "per_request_salt" and not allow_unknown_usage:
        errors.append(
            "run: per_request_salt is a planned policy; use verified_per_request_salt only after ledger validation"
        )
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
    tasks_by_package: dict[str, list[dict]] = defaultdict(list)
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
        if package_schema:
            package_id = str(task.get("package_id") or "")
            tasks_by_package[package_id].append(task)
            declared_sources = task.get("source_ids")
            if not _valid_id_list(declared_sources) or len(declared_sources) < 2:
                errors.append(
                    f"{task_id}: package source_ids must list at least two unique source IDs"
                )
                declared_sources = []
            elif not set(declared_sources).issubset(source_ids):
                errors.append(f"{task_id}: package source_ids contains an unknown source")
            if source_id and source_id not in set(declared_sources):
                errors.append(f"{task_id}: source_id must be included in package source_ids")
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
        for arm, fields in USAGE_BREAKDOWN_FIELDS.items():
            status_field, uncached_field, cache_read_field, cache_creation_field, total_field = fields
            breakdown_status = task.get(status_field, "unknown")
            if breakdown_status not in {"reported", "not_exposed", "unknown"}:
                errors.append(
                    f"{task_id}: {status_field} must be reported, not_exposed, or unknown"
                )
                continue
            component_fields = (
                uncached_field,
                cache_read_field,
                cache_creation_field,
            )
            component_values = [task.get(field) for field in component_fields]
            if breakdown_status == "reported":
                if task.get(
                    "usage_status" if arm == "envoy" else "baseline_usage_status"
                ) != "actual":
                    errors.append(f"{task_id}: {status_field}=reported requires actual usage")
                for field in component_fields:
                    _require_non_negative(task, field, task_id)
                if all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in component_values
                ):
                    reported_total = sum(component_values)
                    total_input = task.get(total_field)
                    if (
                        isinstance(total_input, int)
                        and not isinstance(total_input, bool)
                        and reported_total != total_input
                    ):
                        errors.append(
                            f"{task_id}: {status_field} categories must sum to {total_field}"
                        )
            elif any(value is not None for value in component_values):
                errors.append(
                    f"{task_id}: {status_field}={breakdown_status} cannot include token categories"
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
        if package_schema:
            continue
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
    if package_schema:
        if not tasks_by_package:
            errors.append("task package schema requires package_id on every task")
        for package_id, package_tasks in tasks_by_package.items():
            if not package_id:
                errors.append("task package schema requires non-empty package_id")
                continue
            source_ids_in_package = {
                str(task.get("source_id") or "")
                for task in package_tasks
            }
            declared_source_ids = set()
            for task in package_tasks:
                raw = task.get("source_ids")
                if not isinstance(raw, list):
                    errors.append(f"{package_id}: source_ids must be a list")
                    continue
                declared_source_ids.update(str(item) for item in raw)
            if len(source_ids_in_package | declared_source_ids) < 2:
                errors.append(f"{package_id} requires at least two distinct source_ids")
            roles = [task.get("task_role") for task in package_tasks]
            if roles.count("initial") != 1 or roles.count("followup") != 2:
                errors.append(
                    f"{package_id} requires exactly one initial and two followup tasks"
                )
            if len(package_tasks) != 3:
                errors.append(f"{package_id} requires exactly three scripted turns")
            source_lists = {
                tuple(sorted(str(item) for item in task.get("source_ids", [])))
                for task in package_tasks
                if isinstance(task.get("source_ids"), list)
            }
            if len(source_lists) > 1:
                errors.append(f"{package_id}: source_ids must remain fixed across its three turns")
            for task in package_tasks:
                task_id = str(task.get("task_id") or "<unknown-task>")
                for field in ("envoy_task_outcome", "baseline_task_outcome"):
                    if task.get(field) not in {"completed", "partial", "blocked", "failed"}:
                        errors.append(
                            f"{task_id}: {field} must be completed, partial, blocked, or failed"
                        )
                for field in (
                    "human_interventions",
                    "baseline_human_interventions",
                    "human_intervention_minutes",
                    "baseline_human_intervention_minutes",
                ):
                    _require_non_negative(task, field, task_id)
                for arm, status_field, cost_field in (
                    ("Envoy", "server_cost_status", "server_reported_cost"),
                    ("baseline", "baseline_server_cost_status", "baseline_server_reported_cost"),
                ):
                    cost_status = task.get(status_field)
                    if cost_status not in {"reported", "not_exposed", "unknown"}:
                        errors.append(
                            f"{task_id}: {status_field} must be reported, not_exposed, or unknown"
                        )
                    elif cost_status == "reported":
                        _require_non_negative(task, cost_field, task_id)
                    elif task.get(cost_field) is not None:
                        errors.append(
                            f"{task_id}: {status_field}={cost_status} cannot include {cost_field}"
                        )
    if require_minimum and len(sources) < MIN_SOURCES:
        errors.append(f"minimum source gate is {MIN_SOURCES}; got {len(sources)}")
    if require_minimum and len(tasks) < MIN_TASKS:
        errors.append(f"minimum task gate is {MIN_TASKS}; got {len(tasks)}")
    return errors


def _bootstrap_metric_values(tasks: list[dict]) -> dict[str, float | None]:
    values: dict[str, float | None] = {}

    def paired_mean_delta(envoy_field: str, baseline_field: str) -> float | None:
        pairs = [(task.get(envoy_field), task.get(baseline_field)) for task in tasks]
        if not pairs or any(not isinstance(a, bool) or not isinstance(b, bool) for a, b in pairs):
            return None
        return sum(int(a) - int(b) for a, b in pairs) / len(pairs)

    for name, envoy_field, baseline_field in (
        ("answer_accuracy_delta", "answer_correct", "baseline_answer_correct"),
        ("citation_accuracy_delta", "citation_correct", "baseline_citation_correct"),
    ):
        delta = paired_mean_delta(envoy_field, baseline_field)
        values[name] = delta * 100 if delta is not None else None

    fact_pairs = [
        (_arm_fact_metrics(task, "envoy"), _arm_fact_metrics(task, "baseline"))
        for task in tasks
    ]
    for name, metric_index in (
        ("fact_recall_delta", 0),
        ("fact_precision_delta", 1),
        ("citation_support_delta", 2),
    ):
        if fact_pairs and all(envoy is not None and baseline is not None for envoy, baseline in fact_pairs):
            values[name] = (
                sum(envoy[metric_index] - baseline[metric_index] for envoy, baseline in fact_pairs)
                / len(fact_pairs)
                * 100
            )
        else:
            values[name] = None

    if tasks and all(
        task.get("usage_status") == "actual"
        and task.get("baseline_usage_status") == "actual"
        and isinstance(task.get("input_tokens"), int)
        and not isinstance(task.get("input_tokens"), bool)
        and isinstance(task.get("baseline_input_tokens"), int)
        and not isinstance(task.get("baseline_input_tokens"), bool)
        for task in tasks
    ):
        baseline_tokens = sum(task["baseline_input_tokens"] for task in tasks)
        values["input_token_savings_fraction"] = (
            (baseline_tokens - sum(task["input_tokens"] for task in tasks)) / baseline_tokens
            if baseline_tokens > 0
            else None
        )
    else:
        values["input_token_savings_fraction"] = None

    if tasks and all(
        task.get("usage_status") == "actual"
        and task.get("baseline_usage_status") == "actual"
        and task.get("usage_breakdown_status") == "reported"
        and task.get("baseline_usage_breakdown_status") == "reported"
        and all(
            isinstance(task.get(field), int)
            and not isinstance(task.get(field), bool)
            for field in (
                "uncached_input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "baseline_uncached_input_tokens",
                "baseline_cache_read_input_tokens",
                "baseline_cache_creation_input_tokens",
            )
        )
        for task in tasks
    ):
        envoy_exposure = sum(
            task["uncached_input_tokens"]
            + task["cache_read_input_tokens"]
            + task["cache_creation_input_tokens"]
            for task in tasks
        )
        baseline_exposure = sum(
            task["baseline_uncached_input_tokens"]
            + task["baseline_cache_read_input_tokens"]
            + task["baseline_cache_creation_input_tokens"]
            for task in tasks
        )
        values["input_exposure_savings_fraction"] = (
            (baseline_exposure - envoy_exposure) / baseline_exposure
            if baseline_exposure > 0
            else None
        )
    else:
        values["input_exposure_savings_fraction"] = None

    for name, envoy_field, baseline_field in (
        ("first_evidence_reduction_fraction", "first_evidence_ms", "baseline_first_evidence_ms"),
        ("total_elapsed_reduction_fraction", "total_elapsed_ms", "baseline_total_elapsed_ms"),
    ):
        envoy_median = _complete_median(tasks, envoy_field)
        baseline_median = _complete_median(tasks, baseline_field)
        values[name] = (
            (baseline_median - envoy_median) / baseline_median
            if envoy_median is not None and baseline_median is not None and baseline_median > 0
            else None
        )
    return values


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _paired_cluster_bootstrap(
    run: dict,
    tasks: list[dict],
    *,
    repetitions: int = 2000,
    cluster_field: str = "source_id",
) -> dict[str, Any]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        clusters[str(task.get(cluster_field) or "")].append(task)
    cluster_ids = sorted(key for key in clusters if key)
    observed = _bootstrap_metric_values(tasks)
    output: dict[str, Any] = {}
    if len(cluster_ids) < 2:
        return {
            "cluster_unit": cluster_field,
            "cluster_count": len(cluster_ids),
            "replicates": repetitions,
            "confidence_level": 0.95,
            "metrics": {
                name: None
                for name in observed
            },
        }

    seed_material = f"{run.get('run_id', '')}\0{run.get('envoy_commit', '')}".encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    rng = random.Random(seed)
    distributions: dict[str, list[float]] = {name: [] for name in observed}
    for _ in range(repetitions):
        selected = [
            cluster_ids[rng.randrange(len(cluster_ids))]
            for _ in cluster_ids
        ]
        sampled_tasks = [
            task
            for cluster_id in selected
            for task in clusters[cluster_id]
        ]
        sample_values = _bootstrap_metric_values(sampled_tasks)
        for name, value in sample_values.items():
            if value is not None:
                distributions[name].append(value)
    for name, estimate in observed.items():
        draws = sorted(distributions[name])
        output[name] = (
            {
                "estimate": round(estimate, 6),
                "lower": round(_percentile(draws, 0.025), 6),
                "upper": round(_percentile(draws, 0.975), 6),
                "valid_replicates": len(draws),
            }
            if estimate is not None and len(draws) == repetitions
            else None
        )
    return {
        "cluster_unit": cluster_field,
        "cluster_count": len(cluster_ids),
        "replicates": repetitions,
        "confidence_level": 0.95,
        "metrics": output,
    }


def summarize(run: dict, sources: list[dict], tasks: list[dict]) -> dict:
    package_schema = run.get("task_package_schema_version") == "envoy.task-package/1"
    cluster_field = "package_id" if package_schema else "source_id"
    if package_schema:
        packages: dict[str, list[dict]] = defaultdict(list)
        for task in tasks:
            packages[str(task.get("package_id") or "")].append(task)
        envoy_package_outcomes = [
            all(task.get("envoy_task_outcome") == "completed" for task in package_tasks)
            for package_tasks in packages.values()
        ]
        baseline_package_outcomes = [
            all(task.get("baseline_task_outcome") == "completed" for task in package_tasks)
            for package_tasks in packages.values()
        ]
        envoy_package_completion_rate = (
            sum(envoy_package_outcomes) / len(envoy_package_outcomes)
            if envoy_package_outcomes else None
        )
        baseline_package_completion_rate = (
            sum(baseline_package_outcomes) / len(baseline_package_outcomes)
            if baseline_package_outcomes else None
        )
        human_intervention_task_count = sum(
            int(task["human_interventions"]) > 0 for task in tasks
        )
        baseline_human_intervention_task_count = sum(
            int(task["baseline_human_interventions"]) > 0 for task in tasks
        )
        human_intervention_package_count = sum(
            any(int(task["human_interventions"]) > 0 for task in package_tasks)
            for package_tasks in packages.values()
        )
        baseline_human_intervention_package_count = sum(
            any(int(task["baseline_human_interventions"]) > 0 for task in package_tasks)
            for package_tasks in packages.values()
        )
        total_human_intervention_minutes = sum(
            float(task["human_intervention_minutes"]) for task in tasks
        )
        baseline_total_human_intervention_minutes = sum(
            float(task["baseline_human_intervention_minutes"]) for task in tasks
        )
        envoy_cost_tasks = [
            task for task in tasks if task.get("server_cost_status") == "reported"
        ]
        baseline_cost_tasks = [
            task for task in tasks if task.get("baseline_server_cost_status") == "reported"
        ]
        envoy_server_reported_cost = (
            _complete_sum(envoy_cost_tasks, "server_reported_cost")
            if len(envoy_cost_tasks) == len(tasks) and tasks else None
        )
        baseline_server_reported_cost = (
            _complete_sum(baseline_cost_tasks, "baseline_server_reported_cost")
            if len(baseline_cost_tasks) == len(tasks) and tasks else None
        )
        cost_status_values = [
            task.get("server_cost_status") for task in tasks
        ] + [
            task.get("baseline_server_cost_status") for task in tasks
        ]
        if all(value == "reported" for value in cost_status_values) and tasks:
            server_cost_status = "reported"
        elif any(value == "reported" for value in cost_status_values):
            server_cost_status = "partial"
        elif cost_status_values and all(value == "not_exposed" for value in cost_status_values):
            server_cost_status = "not_exposed"
        else:
            server_cost_status = "unknown"
    else:
        packages = defaultdict(list)
        envoy_package_completion_rate = None
        baseline_package_completion_rate = None
        human_intervention_task_count = None
        baseline_human_intervention_task_count = None
        human_intervention_package_count = None
        baseline_human_intervention_package_count = None
        total_human_intervention_minutes = None
        baseline_total_human_intervention_minutes = None
        envoy_server_reported_cost = None
        baseline_server_reported_cost = None
        server_cost_status = "unknown"
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
    paired_bootstrap = _paired_cluster_bootstrap(
        run,
        tasks,
        cluster_field=cluster_field,
    )
    source_class_by_id = {
        str(source["source_id"]): str(source["source_class"])
        for source in sources
    }
    stratified_tasks: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        source_class = source_class_by_id.get(str(task.get("source_id") or ""))
        if source_class:
            stratified_tasks[source_class].append(task)
    stratified_bootstrap = {
        source_class: _paired_cluster_bootstrap(
            run,
            class_tasks,
            cluster_field=cluster_field,
        )
        for source_class, class_tasks in sorted(stratified_tasks.items())
    }
    source_class_task_counts = {
        source_class: len(class_tasks)
        for source_class, class_tasks in sorted(stratified_tasks.items())
    }
    token_savings_interval = paired_bootstrap["metrics"]["input_token_savings_fraction"]
    exposure_savings_interval = paired_bootstrap["metrics"]["input_exposure_savings_fraction"]
    cache_isolation_verified = run.get("cache_isolation_status") in {
        "verified_disabled",
        "verified_isolated",
    }
    salt_evidence = run.get("cache_isolation_evidence")
    if (
        run.get("cache_isolation_status") == "verified_per_request_salt"
        and isinstance(salt_evidence, dict)
    ):
        salt_counts = (
            salt_evidence.get("server_request_count"),
            salt_evidence.get("salt_hash_count"),
            salt_evidence.get("unique_salt_hash_count"),
        )
        cache_isolation_verified = (
            salt_evidence.get("verified") is True
            and salt_evidence.get("status") == "verified_per_request_salt"
            and salt_evidence.get("cache_read_input_tokens") == 0
            and salt_evidence.get("cache_read_count_complete") is True
            and isinstance(salt_evidence.get("ledger_report_sha256"), str)
            and re.fullmatch(
                r"[0-9a-f]{64}",
                salt_evidence["ledger_report_sha256"],
            ) is not None
            and all(isinstance(value, int) and value > 0 for value in salt_counts)
            and len(set(salt_counts)) == 1
        )
    savings_eligible = (
        len(usage_actual) == len(tasks)
        and quality_non_inferior is True
        and envoy_input_tokens is not None
        and baseline_input_tokens is not None
        and cache_isolation_verified
    )
    usage_breakdown_tasks = [
        task for task in tasks
        if task.get("usage_status") == "actual"
        and task.get("baseline_usage_status") == "actual"
        and task.get("usage_breakdown_status") == "reported"
        and task.get("baseline_usage_breakdown_status") == "reported"
    ]
    usage_breakdown_complete = bool(tasks) and len(usage_breakdown_tasks) == len(tasks)
    envoy_uncached_tokens = (
        _complete_sum(usage_breakdown_tasks, "uncached_input_tokens")
        if usage_breakdown_complete else None
    )
    envoy_cache_read_tokens = (
        _complete_sum(usage_breakdown_tasks, "cache_read_input_tokens")
        if usage_breakdown_complete else None
    )
    envoy_cache_creation_tokens = (
        _complete_sum(usage_breakdown_tasks, "cache_creation_input_tokens")
        if usage_breakdown_complete else None
    )
    baseline_uncached_tokens = (
        _complete_sum(usage_breakdown_tasks, "baseline_uncached_input_tokens")
        if usage_breakdown_complete else None
    )
    baseline_cache_read_tokens = (
        _complete_sum(usage_breakdown_tasks, "baseline_cache_read_input_tokens")
        if usage_breakdown_complete else None
    )
    baseline_cache_creation_tokens = (
        _complete_sum(usage_breakdown_tasks, "baseline_cache_creation_input_tokens")
        if usage_breakdown_complete else None
    )
    envoy_input_exposure = (
        envoy_uncached_tokens + envoy_cache_read_tokens + envoy_cache_creation_tokens
        if usage_breakdown_complete else None
    )
    baseline_input_exposure = (
        baseline_uncached_tokens + baseline_cache_read_tokens + baseline_cache_creation_tokens
        if usage_breakdown_complete else None
    )
    breakdown_statuses = [
        task.get("usage_breakdown_status", "unknown")
        for task in tasks
    ] + [
        task.get("baseline_usage_breakdown_status", "unknown")
        for task in tasks
    ]
    if usage_breakdown_complete:
        usage_breakdown_status = "reported"
    elif any(status == "reported" for status in breakdown_statuses):
        usage_breakdown_status = "partial"
    elif breakdown_statuses and all(status == "not_exposed" for status in breakdown_statuses):
        usage_breakdown_status = "not_exposed"
    else:
        usage_breakdown_status = "unknown"
    input_exposure_savings = (
        exposure_savings_interval["estimate"]
        if (
            savings_eligible
            and usage_breakdown_complete
            and exposure_savings_interval is not None
            and exposure_savings_interval["lower"] > 0
        )
        else None
    )
    followups = [task for task in tasks if task.get("task_role") == "followup"]
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_scope": "real sources and host-agent tasks supplied by evaluator",
        "task_package_schema_version": (
            run.get("task_package_schema_version")
            if package_schema else None
        ),
        "package_count": (
            len({str(task.get("package_id") or "") for task in tasks})
            if package_schema else None
        ),
        "envoy_package_completion_rate": envoy_package_completion_rate,
        "baseline_package_completion_rate": baseline_package_completion_rate,
        "human_intervention_task_count": human_intervention_task_count,
        "baseline_human_intervention_task_count": baseline_human_intervention_task_count,
        "human_intervention_package_rate": (
            human_intervention_package_count / len(packages)
            if human_intervention_package_count is not None and packages else None
        ),
        "baseline_human_intervention_package_rate": (
            baseline_human_intervention_package_count / len(packages)
            if baseline_human_intervention_package_count is not None and packages else None
        ),
        "total_human_intervention_minutes": total_human_intervention_minutes,
        "baseline_total_human_intervention_minutes": baseline_total_human_intervention_minutes,
        "server_cost_status": server_cost_status,
        "envoy_server_reported_cost": envoy_server_reported_cost,
        "baseline_server_reported_cost": baseline_server_reported_cost,
        "run": run,
        "source_count": len(sources),
        "task_count": len(tasks),
        "minimum_source_gate": MIN_SOURCES,
        "minimum_task_gate": MIN_TASKS,
        "scale_gate_status": "passed" if len(sources) >= MIN_SOURCES and len(tasks) >= MIN_TASKS else "incomplete",
        # The release decision is reported as four independent layers (filled by
        # the CLI): protocol_valid, data_complete, quality_reviewed,
        # claim_eligible. They are kept unset here so a caller of summarize can
        # tell "not evaluated yet" apart from a genuine false.
        "gate_status": "pending_validation",
        "protocol_valid": None,
        "data_complete": None,
        "quality_reviewed": None,
        "claim_eligible": None,
        "claim_blockers": [],
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
        "paired_bootstrap": paired_bootstrap,
        "stratified_bootstrap": stratified_bootstrap,
        "source_class_task_counts": source_class_task_counts,
        "input_token_savings_ci_support": bool(
            token_savings_interval is not None
            and cache_isolation_verified
            and token_savings_interval["lower"] > 0
        ),
        "token_savings_eligible": savings_eligible,
        "cache_isolation_verified": cache_isolation_verified,
        "input_exposure_savings_ci_support": bool(
            exposure_savings_interval is not None
            and cache_isolation_verified
            and exposure_savings_interval["lower"] > 0
        ),
        "cache_isolation_status": run.get("cache_isolation_status", "unknown"),
        "usage_breakdown_status": usage_breakdown_status,
        "usage_breakdown_task_count": len(usage_breakdown_tasks),
        "envoy_uncached_input_tokens": envoy_uncached_tokens if usage_breakdown_complete else None,
        "envoy_cache_read_input_tokens": envoy_cache_read_tokens if usage_breakdown_complete else None,
        "envoy_cache_creation_input_tokens": envoy_cache_creation_tokens if usage_breakdown_complete else None,
        "baseline_uncached_input_tokens": baseline_uncached_tokens if usage_breakdown_complete else None,
        "baseline_cache_read_input_tokens": baseline_cache_read_tokens if usage_breakdown_complete else None,
        "baseline_cache_creation_input_tokens": baseline_cache_creation_tokens if usage_breakdown_complete else None,
        "envoy_input_exposure_tokens": envoy_input_exposure,
        "baseline_input_exposure_tokens": baseline_input_exposure,
        "input_exposure_savings_fraction": input_exposure_savings,
        "median_first_evidence_ms": _complete_median(tasks, "first_evidence_ms"),
        "baseline_median_first_evidence_ms": _complete_median(tasks, "baseline_first_evidence_ms"),
        "median_total_elapsed_ms": _complete_median(tasks, "total_elapsed_ms"),
        "baseline_median_total_elapsed_ms": _complete_median(tasks, "baseline_total_elapsed_ms"),
        "p90_total_elapsed_ms": _complete_percentile(tasks, "total_elapsed_ms", 0.9),
        "baseline_p90_total_elapsed_ms": _complete_percentile(tasks, "baseline_total_elapsed_ms", 0.9),
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


def _markdown_number(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if percent:
        return f"{float(value) * 100:.2f}%"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _markdown_reduction(baseline: Any, envoy: Any) -> str:
    if (
        not isinstance(baseline, (int, float))
        or isinstance(baseline, bool)
        or baseline <= 0
        or not isinstance(envoy, (int, float))
        or isinstance(envoy, bool)
    ):
        return "N/A"
    delta = (baseline - envoy) / baseline * 100
    return f"减少 {delta:.2f}%" if delta >= 0 else f"增加 {abs(delta):.2f}%"


def _markdown_pp_change(baseline: Any, envoy: Any) -> str:
    if not isinstance(baseline, (int, float)) or not isinstance(envoy, (int, float)):
        return "N/A"
    delta = (envoy - baseline) * 100
    return f"{delta:+.2f} 个百分点"


def _markdown_count_change(baseline: Any, envoy: Any) -> str:
    if (
        not isinstance(baseline, (int, float))
        or isinstance(baseline, bool)
        or not isinstance(envoy, (int, float))
        or isinstance(envoy, bool)
    ):
        return "N/A"
    delta = int(envoy) - int(baseline)
    return "0" if delta == 0 else f"{delta:+,}"


def render_markdown(report: dict[str, Any]) -> str:
    strict_gate = report.get("gate_status") == "passed"
    rows = [
        ("答案正确率", "baseline_answer_accuracy", "answer_accuracy", "quality"),
        ("事实召回", "baseline_fact_recall", "fact_recall", "quality"),
        ("引用支持率", "baseline_citation_support_rate", "citation_support_rate", "quality"),
        ("成功完成 package", "baseline_package_completion_rate", "envoy_package_completion_rate", "quality"),
        ("输入 token", "baseline_actual_input_tokens", "envoy_actual_input_tokens", "efficiency"),
        ("输入 token 暴露量", "baseline_input_exposure_tokens", "envoy_input_exposure_tokens", "exposure"),
        ("未缓存输入 token", "baseline_uncached_input_tokens", "envoy_uncached_input_tokens", "efficiency"),
        ("缓存读取 token", "baseline_cache_read_input_tokens", "envoy_cache_read_input_tokens", "count"),
        ("缓存创建 token", "baseline_cache_creation_input_tokens", "envoy_cache_creation_input_tokens", "count"),
        ("输出 token", "baseline_actual_output_tokens", "envoy_actual_output_tokens", "efficiency"),
        ("服务端报告成本", "baseline_server_reported_cost", "envoy_server_reported_cost", "efficiency"),
        ("首次证据耗时中位数", "baseline_median_first_evidence_ms", "median_first_evidence_ms", "efficiency"),
        ("总耗时中位数", "baseline_median_total_elapsed_ms", "median_total_elapsed_ms", "efficiency"),
        ("总耗时 p90", "baseline_p90_total_elapsed_ms", "p90_total_elapsed_ms", "efficiency"),
        ("人工介入任务数", "baseline_human_intervention_task_count", "human_intervention_task_count", "count"),
        ("人工介入 package 比例", "baseline_human_intervention_package_rate", "human_intervention_package_rate", "quality"),
        ("人工介入分钟", "baseline_total_human_intervention_minutes", "total_human_intervention_minutes", "count"),
        ("视觉输入数", "total_baseline_visual_inputs", "total_visual_inputs", "count"),
        ("重复下载数", "total_baseline_repeated_downloads", "total_repeated_downloads", "count"),
    ]
    lines = [
        "| 指标 | Native baseline | Envoy | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, baseline_key, envoy_key, kind in rows:
        baseline = report.get(baseline_key)
        envoy = report.get(envoy_key)
        if kind == "quality":
            change = _markdown_pp_change(baseline, envoy)
            lines.append(
                f"| {label} | {_markdown_number(baseline, percent=True)} "
                f"| {_markdown_number(envoy, percent=True)} | {change} |"
            )
        elif kind == "count":
            lines.append(
                f"| {label} | {_markdown_number(baseline)} "
                f"| {_markdown_number(envoy)} | {_markdown_count_change(baseline, envoy)} |"
            )
        elif kind == "exposure":
            change = (
                _markdown_reduction(baseline, envoy)
                if strict_gate
                and report.get("input_exposure_savings_fraction") is not None
                else "N/A"
            )
            lines.append(
                f"| {label} | {_markdown_number(baseline)} "
                f"| {_markdown_number(envoy)} | {change} |"
            )
        else:
            change = (
                _markdown_reduction(baseline, envoy)
                if strict_gate and report.get("token_savings_eligible")
                else "N/A"
            )
            lines.append(
                f"| {label} | {_markdown_number(baseline)} "
                f"| {_markdown_number(envoy)} | {change} |"
            )
    lines.extend([
        "",
        (
            f"配对任务：{report.get('task_count', 0)}；来源：{report.get('source_count', 0)}；"
            f"门槛：{report.get('gate_status', 'incomplete')}"
        ),
        (
            "输入 token 节省 95% CI："
            + (
                f"[{report['paired_bootstrap']['metrics']['input_token_savings_fraction']['lower'] * 100:.2f}%, "
                f"{report['paired_bootstrap']['metrics']['input_token_savings_fraction']['upper'] * 100:.2f}%]"
                if report.get("token_savings_eligible")
                and report.get("paired_bootstrap", {}).get("metrics", {}).get("input_token_savings_fraction")
                else "N/A"
            )
        ),
        (
            "实际 paired usage："
            f"{report.get('actual_usage_task_count', 0)}/{report.get('task_count', 0)}；"
            f"质量非劣：{report.get('quality_non_inferior')}"
        ),
        (
            "服务端缓存策略："
            f"{report.get('cache_isolation_status', 'unknown')}；"
            f"缓存分项：{report.get('usage_breakdown_status', 'unknown')} "
            f"({report.get('usage_breakdown_task_count', 0)}/{report.get('task_count', 0)} tasks)"
        ),
    ])
    if not strict_gate and report.get("gate_status") == "complete":
        lines.append("实验数据完整，但统计证据不支持效率结论；节省率不作结论。")
    elif not strict_gate:
        lines.append("当前数据未通过正式评测门槛，效率变化率不作结论。")
    strata = report.get("stratified_bootstrap") or {}
    if strata:
        lines.extend([
            "",
            "来源类型分层（source-clustered 95% CI）：",
            "| 来源类型 | 来源数 | 配对任务 | 输入 token 节省点估计 | 95% CI |",
            "| --- | ---: | ---: | ---: | ---: |",
        ])
        sources_by_class = report.get("source_classes") or {}
        # Task counts are included in each stratum below by summing from the
        # source records' task links in the structured report.
        tasks_by_class = report.get("source_class_task_counts") or {}
        for source_class, interval_report in sorted(strata.items()):
            token_interval = interval_report["metrics"].get("input_token_savings_fraction")
            if token_interval is None:
                estimate = "N/A"
                confidence_interval = "N/A"
            else:
                estimate = f"{token_interval['estimate'] * 100:.2f}%"
                confidence_interval = (
                    f"[{token_interval['lower'] * 100:.2f}%, "
                    f"{token_interval['upper'] * 100:.2f}%]"
                )
            lines.append(
                f"| {source_class} | {sources_by_class.get(source_class, 0)} "
                f"| {tasks_by_class.get(source_class, 0)} | {estimate} "
                f"| {confidence_interval} |"
            )
    return "\n".join(lines) + "\n"


# Review methods that count as the release-grade review of answer, citation and
# coverage claims. A run that only logs a weaker method (or no method at all)
# fails this layer instead of inheriting the old "reviewed unless proven
# otherwise" assumption.
RELEASE_QUALITY_REVIEW_METHODS = {"dual_human", "reference_rubric"}


def _source_records_complete(sources: list[dict]) -> list[str]:
    """Provenance blockers for the data layer.

    The ledger has no separate attempts table: one task record represents one
    attempt, and a source record represents the material that attempt ran on.
    A missing revision/timestamp/observed state means the provenance entry was
    never written, so the data layer stays false rather than inferring it.
    """
    blockers = []
    for source in sources:
        source_id = str(source.get("source_id") or "<unknown-source>")
        revision = source.get("source_revision")
        if not isinstance(revision, str) or not revision:
            blockers.append(f"source_provenance_missing:{source_id}")
        if not source.get("revalidated_at"):
            blockers.append(f"source_revalidation_missing:{source_id}")
        if source.get("accepted_state") not in {
            "accepted", "partial", "blocked", "unverified", "failed"
        }:
            blockers.append(f"source_state_missing:{source_id}")
    return blockers


def _task_order_blockers(sources: list[dict], tasks: list[dict]) -> list[str]:
    """Protocol layer: scripted turns must run initial -> followup per source."""
    blockers = []
    for source in sources:
        source_id = str(source.get("source_id") or "")
        roles = [
            task.get("task_role")
            for task in tasks
            if str(task.get("source_id") or "") == source_id
        ]
        if not roles:
            continue
        if "followup" in roles and "initial" not in roles:
            blockers.append(f"task_order_missing_initial:{source_id}")
        elif roles[0] != "initial":
            blockers.append(f"task_order_followup_first:{source_id}")
    return blockers


def _protocol_blockers(
    run: dict,
    sources: list[dict],
    tasks: list[dict],
    report: dict,
    errors: list[str],
) -> list[str]:
    """Layer 1: model/budget/order/tool environment match the pre-agreed protocol."""
    blockers = []
    if errors:
        blockers.append("protocol_validation_failed")
    if len(sources) < MIN_SOURCES:
        blockers.append(f"source_scale_below_minimum:{len(sources)}<{MIN_SOURCES}")
    if len(tasks) < MIN_TASKS:
        blockers.append(f"task_scale_below_minimum:{len(tasks)}<{MIN_TASKS}")
    if not report.get("cache_isolation_verified"):
        blockers.append("cache_isolation_not_verified")
    blockers.extend(_task_order_blockers(sources, tasks))
    # Model identity: every recorded arm must resolve to the run's agreed
    # provider/model. Unknown or missing identity is unverified, not "fine",
    # because a release claim needs a known model.
    unknown_markers = {"unknown", "draft", "not exposed"}
    run_identity = tuple(str(run.get(field) or "").strip() for field in ("provider", "model"))
    for task in tasks:
        task_id = str(task.get("task_id") or "<unknown-task>")
        for arm in ("envoy", "baseline"):
            prefix = "" if arm == "envoy" else "baseline_"
            identity = tuple(
                str(task.get(f"{prefix}{field}") or "").strip()
                for field in ("provider", "model")
            )
            if not all(identity) or any(value.lower() in unknown_markers for value in identity):
                blockers.append(f"model_identity_unverified:{arm}:{task_id}")
            elif identity != run_identity:
                blockers.append(f"model_identity_mismatch:{arm}:{task_id}")
    return blockers


def _data_blockers(sources: list[dict], tasks: list[dict]) -> list[str]:
    """Layer 2: attempts, failures, actual usage, artifacts and provenance recorded.

    "Attempts" here are the per-task ledger rows themselves: an attempt with
    unknown usage or without its artifacts/citations was not recorded, so it
    counts as a missing attempt rather than a passing one. A failed/blocked
    package attempt must still carry a recorded outcome.
    """
    blockers = _source_records_complete(sources)
    if not tasks:
        blockers.append("attempt_records_missing")
    for task in tasks:
        task_id = str(task.get("task_id") or "<unknown-task>")
        if task.get("usage_status") != "actual":
            blockers.append(f"usage_not_actual:envoy:{task_id}")
        if task.get("baseline_usage_status") != "actual":
            blockers.append(f"usage_not_actual:baseline:{task_id}")
        for arm in ("envoy", "baseline"):
            citations = task.get(f"{arm}_citations")
            if not isinstance(citations, list) or not citations:
                blockers.append(f"artifacts_missing:{arm}:{task_id}")
        if not isinstance(task.get("evidence_locators"), list) or not task["evidence_locators"]:
            blockers.append(f"artifacts_missing:evidence:{task_id}")
        if "envoy_task_outcome" in task and task.get("envoy_task_outcome") not in {
            "completed", "partial", "blocked", "failed"
        }:
            blockers.append(f"failure_record_missing:envoy:{task_id}")
        if "baseline_task_outcome" in task and task.get("baseline_task_outcome") not in {
            "completed", "partial", "blocked", "failed"
        }:
            blockers.append(f"failure_record_missing:baseline:{task_id}")
    return blockers


def _quality_blockers(run: dict, tasks: list[dict]) -> list[str]:
    """Layer 3: answer/citation/coverage claims got their required review.

    Reuses the existing human-rating and fact-coverage fields. Missing review
    fields are a blocker; they are never defaulted to reviewed.
    """
    blockers = []
    method = run.get("quality_review_method")
    if method not in RELEASE_QUALITY_REVIEW_METHODS:
        blockers.append(
            "quality_review_not_completed"
            + (f":{method}" if isinstance(method, str) and method else "")
        )
    unreviewed = [
        str(task.get("task_id") or "<unknown-task>")
        for task in tasks
        if any(_arm_fact_metrics(task, arm) is None for arm in ("envoy", "baseline"))
    ]
    for task_id in unreviewed:
        blockers.append(f"quality_review_inputs_missing:{task_id}")
    return blockers


def gate_layers(
    run: dict,
    sources: list[dict],
    tasks: list[dict],
    report: dict,
    errors: list[str],
) -> dict:
    """Split the single release gate into four independently reported layers.

    Old-field mapping (the fields below stay in the report unchanged):
    - ``validation_status`` / ``validation_errors``: machine schema check only.
      It feeds the protocol layer and is not the release decision itself.
    - ``scale_gate_status``: the source/task scale subset of the protocol layer.
    - ``quality_inputs_complete``-equivalent metrics: folded into
      ``quality_reviewed`` via ``answer_reviewed_task_count`` and the existing
      fact/citation review fields.
    - ``actual_usage_status``, ``usage_breakdown_status``: evidence inputs for
      ``data_complete``.
    - ``token_savings_eligible`` / ``input_token_savings_ci_support`` /
      ``quality_non_inferior``: still the statistical evidence, but they now
      only decide ``claim_eligible``; they no longer block an otherwise complete
      experiment.
    """
    protocol_blockers = _protocol_blockers(run, sources, tasks, report, errors)
    data_blockers = _data_blockers(sources, tasks)
    quality_blockers = _quality_blockers(run, tasks)
    claim_blockers = protocol_blockers + data_blockers + quality_blockers
    if not report.get("input_token_savings_ci_support"):
        claim_blockers.append("no_token_saving_signal")
    if report.get("quality_non_inferior") is not True:
        # A token saving that comes with a quality regression is not a claim the
        # run may make; the experiment itself can still be complete.
        claim_blockers.append("quality_non_inferior_not_supported")

    protocol_valid = not protocol_blockers
    data_complete = not data_blockers
    quality_reviewed = not quality_blockers
    claim_eligible = (
        protocol_valid
        and data_complete
        and quality_reviewed
        and report.get("quality_non_inferior") is True
        and bool(report.get("input_token_savings_ci_support"))
    )
    if errors:
        gate_status = "failed"
    elif not (protocol_valid and data_complete and quality_reviewed):
        # Incomplete means the experiment itself is not usable yet; a missing
        # improvement is not incompleteness, it only blocks the claim.
        gate_status = "incomplete"
    elif claim_eligible:
        gate_status = "passed"
    else:
        gate_status = "complete"
    return {
        "protocol_valid": protocol_valid,
        "data_complete": data_complete,
        "quality_reviewed": quality_reviewed,
        "claim_eligible": claim_eligible,
        "claim_blockers": sorted(set(claim_blockers)),
        "gate_status": gate_status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--markdown", action="store_true", help="render a concise comparison table")
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
        layers = gate_layers(run, sources, tasks, report, errors)
        report.update(layers)
        # Migration note: the old single release gate was equivalent to
        # protocol+data+quality+statistics all being true. The four layers above
        # keep that decision but report it per layer, so "no measured saving"
        # only removes claim eligibility and leaves the experiment complete.
        release_eval_ready = layers["claim_eligible"]
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
    if args.markdown:
        print(render_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
