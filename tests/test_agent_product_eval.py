from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "agent_product_eval.py"


def _write_records(path: Path, source_count: int = 20, tasks_per_source: int = 2):
    with path.open("w", encoding="utf-8") as output:
        output.write(json.dumps({
            "record_type": "run",
            "run_id": "run-2026-10-23",
            "envoy_version": "0.1.6-dev",
            "envoy_commit": "a" * 40,
            "host_agent": "fixture-agent",
            "host_agent_version": "fixture",
            "operating_system": "macOS",
            "python_version": "3.11",
            "started_at": "2026-10-23T00:00:00Z",
            "asr_status": "verified",
            "ocr_status": "tesseract",
            "provider": "test-provider",
            "model": "test-model",
            "rubric_version": "rubric-1",
            "quality_review_method": "reference_rubric",
        }) + "\n")
        for source_index in range(source_count):
            source_id = f"source-{source_index:02d}"
            output.write(json.dumps({
                "record_type": "source",
                "source_id": source_id,
                "url": f"https://public-source-{source_index}.net/item/{source_index}",
                "source_class": "fixture",
                "accepted_state": "accepted",
                "source_revision": f"{source_index + 1:064x}",
                "revalidated_at": "2026-10-23T00:00:00Z",
            }) + "\n")
            for task_index in range(tasks_per_source):
                output.write(json.dumps({
                    "record_type": "task",
                    "task_id": f"{source_id}-task-{task_index}",
                    "source_id": source_id,
                    "task_role": "initial" if task_index == 0 else "followup",
                    "goal": (
                        "Find the cited fact"
                        if task_index == 0
                        else "Follow up: find the remaining condition"
                    ),
                    "reference_facts": {
                        "fact-1": "The documented answer is fact.",
                        "fact-2": "The supporting condition is stated.",
                    },
                    "answer_correct": True,
                    "citation_correct": True,
                    "baseline_answer_correct": True,
                    "baseline_citation_correct": True,
                    "envoy_claimed_fact_ids": ["fact-1", "fact-2"],
                    "envoy_supported_fact_ids": ["fact-1", "fact-2"],
                    "baseline_claimed_fact_ids": ["fact-1", "fact-2"],
                    "baseline_supported_fact_ids": ["fact-1", "fact-2"],
                    "envoy_citations": [
                        {"fact_id": "fact-1", "locator": "https://public-source.test#quote", "supports_fact": True},
                        {"fact_id": "fact-2", "locator": "blk-2", "supports_fact": True},
                    ],
                    "baseline_citations": [
                        {"fact_id": "fact-1", "locator": "https://public-source.test#quote", "supports_fact": True},
                        {"fact_id": "fact-2", "locator": "blk-2", "supports_fact": True},
                    ],
                    "first_evidence_ms": 100 + task_index,
                    "baseline_first_evidence_ms": 120 + task_index,
                    "total_elapsed_ms": 500 + task_index,
                    "baseline_total_elapsed_ms": 600 + task_index,
                    "envoy_tool_calls": 1,
                    "baseline_tool_calls": 2,
                    "visual_inputs": 0,
                    "baseline_visual_inputs": 1,
                    "repeated_downloads": 0,
                    "baseline_repeated_downloads": 0,
                    "reused_material": task_index == 1,
                    "baseline_reused_material": False,
                    "baseline_method": "host_read_with_fair_cache",
                    "evidence_locators": [{"type": "web", "quote": "fact"}],
                    "usage_status": "actual",
                    "provider": "test-provider",
                    "model": "test-model",
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "baseline_usage_status": "actual",
                    "baseline_provider": "test-provider",
                    "baseline_model": "test-model",
                    "baseline_input_tokens": 1200,
                    "baseline_output_tokens": 110,
                }) + "\n")


def test_agent_product_eval_requires_real_scale_and_reports_actual_usage(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "envoy.agent-product-eval/1"
    assert report["validation_status"] == "passed"
    assert report["gate_status"] == "passed"
    assert report["source_count"] == 20
    assert report["task_count"] == 40
    assert report["actual_usage_status"] == "paired_actual"
    assert report["envoy_actual_input_tokens"] == 40_000
    assert report["baseline_actual_input_tokens"] == 48_000
    assert abs(report["input_token_savings_fraction"] - 1 / 6) < 0.000001
    assert report["quality_non_inferior"] is True
    assert report["fact_recall"] == 1.0
    assert report["baseline_fact_recall"] == 1.0
    assert report["citation_support_rate"] == 1.0
    assert report["followup_reuse_rate"] == 1.0


def test_agent_product_eval_can_render_a_concise_markdown_comparison(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| 指标 | Native baseline | Envoy | 变化 |" in result.stdout
    assert "| 输入 token | 48,000 | 40,000 | 减少 16.67% |" in result.stdout
    assert "| 事实召回 | 100.00% | 100.00% | +0.00 个百分点 |" in result.stdout
    assert "| 视觉输入数 | 40 | 0 | -40 |" in result.stdout
    assert "配对任务：40；来源：20；门槛：passed" in result.stdout


def test_agent_product_eval_markdown_withholds_incomplete_savings(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[3])
    task["usage_status"] = "unknown"
    task["baseline_usage_status"] = "unknown"
    lines[3] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--records",
            str(records),
            "--allow-incomplete",
            "--markdown",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "| 输入 token | N/A | N/A | N/A |" in result.stdout
    assert "门槛：incomplete" in result.stdout


def test_agent_product_eval_withholds_savings_if_answer_quality_drops(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[3])
    task["answer_correct"] = False
    lines[3] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["quality_non_inferior"] is False
    assert report["input_token_savings_fraction"] is None


def test_agent_product_eval_reports_fact_recall_and_withholds_savings_on_fact_loss(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task["envoy_claimed_fact_ids"] = ["fact-1"]
    task["envoy_supported_fact_ids"] = ["fact-1"]
    task["envoy_citations"] = [
        {"fact_id": "fact-1", "locator": "blk-1", "supports_fact": True},
    ]
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["validation_status"] == "passed"
    assert report["fact_recall"] < report["baseline_fact_recall"]
    assert report["quality_non_inferior"] is False
    assert report["input_token_savings_fraction"] is None


def test_agent_product_eval_rejects_placeholder_and_incomplete_records(tmp_path):
    records = tmp_path / "records.jsonl"
    records.write_text(json.dumps({
        "record_type": "run",
        "run_id": "draft",
        "envoy_version": "0.1.6-dev",
        "envoy_commit": "draft",
        "host_agent": "codex",
        "host_agent_version": "unknown",
        "operating_system": "macOS",
        "python_version": "3.11",
        "started_at": "2026-10-23T00:00:00Z",
        "asr_status": "not_checked",
        "ocr_status": "not_checked",
        "provider": "provider",
        "model": "model",
        "rubric_version": "rubric-1",
        "quality_review_method": "reference_rubric",
    }) + "\n" + json.dumps({
        "record_type": "source",
        "source_id": "one",
        "url": "https://example.org/",
        "source_class": "fixture",
        "accepted_state": "accepted",
        "source_revision": None,
        "revalidated_at": "2026-10-23T00:00:00Z",
    }) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["validation_status"] == "failed"
    assert any("placeholder" in error for error in report["validation_errors"])
    assert any("minimum source gate" in error for error in report["validation_errors"])
    assert report.get("input_token_savings_fraction") is None


def test_agent_product_eval_requires_initial_and_followup_per_source(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[3])
    task["task_role"] = "initial"
    lines[3] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("exactly one initial task" in error for error in report["validation_errors"])


def test_agent_product_eval_rejects_correct_citation_without_supporting_locator(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task["envoy_citations"][0]["locator"] = ""
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("citation locator" in error for error in report["validation_errors"])


def test_agent_product_eval_rejects_private_source_addresses(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    source = json.loads(lines[1])
    source["url"] = "http://127.0.0.1/private"
    lines[1] = json.dumps(source)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("public" in error.lower() or "private" in error.lower() for error in report["validation_errors"])


def test_agent_product_eval_deduplicates_tracking_parameter_variants(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    source = json.loads(lines[1])
    source["url"] = "https://public-source-0.net/item/0?utm_source=feed"
    lines[1] = json.dumps(source)
    source = json.loads(lines[4])
    source["url"] = "https://public-source-0.net/item/0?utm_campaign=share"
    lines[4] = json.dumps(source)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("duplicate source URL" in error for error in report["validation_errors"])


def test_incomplete_pilot_allows_unknown_usage_but_never_reports_savings(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    run = json.loads(lines[0])
    run["provider"] = "unknown"
    run["model"] = "unknown"
    lines[0] = json.dumps(run)
    for index in (2, 3):
        task = json.loads(lines[index])
        task.update({
            "provider": "unknown",
            "model": "unknown",
            "baseline_provider": "unknown",
            "baseline_model": "unknown",
            "usage_status": "unknown",
            "baseline_usage_status": "unknown",
        })
        task.pop("input_tokens", None)
        task.pop("output_tokens", None)
        task.pop("baseline_input_tokens", None)
        task.pop("baseline_output_tokens", None)
        lines[index] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    draft = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(draft.stdout)
    assert draft.returncode == 0
    assert report["validation_status"] == "passed"
    assert report["gate_status"] == "incomplete"
    assert report["actual_usage_status"] == "incomplete"
    assert report["input_token_savings_fraction"] is None

    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert strict.returncode == 2


def test_incomplete_mode_still_rejects_known_private_or_placeholder_source(tmp_path):
    for invalid_url in (
        "http://127.0.0.1/private",
        "https://example.invalid/page",
    ):
        records = tmp_path / "records.jsonl"
        _write_records(records, source_count=1, tasks_per_source=2)
        lines = records.read_text(encoding="utf-8").splitlines()
        source = json.loads(lines[1])
        source["url"] = invalid_url
        lines[1] = json.dumps(source)
        records.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        assert result.returncode == 2
        assert any(
            "public HTTP(S)" in error or "placeholder URL" in error
            for error in report["validation_errors"]
        )


def test_incomplete_mode_still_rejects_mismatched_known_provider_identity(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task["provider"] = "different-provider"
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("must match run provider/model" in error for error in report["validation_errors"])


def test_incomplete_mode_rejects_duplicate_source_and_task_ids(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=2, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    duplicate_source = json.loads(lines[4])
    duplicate_source["source_id"] = "source-00"
    lines[4] = json.dumps(duplicate_source)
    duplicate_task = json.loads(lines[3])
    duplicate_task["task_id"] = json.loads(lines[2])["task_id"]
    lines[3] = json.dumps(duplicate_task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("duplicate source_id" in error for error in report["validation_errors"])
    assert any("duplicate task_id" in error for error in report["validation_errors"])


def test_incomplete_mode_rejects_known_invalid_task_role_and_usage_state(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task["task_role"] = "maybe"
    task["usage_status"] = "estimated"
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert any("task_role" in error for error in report["validation_errors"])
    assert any("usage_status" in error for error in report["validation_errors"])


def test_incomplete_mode_rejects_non_finite_measurements(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task["total_elapsed_ms"] = float("nan")
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stderr)
    assert any("total_elapsed_ms" in error for error in report["validation_errors"])


def test_incomplete_mode_allows_unknown_markers_but_strict_mode_rejects_them(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    run = json.loads(lines[0])
    run.update({
        "envoy_version": "unknown",
        "envoy_commit": "unknown",
        "host_agent": "unknown",
        "host_agent_version": "unknown",
        "operating_system": "unknown",
        "python_version": "unknown",
        "provider": "unknown",
        "model": "unknown",
    })
    lines[0] = json.dumps(run)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    draft = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert draft.returncode == 0, draft.stderr
    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert strict.returncode == 2
    report = json.loads(strict.stdout)
    assert any("actual evaluation runtime" in error for error in report["validation_errors"])


def test_incomplete_draft_serializes_unmeasured_metrics_as_null(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=1, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    run = json.loads(lines[0])
    run.update({
        "envoy_version": "0.1.6",
        "envoy_commit": "872fea695d75",
        "provider": "not exposed",
        "model": "not exposed",
    })
    lines[0] = json.dumps(run)
    for line_index in (2, 3):
        task = json.loads(lines[line_index])
        for field in (
            "answer_correct",
            "citation_correct",
            "baseline_answer_correct",
            "baseline_citation_correct",
            "first_evidence_ms",
            "baseline_first_evidence_ms",
            "total_elapsed_ms",
            "baseline_total_elapsed_ms",
            "envoy_claimed_fact_ids",
            "envoy_supported_fact_ids",
            "baseline_claimed_fact_ids",
            "baseline_supported_fact_ids",
            "envoy_citations",
            "baseline_citations",
        ):
            task[field] = None
        task["usage_status"] = "unknown"
        task["baseline_usage_status"] = "unknown"
        task["usage_provider"] = None
        task["provider"] = None
        task["model"] = None
        task["baseline_provider"] = None
        task["baseline_model"] = None
        task["reference_facts"] = None
        task["evidence_locators"] = None
        task["reused_material"] = None
        task["baseline_reused_material"] = None
        for field in (
            "envoy_tool_calls",
            "baseline_tool_calls",
            "visual_inputs",
            "baseline_visual_inputs",
            "repeated_downloads",
            "baseline_repeated_downloads",
        ):
            task[field] = None
        task["baseline_method"] = None
        lines[line_index] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert report["gate_status"] == "incomplete"
    assert report["answer_accuracy"] is None
    assert report["fact_recall"] is None
    assert report["median_first_evidence_ms"] is None
    assert report["total_envoy_tool_calls"] is None
    assert report["envoy_actual_input_tokens"] is None
    assert report["input_token_savings_fraction"] is None


def test_mixed_reviewed_and_unknown_tasks_keep_quality_and_savings_unscored(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[3])
    task["answer_correct"] = None
    task["citation_correct"] = None
    task["baseline_answer_correct"] = None
    task["baseline_citation_correct"] = None
    lines[3] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["validation_status"] == "passed"
    assert report["answer_reviewed_task_count"] < report["task_count"]
    assert report["quality_non_inferior"] is None
    assert report["input_token_savings_fraction"] is None


def test_real_mdn_pilot_is_recorded_but_does_not_pass_release_gate(tmp_path):
    for sample in (
        "2026-10-23-mdn-404-codex-release-0.1.6.jsonl",
        "2026-10-23-mdn-410-codex-release-0.1.6.jsonl",
        "2026-10-23-mdn-429-codex-release-0.1.6.jsonl",
        "2026-10-23-mdn-201-codex-release-0.1.6.jsonl",
        "2026-10-23-python-json-codex-release-0.1.6.jsonl",
        "2026-10-23-rfc9110-codex-release-0.1.6.jsonl",
    ):
        records = ROOT / "benchmarks" / "pilots" / sample
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        assert result.returncode == 0, result.stderr
        assert report["source_count"] == 1
        assert report["task_count"] == 2
        assert report["gate_status"] == "incomplete"
        assert report["answer_accuracy"] == 1.0
        assert report["answer_reviewed_task_count"] == 2
        assert report["citation_accuracy"] == 1.0
        assert report["citation_reviewed_task_count"] == 2
        assert report["baseline_answer_accuracy"] is None
        assert report["baseline_answer_reviewed_task_count"] == 0
        assert report["fact_recall"] == 1.0
        assert report["baseline_fact_recall"] is None
        assert report["citation_support_rate"] == 1.0
        assert report["baseline_citation_support_rate"] is None
        assert report["median_total_elapsed_ms"] is None
        assert report["envoy_actual_input_tokens"] is None
        assert report["input_token_savings_fraction"] is None


def test_combined_public_documentation_pilots_are_incomplete():
    records = ROOT / "benchmarks" / "pilots" / "2026-10-23-public-docs-codex-release-0.1.6.jsonl"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert report["validation_status"] == "passed"
    assert report["source_count"] == 6
    assert report["task_count"] == 12
    assert report["gate_status"] == "incomplete"
    assert report["fact_recall"] == 1.0
    assert report["followup_reuse_rate"] == 1.0
    assert report["baseline_fact_recall"] is None
    assert report["envoy_actual_input_tokens"] is None
    assert report["input_token_savings_fraction"] is None
