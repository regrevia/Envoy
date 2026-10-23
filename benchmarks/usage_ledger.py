#!/usr/bin/env python3
"""Validate and aggregate redacted inference-server request usage records.

The ledger contains metadata only. It must never contain prompts, responses,
credentials, cookies, or authorization headers. One logical task turn may
map to multiple server requests; this tool preserves request-level IDs and
aggregates only after validating the mapping to a frozen paired plan.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any


FORBIDDEN_KEYS = {
    "prompt",
    "prompts",
    "messages",
    "response",
    "completion",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "cookies",
    "request_body",
    "response_body",
}
REQUIRED_REQUEST_FIELDS = {
    "record_type",
    "server_request_id",
    "run_id",
    "package_id",
    "arm",
    "session_id",
    "turn_correlation_id",
    "provider",
    "model",
    "started_at",
    "completed_at",
    "input_tokens",
    "output_tokens",
}
OPTIONAL_REQUEST_FIELDS = {
    "uncached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "server_cost",
    "server_cost_currency",
}
REQUIRED_TURN_FIELDS = {
    "record_type",
    "run_id",
    "package_id",
    "arm",
    "session_id",
    "turn_correlation_id",
    "turn_outcome",
    "server_request_ids",
    "started_at",
    "completed_at",
}
OPTIONAL_TURN_FIELDS = {
    "human_interventions",
    "human_intervention_minutes",
    "repeated_downloads",
    "visual_inputs",
}
TURN_OUTCOMES = {"completed", "partial", "blocked", "failed"}


def _iso(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _non_negative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _find_forbidden(value: Any, path: str = "") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = "".join(char for char in str(key).lower() if char.isalnum())
            if any(
                "".join(char for char in forbidden if char.isalnum()) in lowered
                for forbidden in FORBIDDEN_KEYS
            ):
                return f"forbidden sensitive field: {path + str(key)}"
            found = _find_forbidden(nested, f"{path}{key}.")
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden(nested, f"{path}{index}.")
            if found:
                return found
    return None


def load_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid plan JSON: {exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    if plan.get("schema_version") != "envoy.paired-experiment-plan/2":
        raise ValueError("plan must use envoy.paired-experiment-plan/2")
    return plan


def _expected_turns(plan: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    expected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for package in plan.get("packages", []):
        package_id = str(package.get("package_id") or "")
        for arm in ("baseline", "envoy"):
            session_id = str((package.get("session_ids") or {}).get(arm) or "")
            turn_ids = (package.get("turn_correlation_ids") or {}).get(arm) or {}
            if not session_id or set(turn_ids) != {"initial", "followup-1", "followup-2"}:
                raise ValueError(f"{package_id}/{arm}: incomplete session or turn correlation plan")
            for role, turn_id in turn_ids.items():
                key = (package_id, arm, str(turn_id))
                if key in expected:
                    raise ValueError(f"duplicate planned turn correlation ID: {turn_id}")
                expected[key] = {
                    "package_id": package_id,
                    "arm": arm,
                    "session_id": session_id,
                    "turn_role": role,
                }
    return expected


def aggregate_ledger(
    plan: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    server_export_complete: bool = False,
) -> dict[str, Any]:
    expected = _expected_turns(plan)
    seen_request_ids: set[str] = set()
    seen_turns: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    turn_summaries: dict[tuple[str, str, str], dict[str, Any]] = {}
    provider_model: set[tuple[str, str]] = set()

    for line_number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            errors.append(f"line {line_number}: record must be an object")
            continue
        forbidden = _find_forbidden(record)
        if forbidden:
            errors.append(f"line {line_number}: {forbidden}")
        record_type = record.get("record_type")
        required = (
            REQUIRED_TURN_FIELDS if record_type == "turn_summary"
            else REQUIRED_REQUEST_FIELDS if record_type == "request_usage"
            else set()
        )
        if not required:
            errors.append(f"line {line_number}: unsupported record_type")
            continue
        missing = required - record.keys()
        if missing:
            errors.append(f"line {line_number}: missing fields {sorted(missing)}")
            continue
        allowed = (
            REQUIRED_TURN_FIELDS | OPTIONAL_TURN_FIELDS
            if record_type == "turn_summary"
            else REQUIRED_REQUEST_FIELDS | OPTIONAL_REQUEST_FIELDS
        )
        extras = record.keys() - allowed
        if extras:
            errors.append(f"line {line_number}: unsupported fields {sorted(extras)}")
        if record["arm"] not in {"baseline", "envoy"}:
            errors.append(f"line {line_number}: arm must be baseline or envoy")
        if str(record["run_id"]) != str(plan.get("run_id")):
            errors.append(f"line {line_number}: run_id does not match frozen plan")
        if record_type == "turn_summary":
            turn_key = (
                str(record["package_id"]),
                str(record["arm"]),
                str(record["turn_correlation_id"]),
            )
            planned = expected.get(turn_key)
            if planned is None:
                errors.append(f"line {line_number}: turn_correlation_id is not in frozen plan")
                continue
            if turn_key in turn_summaries:
                errors.append(f"line {line_number}: duplicate turn_summary")
                continue
            if str(record["session_id"]) != planned["session_id"]:
                errors.append(f"line {line_number}: session_id does not match frozen plan")
            if record["turn_outcome"] not in TURN_OUTCOMES:
                errors.append(f"line {line_number}: turn_outcome is invalid")
            try:
                turn_started = _iso(record["started_at"], "started_at")
                turn_completed = _iso(record["completed_at"], "completed_at")
                if turn_completed < turn_started:
                    errors.append(f"line {line_number}: completed_at precedes started_at")
            except ValueError as exc:
                errors.append(f"line {line_number}: {exc}")
                continue
            for field in OPTIONAL_TURN_FIELDS & record.keys():
                if field in {"human_interventions", "human_intervention_minutes", "repeated_downloads", "visual_inputs"}:
                    try:
                        _non_negative(record[field], field)
                    except ValueError as exc:
                        errors.append(f"line {line_number}: {exc}")
            request_ids = record["server_request_ids"]
            if (
                not isinstance(request_ids, list)
                or any(not isinstance(value, str) or not value for value in request_ids)
                or len(request_ids) != len(set(request_ids))
            ):
                errors.append(f"line {line_number}: server_request_ids must be unique non-empty strings")
                continue
            turn_summaries[turn_key] = record
            seen_turns.add(turn_key)
            continue

        request_id = str(record["server_request_id"])
        if not request_id or request_id in seen_request_ids:
            errors.append(f"line {line_number}: duplicate or empty server_request_id")
        seen_request_ids.add(request_id)
        key = (
            str(record["package_id"]),
            str(record["arm"]),
            str(record["turn_correlation_id"]),
        )
        planned = expected.get(key)
        if planned is None:
            errors.append(f"line {line_number}: turn_correlation_id is not in frozen plan")
        else:
            if str(record["session_id"]) != planned["session_id"]:
                errors.append(f"line {line_number}: session_id does not match frozen plan")
        request_times_valid = True
        try:
            started = _iso(record["started_at"], "started_at")
            completed = _iso(record["completed_at"], "completed_at")
            if completed < started:
                errors.append(f"line {line_number}: completed_at precedes started_at")
        except ValueError as exc:
            errors.append(f"line {line_number}: {exc}")
            request_times_valid = False
        for field in ("input_tokens", "output_tokens"):
            try:
                _non_negative(record[field], field)
            except ValueError as exc:
                errors.append(f"line {line_number}: {exc}")
        for field in (
            "uncached_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            if field in record:
                try:
                    _non_negative(record[field], field)
                except ValueError as exc:
                    errors.append(f"line {line_number}: {exc}")
        if "server_cost" in record:
            cost = record["server_cost"]
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
                errors.append(f"line {line_number}: server_cost must be a non-negative number")
            if not isinstance(record.get("server_cost_currency"), str) or not record["server_cost_currency"].strip():
                errors.append(f"line {line_number}: server_cost requires server_cost_currency")
        elif "server_cost_currency" in record:
            errors.append(f"line {line_number}: server_cost_currency requires server_cost")
        provider_model.add((str(record["provider"]), str(record["model"])))
        if planned is not None and request_times_valid:
            grouped[key].append(record)

    missing_turns = sorted(set(expected) - seen_turns)
    if missing_turns:
        errors.append(f"missing turn summaries: {len(missing_turns)}")
    for key, summary in turn_summaries.items():
        declared_request_ids = set(summary["server_request_ids"])
        observed_request_ids = {
            str(row["server_request_id"])
            for row in grouped.get(key, [])
        }
        if declared_request_ids != observed_request_ids:
            missing_requests = sorted(declared_request_ids - observed_request_ids)
            orphan_requests = sorted(observed_request_ids - declared_request_ids)
            if missing_requests:
                errors.append(
                    f"{key[0]}/{key[1]}/{key[2]}: turn_summary declares missing server requests: "
                    + ", ".join(missing_requests)
                )
            if orphan_requests:
                errors.append(
                    f"{key[0]}/{key[1]}/{key[2]}: usage contains server requests "
                    "not declared by its turn_summary: "
                    + ", ".join(orphan_requests)
                )
        try:
            turn_started = _iso(summary["started_at"], "started_at")
            turn_completed = _iso(summary["completed_at"], "completed_at")
            for row in grouped.get(key, []):
                request_started = _iso(row["started_at"], "started_at")
                request_completed = _iso(row["completed_at"], "completed_at")
                if request_started < turn_started or request_completed > turn_completed:
                    errors.append(
                        f"{key[0]}/{key[1]}/{key[2]}: server request falls outside turn interval"
                    )
        except ValueError:
            pass
    if not server_export_complete:
        errors.append(
            "server_export_complete must be asserted only after the authoritative export window is verified complete"
        )
    if len(provider_model) > 1:
        errors.append("provider/model identity changed within usage ledger")

    turns = []
    turns_by_package_role: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for key in sorted(turn_summaries):
        rows = grouped.get(key, [])
        summary = turn_summaries[key]
        if rows:
            parsed_rows = []
            for row in rows:
                try:
                    parsed_rows.append((
                        _iso(row["started_at"], "started_at"),
                        _iso(row["completed_at"], "completed_at"),
                    ))
                except ValueError:
                    continue
            first = min((start for start, _ in parsed_rows), default=None)
            last = max((end for _, end in parsed_rows), default=None)
            input_tokens = sum(row["input_tokens"] for row in rows)
            output_tokens = sum(row["output_tokens"] for row in rows)
            usage_status = "actual"
            elapsed_ms = (
                int((last - first).total_seconds() * 1000)
                if first is not None and last is not None
                else None
            )
        else:
            first = last = None
            input_tokens = output_tokens = 0
            usage_status = "no_model_request"
            elapsed_ms = None
        item = expected[key]
        turn_started = _iso(summary["started_at"], "started_at")
        turn_completed = _iso(summary["completed_at"], "completed_at")
        aggregate = {
            **item,
            "turn_correlation_id": key[2],
            "turn_outcome": summary["turn_outcome"],
            "started_at": turn_started.isoformat(),
            "completed_at": turn_completed.isoformat(),
            "turn_elapsed_ms": int((turn_completed - turn_started).total_seconds() * 1000),
            "human_interventions": summary.get("human_interventions"),
            "human_intervention_minutes": summary.get("human_intervention_minutes"),
            "repeated_downloads": summary.get("repeated_downloads"),
            "visual_inputs": summary.get("visual_inputs"),
            "server_request_ids": sorted(str(row["server_request_id"]) for row in rows),
            "server_request_count": len(rows),
            "provider": rows[0]["provider"] if rows else None,
            "model": rows[0]["model"] if rows else None,
            "started_at": first.isoformat() if first else None,
            "completed_at": last.isoformat() if last else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usage_status": usage_status,
            "elapsed_ms": elapsed_ms,
            "server_cost_status": (
                "reported"
                if rows and all("server_cost" in row for row in rows)
                else "not_exposed" if rows
                else "unknown"
            ),
        }
        if rows and all("server_cost" in row for row in rows):
            aggregate["server_cost"] = sum(float(row["server_cost"]) for row in rows)
            currencies = {str(row["server_cost_currency"]) for row in rows}
            if len(currencies) != 1:
                errors.append(f"{key[0]}/{key[1]}/{key[2]}: server cost currency changed within turn")
            else:
                aggregate["server_cost_currency"] = next(iter(currencies))
        cache_fields = (
            "uncached_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
        if rows and all(field in row for row in rows for field in cache_fields):
            for field in cache_fields:
                aggregate[field] = sum(row[field] for row in rows)
            if sum(aggregate[field] for field in cache_fields) != aggregate["input_tokens"]:
                errors.append(
                    f"{key[0]}/{key[1]}/{key[2]}: cache token categories do not sum to input_tokens"
                )
            aggregate["usage_breakdown_status"] = "reported"
        elif rows:
            aggregate["usage_breakdown_status"] = "not_exposed"
        else:
            aggregate["usage_breakdown_status"] = "unknown"
        turns.append(aggregate)
        turns_by_package_role[(key[0], str(item["turn_role"]))][key[1]] = aggregate

    paired_usage_rows = []
    for (package_id, turn_role), arms in sorted(turns_by_package_role.items()):
        if set(arms) != {"baseline", "envoy"}:
            errors.append(f"{package_id}/{turn_role}: paired turn is missing an arm summary")
            continue
        baseline = arms["baseline"]
        envoy = arms["envoy"]
        matching_cost_currency = (
            baseline["server_cost_status"] == "reported"
            and envoy["server_cost_status"] == "reported"
            and baseline.get("server_cost_currency") == envoy.get("server_cost_currency")
        )
        row = {
            "task_id": f"{package_id}-{turn_role}",
            "package_id": package_id,
            "task_role": "initial" if turn_role == "initial" else "followup",
            "turn_role": turn_role,
            "baseline_turn_correlation_id": baseline["turn_correlation_id"],
            "envoy_turn_correlation_id": envoy["turn_correlation_id"],
            "baseline_server_request_ids": baseline["server_request_ids"],
            "envoy_server_request_ids": envoy["server_request_ids"],
            "baseline_usage_status": (
                "actual" if baseline["usage_status"] == "actual" else "unknown"
            ),
            "usage_status": "actual" if envoy["usage_status"] == "actual" else "unknown",
            "baseline_input_tokens": baseline["input_tokens"] if baseline["usage_status"] == "actual" else None,
            "input_tokens": envoy["input_tokens"] if envoy["usage_status"] == "actual" else None,
            "baseline_output_tokens": baseline["output_tokens"] if baseline["usage_status"] == "actual" else None,
            "output_tokens": envoy["output_tokens"] if envoy["usage_status"] == "actual" else None,
            "baseline_total_elapsed_ms": baseline["turn_elapsed_ms"],
            "total_elapsed_ms": envoy["turn_elapsed_ms"],
            "baseline_task_outcome": baseline["turn_outcome"],
            "envoy_task_outcome": envoy["turn_outcome"],
            "baseline_human_interventions": baseline["human_interventions"],
            "human_interventions": envoy["human_interventions"],
            "baseline_human_intervention_minutes": baseline["human_intervention_minutes"],
            "human_intervention_minutes": envoy["human_intervention_minutes"],
            "baseline_repeated_downloads": baseline["repeated_downloads"],
            "repeated_downloads": envoy["repeated_downloads"],
            "baseline_visual_inputs": baseline["visual_inputs"],
            "visual_inputs": envoy["visual_inputs"],
            "baseline_server_cost_status": (
                "reported" if matching_cost_currency else baseline["server_cost_status"]
            ),
            "server_cost_status": (
                "reported" if matching_cost_currency else envoy["server_cost_status"]
            ),
            "baseline_server_cost_currency": (
                baseline.get("server_cost_currency") if matching_cost_currency else None
            ),
            "server_cost_currency": (
                envoy.get("server_cost_currency") if matching_cost_currency else None
            ),
            "baseline_server_reported_cost": (
                baseline.get("server_cost")
                if matching_cost_currency
                else None
            ),
            "server_reported_cost": (
                envoy.get("server_cost")
                if matching_cost_currency
                else None
            ),
        }
        if (
            baseline["usage_breakdown_status"] == "reported"
            and envoy["usage_breakdown_status"] == "reported"
        ):
            row.update({
                "baseline_usage_breakdown_status": "reported",
                "usage_breakdown_status": "reported",
                "baseline_uncached_input_tokens": baseline["uncached_input_tokens"],
                "baseline_cache_read_input_tokens": baseline["cache_read_input_tokens"],
                "baseline_cache_creation_input_tokens": baseline["cache_creation_input_tokens"],
                "uncached_input_tokens": envoy["uncached_input_tokens"],
                "cache_read_input_tokens": envoy["cache_read_input_tokens"],
                "cache_creation_input_tokens": envoy["cache_creation_input_tokens"],
            })
        else:
            row.update({
                "baseline_usage_breakdown_status": "not_exposed",
                "usage_breakdown_status": "not_exposed",
            })
        paired_usage_rows.append(row)

    return {
        "schema_version": "envoy.usage-ledger-report/1",
        "run_id": plan.get("run_id"),
        "plan_schema_version": plan.get("schema_version"),
        "cache_isolation_status": plan.get("cache_isolation_status", "unknown"),
        "planned_turn_count": len(expected),
        "observed_turn_count": len(seen_turns),
        "missing_turn_count": len(missing_turns),
        "server_request_count": len(seen_request_ids),
        "server_export_complete": server_export_complete,
        "paired_task_count": len(paired_usage_rows),
        "turns": turns,
        "paired_usage_rows": paired_usage_rows,
        "validation_errors": errors,
        "valid": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument(
        "--server-export-complete",
        action="store_true",
        help="assert that the authoritative server export covers the full preregistered run window",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = load_plan(args.plan)
        records = [
            json.loads(line)
            for line in args.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        report = aggregate_ledger(
            plan,
            records,
            server_export_complete=args.server_export_complete,
        )
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
        return 0 if report["valid"] else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"envoy usage ledger: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
