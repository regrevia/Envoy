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


def test_downloaded_ocr_image_keeps_backend_supported_extension(tmp_path):
    import hashlib
    from envoy_cli.reader import _download_image_response

    payload = b"\x89PNG\r\n\x1a\nfixture"

    class FakeResponse:
        headers = {"content-type": "image/png; charset=binary"}

        def raise_for_status(self):
            pass

        def iter_bytes(self, chunk_size):
            assert chunk_size > 0
            yield payload

    image_path, image_sha256 = _download_image_response(FakeResponse(), tmp_path, 1024)

    assert image_path.suffix == ".png"
    assert image_path.read_bytes() == payload
    assert image_sha256 == hashlib.sha256(payload).hexdigest()


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


def test_tesseract_tsv_uses_reported_page_size_when_image_dimensions_unavailable():
    from envoy_cli.ocr import _parse_tesseract_tsv

    result = _parse_tesseract_tsv(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t1800\t220\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t180\t44\t720\t44\t92.5\t中文\n",
        width=1,
        height=1,
    )

    assert result == [{
        "text": "中文",
        "bbox": [0.1, 0.2, 0.5, 0.4],
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


def test_paddle_ocr_maps_tesseract_simplified_chinese_language_tag(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from envoy_cli import ocr

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(ocr, "_image_size", lambda _path: (100, 100))

    ocr._run_paddle(image, language="chi_sim", profile="balanced")

    assert captured["lang"] == "ch"


def test_paddle_ocr_maps_tesseract_traditional_chinese_language_tag(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from envoy_cli import ocr

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(ocr, "_image_size", lambda _path: (100, 100))

    ocr._run_paddle(image, language="chi_tra", profile="balanced")

    assert captured["lang"] == "chinese_cht"


def test_paddle_ocr_unwraps_paddlex_ocr_result_json(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from envoy_cli import ocr

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")

    class FakePaddleOCR:
        def __init__(self, **_kwargs):
            pass

        def predict(self, _path):
            return [SimpleNamespace(json={
                "res": {
                    "rec_texts": ["简体中文"],
                    "rec_scores": [0.97],
                    "rec_polys": [[
                        [10, 20],
                        [60, 20],
                        [60, 40],
                        [10, 40],
                    ]],
                },
            })]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(ocr, "_image_size", lambda _path: (100, 100))

    result = ocr._run_paddle(image, language="chi_sim", profile="balanced")

    assert result == [{
        "text": "简体中文",
        "bbox": [0.1, 0.2, 0.6, 0.4],
        "confidence": 0.97,
    }]


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


def test_direct_image_source_includes_local_ocr_and_visual_handoff(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ImageRegionLocator, MaterialSearchRequest, ReadRequest

    image = tmp_path / "source.jpg"
    image.write_bytes(b"image")
    source_url = "https://images.example.org/source.jpg"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("ENVOY_OCR_LANG", "chi_sim")
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 5,
    })
    monkeypatch.setattr(reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (
        image,
        "a" * 64,
    ))
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "tesseract",
        "engine_version": "tesseract 5.5.2",
        "regions": [{
            "text": "深圳岗位",
            "bbox": [0.1, 0.2, 0.5, 0.4],
            "confidence": 0.93,
        }, {
            "text": "招聘信息与岗位职责" * 10,
            "bbox": [0.1, 0.5, 0.9, 0.8],
            "confidence": 0.0,
        }],
    })
    request = ReadRequest.model_validate({
        "request_id": "direct-image-ocr",
        "source": {"url": source_url, "kind": "image"},
        "goal": "识别图片文字",
        "budget": {"max_response_tokens": 64},
        "ocr_profile": "balanced",
        "host_capabilities": {"image_input": "uri"},
    })

    response = reader._read_image_source(request, source_url, {})

    image_block = next(block for block in response.blocks if block.kind.value == "image")
    ocr_block = next(block for block in response.blocks if block.kind.value == "ocr")
    assert response.result.value == "needs_host"
    assert ocr_block.content.text == "深圳岗位"
    assert ocr_block.parent_id == image_block.id
    assert isinstance(ocr_block.locator, ImageRegionLocator)
    assert ocr_block.locator.resource_id == image_block.content.resource_id
    assert ocr_block.locator.bbox == [0.1, 0.2, 0.5, 0.4]
    assert response.resources[0].sha256 == "a" * 64
    assert response.coverage.modalities["ocr"].details["profile"] == "balanced"
    assert response.coverage.modalities["ocr"].details["language"] == "chi_sim"
    assert response.coverage.modalities["ocr"].details["cache_hit"] is False
    assert response.coverage.modalities["ocr"].extraction == "partial"
    assert response.coverage.modalities["ocr"].details["zero_confidence_regions"] == 1
    assert not any(
        block.kind.value == "ocr" and block.content.text.startswith("招聘信息")
        for block in response.blocks
    )

    searched = reader.search_material(MaterialSearchRequest.model_validate({
        "request_id": "direct-image-ocr-followup",
        "material_id": response.material_id,
        "query": "招聘信息",
    }))
    assert searched.blocks
    assert searched.blocks[0].content.text.startswith("招聘信息与岗位职责")
    assert isinstance(searched.blocks[0].locator, ImageRegionLocator)
    assert searched.blocks[0].locator.resource_id == image_block.content.resource_id
    assert searched.blocks[0].locator.bbox == [0.1, 0.5, 0.9, 0.8]
    assert searched.blocks[0].quality.engine_score == 0
    assert any("zero confidence" in item for item in searched.blocks[0].quality.unresolved)


def test_direct_image_ocr_works_without_host_image_capability(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    image = tmp_path / "source.jpg"
    image.write_bytes(b"image")
    source_url = "https://images.example.org/source.jpg"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 5,
    })
    monkeypatch.setattr(reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (
        image,
        "b" * 64,
    ))
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "tesseract",
        "engine_version": "tesseract 5.5.2",
        "regions": [{
            "text": "图片文字",
            "bbox": [0.2, 0.3, 0.7, 0.6],
            "confidence": 0.91,
        }],
    })
    request = ReadRequest.model_validate({
        "request_id": "ocr-only-image",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "balanced",
    })

    response = reader._read_image_source(request, source_url, {})

    assert response.result.value == "ready"
    assert response.execution_state.value == "completed"
    assert response.needs == []
    assert [block.content.text for block in response.blocks if block.kind.value == "ocr"] == [
        "图片文字"
    ]
    assert response.coverage.modalities["ocr"].extraction == "complete"


def test_direct_image_ocr_failure_preserves_host_visual_handoff(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://images.example.org/source.jpg"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 5,
    })
    monkeypatch.setattr(
        reader,
        "_download_image_for_ocr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )
    request = ReadRequest.model_validate({
        "request_id": "direct-image-ocr-failure",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "balanced",
        "host_capabilities": {"image_input": "uri"},
    })

    response = reader._read_image_source(request, source_url, {})

    assert response.result.value == "needs_host"
    assert len(response.resources) == 1
    assert response.needs[0].type == "inspect_visual"
    assert response.coverage.modalities["ocr"].extraction == "partial"
    assert response.coverage.modalities["ocr"].unresolved


def test_direct_image_blocks_when_ocr_fails_and_host_cannot_inspect(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://images.example.org/source.jpg"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 5,
    })
    monkeypatch.setattr(
        reader,
        "_download_image_for_ocr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )
    request = ReadRequest.model_validate({
        "request_id": "direct-image-ocr-unavailable",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "balanced",
    })

    response = reader._read_image_source(request, source_url, {})

    assert response.result.value == "blocked"
    assert response.execution_state.value == "completed"
    assert response.resources[0].resource_id == "resource-image"
    assert response.resources[0].delivery.uri == source_url
    assert response.needs == []
    assert response.errors[0].code.value == "empty_content"
    assert response.coverage.modalities["images"].delivered == 0


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


def test_ocr_cache_ignores_results_from_previous_pipeline_revision(monkeypatch, tmp_path):
    import json
    from envoy_cli import reader
    from envoy_cli.ocr import ocr_cache_key

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    image_sha256 = "b" * 64
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(reader, "available_ocr_backends", lambda: {
        "paddleocr": False,
        "tesseract": True,
    })
    monkeypatch.setattr(reader, "_engine_version", lambda _backend: "tesseract 5")
    cache_root = tmp_path / "materials" / "ocr-cache"
    cache_root.mkdir(parents=True)
    legacy_key = ocr_cache_key(
        image_sha256,
        profile="balanced",
        language="chi_sim",
        backend="tesseract",
        engine_version="tesseract 5",
    )
    (cache_root / f"{legacy_key}.json").write_text(json.dumps({
        "source_sha256": image_sha256,
        "regions": [],
        "legacy_bad_bbox": True,
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(reader, "extract_image_ocr", lambda *_args, **_kwargs: (
        calls.append("ocr")
        or {
            "backend": "tesseract",
            "engine_version": "tesseract 5",
            "source_sha256": image_sha256,
            "regions": [{"text": "深圳岗位", "bbox": [0.1, 0.2, 0.4, 0.3], "confidence": 0.9}],
        }
    ))

    result = reader._read_cached_image_ocr(
        image,
        image_sha256,
        profile="balanced",
        language="chi_sim",
    )

    assert calls == ["ocr"]
    assert result["regions"][0]["text"] == "深圳岗位"
    assert result["cache_hit"] is False


def test_ocr_cache_key_tracks_runtime_backend_version(monkeypatch, tmp_path):
    from envoy_cli import reader

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
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
            "source_sha256": "c" * 64,
            "regions": [{"text": "旧模型结果", "bbox": [0.1, 0.1, 0.4, 0.2], "confidence": 0.9}],
        }
    ))

    reader._read_cached_image_ocr(
        image,
        "c" * 64,
        profile="balanced",
        language="chi_sim",
    )
    monkeypatch.setattr(reader, "_engine_version", lambda _backend: "tesseract 6")
    monkeypatch.setattr(reader, "extract_image_ocr", lambda *_args, **_kwargs: (
        calls.append("ocr")
        or {
            "backend": "tesseract",
            "engine_version": "tesseract 6",
            "source_sha256": "c" * 64,
            "regions": [{"text": "新模型结果", "bbox": [0.1, 0.1, 0.4, 0.2], "confidence": 0.95}],
        }
    ))

    result = reader._read_cached_image_ocr(
        image,
        "c" * 64,
        profile="balanced",
        language="chi_sim",
    )

    assert calls == ["ocr", "ocr"]
    assert result["regions"][0]["text"] == "新模型结果"
    assert result["cache_hit"] is False


def test_ocr_cache_does_not_reuse_tesseract_after_paddleocr_becomes_available(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import reader

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    backends = {"paddleocr": False, "tesseract": True}
    monkeypatch.setattr(reader, "available_ocr_backends", lambda: dict(backends))
    monkeypatch.setattr(
        reader,
        "_engine_version",
        lambda backend: "paddle-1" if backend == "paddleocr" else "tesseract-5",
    )
    calls = []

    def extract(*_args, **_kwargs):
        backend = "paddleocr" if backends["paddleocr"] else "tesseract"
        calls.append(backend)
        return {
            "backend": backend,
            "engine_version": "paddle-1" if backend == "paddleocr" else "tesseract-5",
            "source_sha256": "d" * 64,
            "regions": [{
                "text": f"来自{backend}",
                "bbox": [0.1, 0.1, 0.4, 0.2],
                "confidence": 0.9,
            }],
        }

    monkeypatch.setattr(reader, "extract_image_ocr", extract)
    first = reader._read_cached_image_ocr(
        image,
        "d" * 64,
        profile="balanced",
        language="chi_sim",
    )
    backends["paddleocr"] = True
    upgraded = reader._read_cached_image_ocr(
        image,
        "d" * 64,
        profile="balanced",
        language="chi_sim",
    )

    assert first["backend"] == "tesseract"
    assert upgraded["backend"] == "paddleocr"
    assert upgraded["cache_hit"] is False
    assert calls == ["tesseract", "paddleocr"]
