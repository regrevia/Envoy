from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))


def test_ocr_cache_key_changes_with_profile_language_and_backend():
    from envoy_cli.ocr import ocr_cache_key

    base = ocr_cache_key("a" * 64, profile="balanced", language="eng", backend="auto")
    assert base != ocr_cache_key("a" * 64, profile="quality", language="eng", backend="auto")
    assert base != ocr_cache_key("a" * 64, profile="balanced", language="chi_sim", backend="auto")
    assert base != ocr_cache_key("a" * 64, profile="balanced", language="eng", backend="tesseract")


def test_tesseract_tsv_parser_preserves_order_bbox_and_confidence():
    from envoy_cli.ocr import _parse_tesseract_tsv

    result = _parse_tesseract_tsv(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t80\t20\t92.5\t深圳岗位\n"
        "5\t1\t1\t1\t2\t1\t10\t50\t50\t20\t-1\t\n",
        width=200,
        height=100,
    )

    assert result == [{
        "text": "深圳岗位",
        "bbox": [0.05, 0.2, 0.45, 0.4],
        "confidence": 0.925,
    }]


def test_extract_image_ocr_prefers_paddle_when_available(monkeypatch, tmp_path):
    from envoy_cli import ocr

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: {
        "paddleocr": True,
        "tesseract": True,
    })
    monkeypatch.setattr(ocr, "_run_paddle", lambda *_args, **_kwargs: [{
        "text": "岗位",
        "bbox": [0.1, 0.2, 0.4, 0.3],
        "confidence": 0.9,
    }])

    result = ocr.extract_image_ocr(image, profile="balanced", language="ch")

    assert result["backend"] == "paddleocr"
    assert result["text"] == "岗位"
    assert result["regions"][0]["bbox"] == [0.1, 0.2, 0.4, 0.3]


def test_extract_image_ocr_reports_missing_backend(monkeypatch, tmp_path):
    from envoy_cli import ocr

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: {
        "paddleocr": False,
        "tesseract": False,
    })

    with pytest.raises(RuntimeError, match="No .*OCR backend"):
        ocr.extract_image_ocr(image, profile="fast")


def test_ocr_cache_reuses_same_image_and_engine(monkeypatch, tmp_path):
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(reader, "available_ocr_backends", lambda: {
        "paddleocr": False,
        "tesseract": True,
    })
    monkeypatch.setattr(reader, "_engine_version", lambda _backend: "tesseract 5")
    calls = []
    monkeypatch.setattr(reader, "extract_image_ocr", lambda *_args, **_kwargs: (
        calls.append("ocr")
        or {
            "backend": "tesseract",
            "engine_version": "tesseract 5",
            "source_sha256": "a" * 64,
            "regions": [],
        }
    ))

    first = reader._read_cached_image_ocr(
        image, "a" * 64, profile="balanced", language="eng"
    )
    second = reader._read_cached_image_ocr(
        image, "a" * 64, profile="balanced", language="eng"
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert calls == ["ocr"]
