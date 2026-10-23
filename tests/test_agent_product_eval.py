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
