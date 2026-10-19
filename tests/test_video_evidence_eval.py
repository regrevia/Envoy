import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "video_evidence_eval.py"


def test_video_evidence_eval_measures_coverage_calls_and_host_usage_status(tmp_path):
    usage_file = tmp_path / "host-usage.json"
    usage_file.write_text(json.dumps({
        "dodge-cue": {
            "provider": "test-host",
            "model": "test-model",
            "input_tokens": 1200,
            "output_tokens": 88,
        },
        "phase-change": {
            "provider": "test-host",
            "model": "test-model",
            "input_tokens": 1340,
            "output_tokens": 102,
        },
    }), encoding="utf-8")
    materials_dir = tmp_path / "materials"
    output = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--materials-dir",
            str(materials_dir),
            "--host-usage-file",
            str(usage_file),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "packages" / "envoy-core" / "src"),
                str(ROOT / "packages" / "envoy-cli" / "src"),
            )),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert output.returncode == 0, output.stdout + output.stderr
    report = json.loads(output.stdout)
    assert report["schema_version"] == "envoy.video-evidence-eval/1"
    assert report["tool_calls"] == 2
    assert report["coverage_rate"] == 1.0
    assert report["returned_chars"] < report["full_scan_baseline_chars"]
    assert report["host_actual_input_tokens"] == 2540
    assert report["host_usage_status"] == "actual"
    assert all(
        any(locator["locator_type"] == "video_time" for locator in item["locators"])
        for item in report["cases"]
    )


def test_video_evidence_eval_does_not_fabricate_actual_host_usage(tmp_path):
    output = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--materials-dir",
            str(tmp_path / "materials"),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "packages" / "envoy-core" / "src"),
                str(ROOT / "packages" / "envoy-cli" / "src"),
            )),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert output.returncode == 0, output.stdout + output.stderr
    report = json.loads(output.stdout)
    assert report["host_usage_status"] == "unknown"
    assert report["host_actual_input_tokens"] is None
    assert report["host_actual_output_tokens"] is None
