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
            "cache_isolation_status": "verified_disabled",
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
                    "budget_seconds": 900,
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "baseline_usage_status": "actual",
                    "baseline_provider": "test-provider",
                    "baseline_model": "test-model",
                    "baseline_budget_seconds": 900,
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
    assert report["protocol_valid"] is True
    assert report["data_complete"] is True
    assert report["quality_reviewed"] is True
    assert report["claim_eligible"] is True
    assert report["claim_blockers"] == []
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


def test_agent_product_eval_reports_cache_token_exposure_only_when_complete(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        item = json.loads(line)
        if item["record_type"] == "run":
            item["cache_isolation_status"] = "verified_disabled"
        elif item["record_type"] == "task":
            item.update({
                "usage_breakdown_status": "reported",
                "uncached_input_tokens": 800,
                "cache_read_input_tokens": 150,
                "cache_creation_input_tokens": 50,
                "baseline_usage_breakdown_status": "reported",
                "baseline_uncached_input_tokens": 1000,
                "baseline_cache_read_input_tokens": 150,
                "baseline_cache_creation_input_tokens": 50,
            })
        rewritten.append(json.dumps(item))
    records.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    report = json.loads(subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout)
    markdown = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert report["cache_isolation_status"] == "verified_disabled"
    assert report["usage_breakdown_status"] == "reported"
    assert report["envoy_uncached_input_tokens"] == 32_000
    assert report["envoy_cache_read_input_tokens"] == 6_000
    assert report["envoy_cache_creation_input_tokens"] == 2_000
    assert report["envoy_input_exposure_tokens"] == 40_000
    assert report["baseline_input_exposure_tokens"] == 48_000
    interval = report["paired_bootstrap"]["metrics"]["input_exposure_savings_fraction"]
    assert abs(interval["estimate"] - 1 / 6) < 0.000001
    assert abs(report["input_exposure_savings_fraction"] - 1 / 6) < 0.000001
    assert markdown.returncode == 0, markdown.stderr
    assert "| 未缓存输入 token | 40,000 | 32,000 | 减少 20.00% |" in markdown.stdout
    assert "| 缓存读取 token | 6,000 | 6,000 | 0 |" in markdown.stdout
    assert "| 缓存创建 token | 2,000 | 2,000 | 0 |" in markdown.stdout
    assert "服务端缓存策略：verified_disabled" in markdown.stdout


def test_agent_product_eval_does_not_infer_missing_cache_breakdown(tmp_path):
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
    assert report["usage_breakdown_status"] == "unknown"
    assert report["envoy_input_exposure_tokens"] is None
    assert report["baseline_input_exposure_tokens"] is None
    assert report["input_exposure_savings_fraction"] is None


def test_agent_product_eval_rejects_negative_cache_token_counts(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    task = json.loads(lines[2])
    task.update({
        "usage_breakdown_status": "reported",
        "uncached_input_tokens": -1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "baseline_usage_breakdown_status": "not_exposed",
    })
    lines[2] = json.dumps(task)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "uncached_input_tokens must be a non-negative number" in result.stderr


def test_agent_product_eval_withholds_savings_when_cache_is_confounded(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    run = json.loads(lines[0])
    run["cache_isolation_status"] = "cache_confounded"
    lines[0] = json.dumps(run)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    markdown = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert report["gate_status"] == "incomplete"
    assert report["protocol_valid"] is False
    assert report["data_complete"] is True
    assert report["quality_reviewed"] is True
    assert report["claim_eligible"] is False
    assert "cache_isolation_not_verified" in report["claim_blockers"]
    assert report["input_token_savings_fraction"] is None
    assert report["input_token_savings_ci_support"] is False
    assert report["token_savings_eligible"] is False
    assert markdown.returncode == 0, markdown.stderr
    assert "| 输入 token | 48,000 | 40,000 | N/A |" in markdown.stdout
    assert "服务端缓存策略：cache_confounded" in markdown.stdout


def test_agent_product_eval_accepts_verified_per_request_salt_isolation(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    run = json.loads(lines[0])
    run["cache_isolation_status"] = "verified_per_request_salt"
    run["cache_isolation_evidence"] = {
        "status": "verified_per_request_salt",
        "verified": True,
        "server_request_count": 120,
        "salt_hash_count": 120,
        "unique_salt_hash_count": 120,
        "cache_read_input_tokens": 0,
        "cache_read_count_complete": True,
        "ledger_report_sha256": "a" * 64,
    }
    lines[0] = json.dumps(run)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    json_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(json_result.stdout)
    assert report["cache_isolation_status"] == "verified_per_request_salt"
    assert report["cache_isolation_verified"] is True
    assert report["token_savings_eligible"] is True
    assert "verified_per_request_salt" in result.stdout


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


def test_agent_product_eval_reports_reproducible_source_clustered_intervals(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    first = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    assert first.stdout == second.stdout
    interval = report["paired_bootstrap"]["metrics"]["input_token_savings_fraction"]
    assert report["paired_bootstrap"]["cluster_unit"] == "source_id"
    assert report["paired_bootstrap"]["cluster_count"] == 20
    assert interval["lower"] > 0
    assert interval["upper"] > 0
    assert report["input_token_savings_fraction"] == interval["estimate"]


def test_task_package_schema_uses_package_cluster_and_accepts_multi_source_three_turn_tasks(
    tmp_path,
):
    records = tmp_path / "package-records.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    run = lines[0]
    run["task_package_schema_version"] = "envoy.task-package/1"
    source_templates = [item for item in lines if item["record_type"] == "source"]
    sources = []
    old_tasks = [item for item in lines if item["record_type"] == "task"]
    package_sources = {}
    for index in range(20):
        package_id = f"package-{index:02d}"
        pair = [f"source-{index:02d}-a", f"source-{index:02d}-b"]
        package_sources[package_id] = pair
        for source_offset, source_id in enumerate(pair):
            source_template = source_templates[(index * 2 + source_offset) % len(source_templates)]
            source = dict(source_template)
            source["source_id"] = source_id
            source["url"] = f"https://{source_id}.public.net/item"
            sources.append(source)
    tasks = []
    for package_id, source_ids in package_sources.items():
        for turn_index in range(3):
            task = dict(old_tasks[(int(package_id[-2:]) * 2 + turn_index) % len(old_tasks)])
            task.update({
                "task_id": f"{package_id}-turn-{turn_index + 1}",
                "package_id": package_id,
                "source_ids": source_ids,
                "source_id": source_ids[0] if turn_index < 2 else source_ids[1],
                "task_role": "initial" if turn_index == 0 else "followup",
                "goal": (
                    f"Initial question for {package_id}"
                    if turn_index == 0
                    else f"Follow-up {turn_index} for {package_id}"
                ),
                "envoy_task_outcome": "completed",
                "baseline_task_outcome": "completed",
                "human_interventions": 0,
                "baseline_human_interventions": 0,
                "human_intervention_minutes": 0,
                "baseline_human_intervention_minutes": 0,
                "server_cost_status": "not_exposed",
                "baseline_server_cost_status": "not_exposed",
            })
            tasks.append(task)
    records.write_text(
        "\n".join(json.dumps(item) for item in [run, *sources, *tasks]) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["task_package_schema_version"] == "envoy.task-package/1"
    assert report["package_count"] == 20
    assert report["envoy_package_completion_rate"] == 1.0
    assert report["baseline_package_completion_rate"] == 1.0
    assert report["human_intervention_task_count"] == 0
    assert report["envoy_server_reported_cost"] is None
    assert report["paired_bootstrap"]["cluster_unit"] == "package_id"
    assert report["paired_bootstrap"]["cluster_count"] == 20
    # Ordering is checked per package, not per source: two of the three turns
    # can live on different sources without looking like a missing initial turn.
    assert report["protocol_valid"] is True
    assert not any(
        blocker.startswith("task_order_") for blocker in report["claim_blockers"]
    )


def test_task_package_completion_and_human_intervention_are_intention_to_treat(tmp_path):
    records = tmp_path / "package-outcomes.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    run = lines[0]
    run["task_package_schema_version"] = "envoy.task-package/1"
    sources = [item for item in lines if item["record_type"] == "source"]
    old_tasks = [item for item in lines if item["record_type"] == "task"]
    new_sources = []
    tasks = []
    for package_index in range(20):
        package_id = f"package-{package_index:02d}"
        source_ids = [f"{package_id}-source-a", f"{package_id}-source-b"]
        for source_index, source_id in enumerate(source_ids):
            source = dict(sources[(package_index * 2 + source_index) % len(sources)])
            source["source_id"] = source_id
            source["url"] = f"https://{source_id}.public.net/item"
            new_sources.append(source)
        for turn_index in range(3):
            task = dict(old_tasks[(package_index * 2 + turn_index) % len(old_tasks)])
            task.update({
                "task_id": f"{package_id}-turn-{turn_index}",
                "package_id": package_id,
                "source_ids": source_ids,
                "source_id": source_ids[turn_index % 2],
                "task_role": "initial" if turn_index == 0 else "followup",
                "goal": f"Question {turn_index} for {package_id}",
                "envoy_task_outcome": "completed",
                "baseline_task_outcome": "completed",
                "human_interventions": 0,
                "baseline_human_interventions": 0,
                "human_intervention_minutes": 0,
                "baseline_human_intervention_minutes": 0,
                "server_cost_status": "not_exposed",
                "baseline_server_cost_status": "not_exposed",
            })
            tasks.append(task)
    tasks[0]["envoy_task_outcome"] = "blocked"
    tasks[0]["human_interventions"] = 1
    tasks[0]["human_intervention_minutes"] = 4
    tasks[0]["server_cost_status"] = "reported"
    tasks[0]["server_reported_cost"] = 0.25
    tasks[0]["baseline_server_cost_status"] = "reported"
    tasks[0]["baseline_server_reported_cost"] = 0.2
    records.write_text(
        "\n".join(json.dumps(item) for item in [run, *new_sources, *tasks]) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--allow-incomplete"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["envoy_package_completion_rate"] == 0.95
    assert report["baseline_package_completion_rate"] == 1.0
    assert report["human_intervention_task_count"] == 1
    assert report["human_intervention_package_rate"] == 0.05
    assert report["total_human_intervention_minutes"] == 4
    assert report["envoy_server_reported_cost"] is None
    assert report["server_cost_status"] == "partial"


def test_task_package_schema_requires_three_turns_and_multiple_sources(tmp_path):
    records = tmp_path / "package-invalid.jsonl"
    _write_records(records, source_count=20, tasks_per_source=2)
    lines = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    lines[0]["task_package_schema_version"] = "envoy.task-package/1"
    task_lines = [item for item in lines if item["record_type"] == "task"]
    for task in task_lines:
        task["package_id"] = "package-one"
        task["source_id"] = "source-00"
        task["source_ids"] = ["source-00"]
        task.update({
            "envoy_task_outcome": "completed",
            "baseline_task_outcome": "completed",
            "human_interventions": 0,
            "baseline_human_interventions": 0,
            "human_intervention_minutes": 0,
            "baseline_human_intervention_minutes": 0,
            "server_cost_status": "not_exposed",
            "baseline_server_cost_status": "not_exposed",
        })
    records.write_text(
        "\n".join(json.dumps(item) for item in lines) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "package-one requires at least two distinct source_ids" in result.stdout


def test_agent_product_eval_withholds_savings_when_cluster_interval_crosses_zero(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    rewritten = [lines[0]]
    for line in lines[1:]:
        item = json.loads(line)
        if item["record_type"] == "task":
            source_index = int(item["source_id"].split("-")[1])
            if source_index < 16:
                item["input_tokens"] = 1100
            else:
                item["input_tokens"] = 1500
        rewritten.append(json.dumps(item))
    records.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    interval = report["paired_bootstrap"]["metrics"]["input_token_savings_fraction"]
    assert interval["estimate"] > 0
    assert interval["lower"] < 0
    assert report["input_token_savings_fraction"] is None
    assert report["quality_non_inferior"] is True


def test_agent_product_eval_reports_source_class_strata(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    rewritten = [lines[0]]
    for line in lines[1:]:
        item = json.loads(line)
        if item["record_type"] == "source":
            source_index = int(item["source_id"].split("-")[1])
            item["source_class"] = "douyin_video" if source_index < 10 else "bilibili_video"
        rewritten.append(json.dumps(item))
    records.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert set(report["stratified_bootstrap"]) == {"bilibili_video", "douyin_video"}
    assert report["stratified_bootstrap"]["douyin_video"]["cluster_count"] == 10
    assert (
        report["stratified_bootstrap"]["douyin_video"]["metrics"]
        ["input_token_savings_fraction"]["estimate"]
        == report["paired_bootstrap"]["metrics"]["input_token_savings_fraction"]["estimate"]
    )
    markdown = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert markdown.returncode == 0, markdown.stderr
    assert "| douyin_video | 10 | 20 | 16.67% |" in markdown.stdout
    assert "| bilibili_video | 10 | 20 | 16.67% |" in markdown.stdout


def test_agent_product_eval_marks_small_source_class_ci_unavailable(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    rewritten = [lines[0]]
    for line in lines[1:]:
        item = json.loads(line)
        if item["record_type"] == "source":
            source_index = int(item["source_id"].split("-")[1])
            item["source_class"] = "small_group" if source_index == 0 else "large_group"
        rewritten.append(json.dumps(item))
    records.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["stratified_bootstrap"]["small_group"]["cluster_count"] == 1
    assert report["stratified_bootstrap"]["small_group"]["metrics"]["input_token_savings_fraction"] is None


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
    assert report["gate_status"] == "complete"
    assert report["protocol_valid"] is True
    assert report["data_complete"] is True
    assert report["quality_reviewed"] is True
    assert report["claim_eligible"] is False
    assert "quality_non_inferior_not_supported" in report["claim_blockers"]


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
    assert report["gate_status"] == "complete"
    assert report["claim_eligible"] is False
    assert "quality_non_inferior_not_supported" in report["claim_blockers"]


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
    assert report["protocol_valid"] is False
    assert report["data_complete"] is False
    assert report["claim_eligible"] is False
    assert any(
        blocker.startswith("model_identity_unverified:")
        for blocker in report["claim_blockers"]
    )
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
    assert report["protocol_valid"] is False
    assert report["data_complete"] is False
    assert report["quality_reviewed"] is False
    assert any(
        blocker.startswith("quality_review_inputs_missing:")
        for blocker in report["claim_blockers"]
    )
    assert report["claim_eligible"] is False
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
        assert report["protocol_valid"] is False
        assert report["data_complete"] is False
        assert report["quality_reviewed"] is False
        assert report["claim_eligible"] is False
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
    assert report["protocol_valid"] is False
    assert report["data_complete"] is False
    assert report["quality_reviewed"] is False
    assert report["fact_recall"] == 1.0
    assert report["followup_reuse_rate"] == 1.0
    assert report["baseline_fact_recall"] is None
    assert report["envoy_actual_input_tokens"] is None
    assert report["input_token_savings_fraction"] is None


def test_data_complete_experiment_without_savings_stays_complete(tmp_path):
    """Validity and claim entitlement are different questions.

    A run whose protocol, data and review layers are complete but whose token
    saving is not statistically supported is a finished experiment with no
    efficiency claim: the report is ``complete`` and ``claim_eligible`` false.
    """
    records = tmp_path / "records.jsonl"
    _write_records(records)
    lines = records.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        item = json.loads(line)
        if item["record_type"] == "task":
            item["input_tokens"] = 1200
        rewritten.append(json.dumps(item))
    records.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert report["protocol_valid"] is True
    assert report["data_complete"] is True
    assert report["quality_reviewed"] is True
    assert report["quality_non_inferior"] is True
    assert report["input_token_savings_ci_support"] is False
    assert report["claim_eligible"] is False
    assert "no_token_saving_signal" in report["claim_blockers"]
    assert report["gate_status"] == "complete"
    assert report["input_token_savings_fraction"] is None

    markdown = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records), "--markdown"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert markdown.returncode == 0, markdown.stderr
    assert "| 输入 token | 48,000 | 48,000 | N/A |" in markdown.stdout
    assert "配对任务：40；来源：20；门槛：complete" in markdown.stdout
    assert "实验数据完整，但统计证据不支持效率结论" in markdown.stdout


def test_quality_review_layer_blocks_claim_when_review_is_missing(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    base_lines = records.read_text(encoding="utf-8").splitlines()

    def write_case(name, mutate, allow_incomplete=False):
        rewritten = [json.loads(line) for line in base_lines]
        mutate(rewritten)
        path = tmp_path / name
        path.write_text(
            "\n".join(json.dumps(item) for item in rewritten) + "\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(SCRIPT), "--records", str(path)]
        if allow_incomplete:
            command.append("--allow-incomplete")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def weak_review_method(items):
        items[0]["quality_review_method"] = "single_human"

    def missing_review_fields(items):
        for item in items:
            if item["record_type"] == "task":
                item["answer_correct"] = None
                item["citation_correct"] = None
                item["baseline_answer_correct"] = None
                item["baseline_citation_correct"] = None
                break

    weak = write_case("weak-review.jsonl", weak_review_method)
    assert weak["protocol_valid"] is True
    assert weak["data_complete"] is True
    assert weak["quality_reviewed"] is False
    assert weak["claim_eligible"] is False
    assert "quality_review_not_completed:single_human" in weak["claim_blockers"]
    assert weak["gate_status"] == "incomplete"

    missing = write_case(
        "missing-review.jsonl", missing_review_fields, allow_incomplete=True
    )
    assert missing["data_complete"] is True
    assert missing["quality_reviewed"] is False
    assert missing["quality_non_inferior"] is None
    assert missing["claim_eligible"] is False
    assert any(
        blocker.startswith("quality_review_inputs_missing:")
        for blocker in missing["claim_blockers"]
    )
    assert missing["gate_status"] == "incomplete"


def test_claim_eligible_when_all_layers_are_green(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert report["protocol_valid"] is True
    assert report["data_complete"] is True
    assert report["quality_reviewed"] is True
    assert report["claim_eligible"] is True
    assert report["claim_blockers"] == []
    assert report["gate_status"] == "passed"


def test_protocol_layer_requires_recorded_and_matching_arm_budgets(tmp_path):
    records = tmp_path / "budgets.jsonl"
    _write_records(records)
    base_lines = records.read_text(encoding="utf-8").splitlines()

    def write_case(name, mutate, allow_incomplete=False):
        rewritten = [json.loads(line) for line in base_lines]
        mutate(rewritten)
        path = tmp_path / name
        path.write_text(
            "\n".join(json.dumps(item) for item in rewritten) + "\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(SCRIPT), "--records", str(path)]
        if allow_incomplete:
            command.append("--allow-incomplete")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def drop_baseline_budget(items):
        for item in items:
            if item["record_type"] == "task":
                item.pop("baseline_budget_seconds")
                break

    def unknown_envoy_budget(items):
        for item in items:
            if item["record_type"] == "task":
                item["budget_seconds"] = "unknown"
                break

    def mismatched_budgets(items):
        for item in items:
            if item["record_type"] == "task":
                item["baseline_budget_seconds"] = 1200
                break

    missing = write_case("budget-missing.jsonl", drop_baseline_budget)
    assert missing["validation_status"] == "passed"
    assert missing["protocol_valid"] is False
    assert "budget_unverified:baseline:source-00-task-0" in missing["claim_blockers"]
    assert missing["gate_status"] == "incomplete"

    unknown = write_case("budget-unknown.jsonl", unknown_envoy_budget)
    assert unknown["protocol_valid"] is False
    assert "budget_unverified:envoy:source-00-task-0" in unknown["claim_blockers"]

    mismatch = write_case("budget-mismatch.jsonl", mismatched_budgets)
    assert mismatch["protocol_valid"] is False
    assert "arm_budget_mismatch:source-00-task-0" in mismatch["claim_blockers"]
    assert mismatch["gate_status"] == "incomplete"


def test_data_layer_accepts_recorded_empty_citations_for_failed_attempt(tmp_path):
    records = tmp_path / "records.jsonl"
    _write_records(records)
    base_lines = records.read_text(encoding="utf-8").splitlines()

    def write_case(name, mutate, allow_incomplete=False):
        rewritten = [json.loads(line) for line in base_lines]
        mutate(rewritten)
        path = tmp_path / name
        path.write_text(
            "\n".join(json.dumps(item) for item in rewritten) + "\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(SCRIPT), "--records", str(path)]
        if allow_incomplete:
            command.append("--allow-incomplete")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def failed_without_citations(items):
        for item in items:
            if item["record_type"] == "task":
                item["envoy_task_outcome"] = "failed"
                item["envoy_citations"] = []
                item["citation_correct"] = False
                break

    failed = write_case("failed.jsonl", failed_without_citations)
    assert failed["validation_status"] == "passed"
    assert failed["data_complete"] is True
    assert "artifacts_missing:envoy:source-00-task-0" not in failed["claim_blockers"]

    def completed_without_citations(items):
        for item in items:
            if item["record_type"] == "task":
                item["envoy_task_outcome"] = "completed"
                item["envoy_citations"] = []
                item["citation_correct"] = False
                break

    completed = write_case("completed.jsonl", completed_without_citations)
    assert completed["data_complete"] is False
    assert "artifacts_missing:envoy:source-00-task-0" in completed["claim_blockers"]

    def missing_citation_field(items):
        for item in items:
            if item["record_type"] == "task":
                item.pop("envoy_citations")
                item["citation_correct"] = False
                break

    missing = write_case("missing.jsonl", missing_citation_field, allow_incomplete=True)
    assert missing["data_complete"] is False
    assert "artifacts_missing:envoy:source-00-task-0" in missing["claim_blockers"]
