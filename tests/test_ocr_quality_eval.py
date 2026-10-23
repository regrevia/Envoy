import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "ocr_quality_eval.py"


def test_ocr_quality_eval_reports_cjk_cer_and_omits_text_from_report(tmp_path):
    records = tmp_path / "ocr.jsonl"
    records.write_text(
        "\n".join([
            json.dumps({
                "sample_id": "xhs-note-1",
                "source_id": "xhs-public-1",
                "backend": "tesseract",
                "source_sha256": "a" * 64,
                "reference_text": "深圳岗位 招聘两人",
                "recognized_text": "深圳岗位 招聘二人",
            }, ensure_ascii=False),
            json.dumps({
                "sample_id": "english-1",
                "source_id": "article-image-1",
                "backend": "paddleocr",
                "source_sha256": "b" * 64,
                "reference_text": "Open the settings panel",
                "recognized_text": "Open settings panel",
            }, ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8",
    )

    output = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )

    assert output.returncode == 0, output.stdout + output.stderr
    report = json.loads(output.stdout)
    assert report["schema_version"] == "envoy.ocr-quality-eval/1"
    assert report["sample_count"] == 2
    assert report["micro_cer"] == 5 / 32
    assert report["macro_cer"] == (1 / 9 + 4 / 23) / 2
    assert report["macro_wer"] == 1 / 4
    assert report["samples"][0]["wer"] is None
    assert report["samples"][0]["cer"] == 1 / 9
    assert "深圳岗位" not in output.stdout
    assert "招聘二人" not in output.stdout


def test_ocr_quality_eval_counts_empty_recognition_as_complete_error(tmp_path):
    records = tmp_path / "ocr.jsonl"
    records.write_text(json.dumps({
        "sample_id": "empty-result",
        "source_id": "public-image",
        "backend": "tesseract",
        "source_sha256": "c" * 64,
        "reference_text": "深圳岗位",
        "recognized_text": "",
    }, ensure_ascii=False), encoding="utf-8")

    output = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert output.returncode == 0, output.stdout + output.stderr
    report = json.loads(output.stdout)
    assert report["micro_cer"] == 1.0
    assert report["samples"][0]["cer"] == 1.0
    assert report["samples"][0]["recognized_characters"] == 0


def test_ocr_quality_eval_rejects_duplicate_sample_ids(tmp_path):
    records = tmp_path / "ocr.jsonl"
    record = {
        "sample_id": "duplicate",
        "source_id": "public-image",
        "backend": "tesseract",
        "source_sha256": "d" * 64,
        "reference_text": "visible text",
        "recognized_text": "visible text",
    }
    records.write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n",
        encoding="utf-8",
    )

    output = subprocess.run(
        [sys.executable, str(SCRIPT), "--records", str(records)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert output.returncode == 2
    assert "Duplicate sample_id" in output.stderr
