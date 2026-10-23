import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

from paired_experiment_plan import build_plan
from usage_ledger import aggregate_ledger
SCRIPT = ROOT / "benchmarks" / "usage_ledger.py"


def _plan():
    return build_plan([
        {
            "package_id": "package-1",
            "source_ids": ["source-1", "source-2"],
            "source_class": "douyin",
            "task_manifest_id": "manifest-1",
        },
    ], seed=20261023, cache_isolation_status="verified_isolated")


def _request(plan, arm, role, server_request_id, **overrides):
    package = plan["packages"][0]
    return {
        "record_type": "request_usage",
        "server_request_id": server_request_id,
        "run_id": plan["run_id"],
        "package_id": package["package_id"],
        "arm": arm,
        "session_id": package["session_ids"][arm],
        "turn_correlation_id": package["turn_correlation_ids"][arm][role],
        "provider": "self-hosted",
        "model": "test-model",
        "started_at": "2026-10-23T00:00:00Z",
        "completed_at": "2026-10-23T00:00:01Z",
        "input_tokens": 100,
        "output_tokens": 20,
        "uncached_input_tokens": 100,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        **overrides,
    }


def _turn(plan, arm, role, request_ids, **overrides):
    package = plan["packages"][0]
    return {
        "record_type": "turn_summary",
        "run_id": plan["run_id"],
        "package_id": package["package_id"],
        "arm": arm,
        "session_id": package["session_ids"][arm],
        "turn_correlation_id": package["turn_correlation_ids"][arm][role],
        "turn_outcome": "completed",
        "server_request_ids": request_ids,
        "started_at": "2026-10-23T00:00:00Z",
        "completed_at": "2026-10-23T00:00:05Z",
        "human_interventions": 0,
        "human_intervention_minutes": 0,
        "repeated_downloads": 0,
        "visual_inputs": 0,
        **overrides,
    }


def test_usage_ledger_aggregates_multiple_server_requests_per_turn():
    plan = _plan()
    records = []
    for arm in ("baseline", "envoy"):
        for index, role in enumerate(("initial", "followup-1", "followup-2")):
            request_ids = [f"{arm}-{role}-a", f"{arm}-{role}-b"]
            records.append(_turn(plan, arm, role, request_ids))
            records.append(_request(plan, arm, role, request_ids[0]))
            records.append(_request(
                plan,
                arm,
                role,
                request_ids[1],
                input_tokens=50,
                output_tokens=7,
                uncached_input_tokens=50,
            ))

    report = aggregate_ledger(plan, records, server_export_complete=True)

    assert report["valid"] is True
    assert report["planned_turn_count"] == 6
    assert report["observed_turn_count"] == 6
    assert report["server_request_count"] == 12
    assert report["paired_task_count"] == 3
    initial = next(item for item in report["turns"] if item["turn_role"] == "initial")
    assert initial["server_request_count"] == 2
    assert initial["server_request_ids"] == ["baseline-initial-a", "baseline-initial-b"]
    assert initial["input_tokens"] == 150
    assert initial["output_tokens"] == 27
    assert initial["uncached_input_tokens"] == 150
    assert initial["turn_elapsed_ms"] == 5000
    assert initial["human_interventions"] == 0
    paired_initial = next(
        item for item in report["paired_usage_rows"]
        if item["turn_role"] == "initial"
    )
    assert paired_initial["baseline_input_tokens"] == 150
    assert paired_initial["input_tokens"] == 150
    assert paired_initial["baseline_usage_breakdown_status"] == "reported"
    assert paired_initial["usage_breakdown_status"] == "reported"
    assert paired_initial["baseline_server_cost_status"] == "not_exposed"


def test_usage_ledger_keeps_assigned_turn_with_zero_model_requests():
    plan = _plan()
    records = [
        _turn(plan, arm, role, [], turn_outcome="failed")
        for arm in ("baseline", "envoy")
        for role in ("initial", "followup-1", "followup-2")
    ]

    report = aggregate_ledger(plan, records, server_export_complete=True)

    assert report["valid"] is True
    assert report["observed_turn_count"] == 6
    assert report["server_request_count"] == 0
    assert all(turn["turn_outcome"] == "failed" for turn in report["turns"])
    assert all(turn["usage_status"] == "no_model_request" for turn in report["turns"])


def test_usage_ledger_rejects_missing_turns_unknown_ids_and_sensitive_payloads():
    plan = _plan()
    record = _turn(plan, "baseline", "initial", ["request-1"])
    request = _request(
        plan,
        "baseline",
        "initial",
        "request-1",
        messages=[{"role": "user", "content": "secret prompt"}],
    )

    report = aggregate_ledger(
        plan,
        [record, request],
        server_export_complete=True,
    )

    assert report["valid"] is False
    assert any("forbidden sensitive field" in error for error in report["validation_errors"])
    assert any("missing turn summaries" in error for error in report["validation_errors"])

    unknown = _request(plan, "envoy", "initial", "request-2")
    unknown["turn_correlation_id"] = "not-in-plan"
    unknown_report = aggregate_ledger(plan, [unknown], server_export_complete=True)
    assert any("not in frozen plan" in error for error in unknown_report["validation_errors"])


def test_usage_ledger_rejects_cache_categories_that_do_not_sum_to_input():
    plan = _plan()
    summary = _turn(plan, "baseline", "initial", ["request-1"])
    record = _request(
        plan,
        "baseline",
        "initial",
        "request-1",
        cache_read_input_tokens=1,
    )

    report = aggregate_ledger(plan, [summary, record], server_export_complete=True)

    assert report["valid"] is False
    assert any("do not sum to input_tokens" in error for error in report["validation_errors"])


def test_usage_ledger_rejects_orphan_and_undeclared_server_request_ids():
    plan = _plan()
    summary = _turn(plan, "baseline", "initial", ["missing"])
    request = _request(plan, "baseline", "initial", "orphan")

    report = aggregate_ledger(
        plan,
        [summary, request],
        server_export_complete=True,
    )

    assert any("not declared by its turn_summary" in error for error in report["validation_errors"])
    assert any("declares missing server requests" in error for error in report["validation_errors"])


def test_usage_ledger_rejects_unrecognized_fields_even_when_not_named_sensitive():
    plan = _plan()
    summary = _turn(plan, "baseline", "initial", [], internal_note="raw server dump")

    report = aggregate_ledger(
        plan,
        [summary],
        server_export_complete=True,
    )

    assert any("unsupported fields" in error for error in report["validation_errors"])


def test_usage_ledger_cli_writes_paired_evaluator_usage_rows(tmp_path):
    plan = _plan()
    rows = []
    for role in ("initial", "followup-1", "followup-2"):
        for arm in ("baseline", "envoy"):
            request_id = f"{arm}-{role}-request"
            rows.append(_turn(plan, arm, role, [request_id]))
            rows.append(_request(plan, arm, role, request_id))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan",
            str(plan_path),
            "--ledger",
            str(ledger_path),
            "--server-export-complete",
            "--output",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["valid"] is True
    assert report["paired_task_count"] == 3
    assert len(report["paired_usage_rows"]) == 3
    assert all(row["baseline_usage_status"] == "actual" for row in report["paired_usage_rows"])
