import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "paired_experiment_plan.py"


def _run(tmp_path, records, *args):
    input_path = tmp_path / "packages.jsonl"
    input_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--packages",
            str(input_path),
            "--seed",
            "20261023",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )


def test_plan_is_deterministic_balanced_and_profile_isolated(tmp_path):
    records = [
        {"package_id": f"pkg-{index}", "source_ids": [f"src-{index}", f"src-{index}-b"]}
        for index in range(6)
    ]

    first = _run(tmp_path, records)
    second = _run(tmp_path, records)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert first.stdout == second.stdout

    plan = json.loads(first.stdout)
    assert plan["schema_version"] == "envoy.paired-experiment-plan/2"
    assert plan["seed"] == 20261023
    assert plan["package_count"] == 6
    assert plan["a_first_count"] == 3
    assert plan["b_first_count"] == 3
    assert all(item["arms"][0] == item["first_arm"] for item in plan["packages"])
    assert all(set(item["arms"]) == {"baseline", "envoy"} for item in plan["packages"])
    assert all(
        item["first_arm"] in {"baseline", "envoy"}
        and item["profiles"]["baseline"] != item["profiles"]["envoy"]
        for item in plan["packages"]
    )


def test_plan_freezes_run_identity_and_server_request_correlation(tmp_path):
    records = [
        {
            "package_id": f"pkg-{index}",
            "source_ids": [f"src-{index}-a", f"src-{index}-b"],
            "source_class": "douyin",
            "task_manifest_id": f"manifest-{index}",
        }
        for index in range(20)
    ]

    result = _run(tmp_path, records)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["run_id"].startswith("envoy-ab-")
    assert len(plan["package_manifest_sha256"]) == 64
    assert plan["task_count"] == 60
    assert plan["unique_source_count"] == 40
    assert plan["confirmatory_minimum_met"] is True
    item = plan["packages"][0]
    assert set(item["session_ids"]) == {"baseline", "envoy"}
    assert item["session_ids"]["baseline"] != item["session_ids"]["envoy"]
    assert set(item["turn_correlation_ids"]) == {"baseline", "envoy"}
    for arm in ("baseline", "envoy"):
        assert set(item["turn_correlation_ids"][arm]) == {
            "initial",
            "followup-1",
            "followup-2",
        }
        for turn_id in item["turn_correlation_ids"][arm].values():
            assert item["package_id"] in turn_id
            assert arm in turn_id


def test_plan_marks_underpowered_pilot_without_promoting_it_to_confirmatory(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pilot-a", "source_ids": ["source-a", "source-b"]},
        {"package_id": "pilot-b", "source_ids": ["source-c", "source-d"]},
    ])

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["package_count"] == 2
    assert plan["task_count"] == 6
    assert plan["unique_source_count"] == 4
    assert plan["confirmatory_minimum_met"] is False


def test_plan_records_cache_policy_and_platform_strata(tmp_path):
    records = [
        {
            "package_id": "bili-1",
            "source_ids": ["bili-a", "bili-b"],
            "source_class": "bilibili",
        },
        {
            "package_id": "wechat-1",
            "source_ids": ["wechat-a", "wechat-b"],
            "source_class": "wechat",
        },
    ]

    result = _run(tmp_path, records, "--cache-isolation-status", "verified_isolated")

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["cache_isolation_status"] == "verified_isolated"
    assert plan["source_class_package_counts"] == {"bilibili": 1, "wechat": 1}


def test_plan_records_per_request_salt_as_unverified_policy(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["source-a", "source-b"]},
    ], "--cache-isolation-status", "per_request_salt")

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["cache_isolation_status"] == "per_request_salt"
    assert plan["cache_policy"] == "per_request_salt; verify from completed authoritative request ledger"


def test_planner_cannot_mark_per_request_salt_as_verified(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["source-a", "source-b"]},
    ], "--cache-isolation-status", "verified_per_request_salt")

    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_fixed_confirmatory_profile_requires_approved_balanced_twenty(tmp_path):
    records = []
    for source_class in (
        "bilibili",
        "douyin",
        "wechat",
        "cross_platform",
    ):
        for index in range(5):
            package_id = f"{source_class}-{index}"
            records.append({
                "package_id": package_id,
                "source_ids": [f"{package_id}-a", f"{package_id}-b"],
                "source_class": source_class,
                "source_manifest_sha256": hashlib.sha256(
                    f"{package_id}-sources".encode()
                ).hexdigest(),
                "task_manifest_sha256": hashlib.sha256(
                    f"{package_id}-tasks".encode()
                ).hexdigest(),
            })

    result = _run(tmp_path, records, "--confirmatory-fixed")

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["evaluation_profile"] == "fixed_confirmatory_20"
    assert plan["cache_isolation_status"] == "unknown"
    assert plan["cache_isolation_verified"] is False
    assert plan["package_count"] == 20
    assert plan["unique_source_count"] == 40
    assert plan["confirmatory_minimum_met"] is True


def test_fixed_confirmatory_profile_requires_manifest_content_hashes(tmp_path):
    records = []
    for source_class in (
        "bilibili",
        "douyin",
        "wechat",
        "cross_platform",
    ):
        for index in range(5):
            package_id = f"{source_class}-{index}"
            records.append({
                "package_id": package_id,
                "source_ids": [f"{package_id}-a", f"{package_id}-b"],
                "source_class": source_class,
            })

    result = _run(tmp_path, records, "--confirmatory-fixed")

    assert result.returncode == 2
    assert "source_manifest_sha256" in result.stderr
    assert "task_manifest_sha256" in result.stderr


def test_fixed_confirmatory_profile_rejects_unbalanced_strata(tmp_path):
    records = [
        {
            "package_id": f"pkg-{index}",
            "source_ids": [f"src-{index}-a", f"src-{index}-b"],
            "source_class": "bilibili",
            "source_manifest_sha256": hashlib.sha256(
                f"source-{index}".encode()
            ).hexdigest(),
            "task_manifest_sha256": hashlib.sha256(
                f"task-{index}".encode()
            ).hexdigest(),
        }
        for index in range(20)
    ]

    result = _run(tmp_path, records, "--confirmatory-fixed")

    assert result.returncode == 2
    assert "5 packages in each approved stratum" in result.stderr


def test_plan_rejects_unknown_cache_policy(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["source-a", "source-b"]},
    ], "--cache-isolation-status", "definitely-cold")

    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_plan_rejects_duplicate_package_ids_and_duplicate_sources_within_package(tmp_path):
    duplicate_package = _run(tmp_path, [
        {"package_id": "same", "source_ids": ["src-a", "src-b"]},
        {"package_id": "same", "source_ids": ["src-c", "src-d"]},
    ])
    assert duplicate_package.returncode == 2
    assert "duplicate package_id" in duplicate_package.stderr.lower()

    duplicate_source = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["src-a", "src-a"]},
    ])
    assert duplicate_source.returncode == 2
    assert "duplicate source_id within package" in duplicate_source.stderr.lower()


def test_source_may_be_reused_across_task_packages(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["src-shared", "src-a"]},
        {"package_id": "pkg-b", "source_ids": ["src-shared", "src-b"]},
    ])

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["package_count"] == 2


def test_plan_requires_two_sources_per_package(tmp_path):
    result = _run(tmp_path, [
        {"package_id": "pkg-a", "source_ids": ["src-a"]},
    ])

    assert result.returncode == 2
    assert "at least two source_ids" in result.stderr


def test_plan_accepts_explicit_platform_stratum_and_task_manifest_reference(tmp_path):
    result = _run(tmp_path, [
        {
            "package_id": "douyin-cross-platform-001",
            "source_ids": ["douyin-1", "wechat-1"],
            "source_class": "cross_platform",
            "task_manifest_id": "manifest-v1-package-001",
        },
        {
            "package_id": "xhs-001",
            "source_ids": ["xhs-1", "xhs-2"],
            "source_class": "xiaohongshu",
            "task_manifest_id": "manifest-v1-package-002",
        },
    ])

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["schema_version"] == "envoy.paired-experiment-plan/2"
    planned = {item["package_id"]: item for item in plan["packages"]}
    assert planned["douyin-cross-platform-001"]["source_class"] == "cross_platform"
    assert (
        planned["douyin-cross-platform-001"]["task_manifest_id"]
        == "manifest-v1-package-001"
    )


def test_plan_rejects_invalid_stratum_and_empty_manifest_id(tmp_path):
    invalid_stratum = _run(tmp_path, [{
        "package_id": "pkg-a",
        "source_ids": ["source-a", "source-b"],
        "source_class": "unknown-platform",
    }])
    assert invalid_stratum.returncode == 2
    assert "source_class" in invalid_stratum.stderr

    empty_manifest = _run(tmp_path, [{
        "package_id": "pkg-b",
        "source_ids": ["source-a", "source-b"],
        "task_manifest_id": "",
    }])
    assert empty_manifest.returncode == 2
    assert "task_manifest_id" in empty_manifest.stderr
