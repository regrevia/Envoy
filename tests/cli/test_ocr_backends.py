from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import ocr, reader  # noqa: E402
from envoy_core.schemas.read_v1 import ReadRequest  # noqa: E402


# ---------------------------------------------------------------------------
# Backend selection and availability
# ---------------------------------------------------------------------------

ALL_AVAILABLE = {
    "vision": True,
    "paddleocr": True,
    "rapidocr": True,
    "tesseract": True,
}
NONE_AVAILABLE = {name: False for name in ocr.OCR_BACKENDS}


def test_available_ocr_backends_reports_every_supported_backend(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setattr(
        ocr,
        "_module_available",
        lambda name: name in {"Vision", "Quartz", "paddleocr", "rapidocr_onnxruntime"},
    )
    monkeypatch.setattr(ocr.shutil, "which", lambda _name: "/opt/homebrew/bin/tesseract")

    assert ocr.available_ocr_backends() == ALL_AVAILABLE

    monkeypatch.setattr(ocr, "_module_available", lambda _name: False)
    monkeypatch.setattr(ocr.shutil, "which", lambda _name: None)
    assert ocr.available_ocr_backends() == NONE_AVAILABLE


def test_vision_is_unavailable_off_macos(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    monkeypatch.setattr(ocr, "_module_available", lambda _name: True)

    assert ocr._vision_available() is False
    assert ocr.available_ocr_backends()["vision"] is False


def test_auto_backend_priority_prefers_vision_then_falls_back():
    assert ocr.select_ocr_backend("auto", available=ALL_AVAILABLE) == "vision"
    assert ocr.select_ocr_backend(
        "auto", available={**ALL_AVAILABLE, "vision": False}
    ) == "paddleocr"
    assert ocr.select_ocr_backend(
        "auto", available={**ALL_AVAILABLE, "vision": False, "paddleocr": False}
    ) == "rapidocr"
    assert ocr.select_ocr_backend(
        "auto",
        available={
            **ALL_AVAILABLE,
            "vision": False,
            "paddleocr": False,
            "rapidocr": False,
        },
    ) == "tesseract"


def test_auto_backend_without_any_engine_explains_install_options():
    with pytest.raises(RuntimeError, match="No .*OCR backend") as error:
        ocr.select_ocr_backend("auto", available=NONE_AVAILABLE)

    message = str(error.value)
    assert "ocr-vision" in message
    assert "ocr-rapid" in message
    assert "paddleocr" in message
    assert "tesseract" in message


def test_explicit_unavailable_backend_reports_install_hint():
    with pytest.raises(RuntimeError, match="ocr-vision"):
        ocr.select_ocr_backend("vision", available=NONE_AVAILABLE)
    with pytest.raises(RuntimeError, match="ocr-rapid"):
        ocr.select_ocr_backend("rapidocr", available=NONE_AVAILABLE)
    with pytest.raises(RuntimeError, match="paddleocr"):
        ocr.select_ocr_backend("paddleocr", available=NONE_AVAILABLE)


def test_explicit_backend_is_honoured_even_when_vision_is_available():
    assert ocr.select_ocr_backend("tesseract", available=ALL_AVAILABLE) == "tesseract"
    assert ocr.select_ocr_backend(" RapidOCR ", available=ALL_AVAILABLE) == "rapidocr"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="Unsupported OCR backend"):
        ocr.select_ocr_backend("easyocr", available=ALL_AVAILABLE)
    with pytest.raises(ValueError, match="Unsupported OCR backend"):
        ocr.resolve_ocr_language("easyocr", "auto")


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------

def test_normalize_ocr_language_aliases():
    assert ocr.normalize_ocr_language(None) == "auto"
    assert ocr.normalize_ocr_language("  ") == "auto"
    for alias in ("ch", "zh", "zh-CN", "zh_Hans", "chi_sim", "chs", "cn", "chinese"):
        assert ocr.normalize_ocr_language(alias) == "zh-Hans", alias
    for alias in ("chi_tra", "zh-TW", "zh_Hant", "chinese_cht", "cht"):
        assert ocr.normalize_ocr_language(alias) == "zh-Hant", alias
    for alias in ("eng", "en-US", "en_us", "english"):
        assert ocr.normalize_ocr_language(alias) == "en", alias
    # Unknown tags pass through so existing backend-specific tags keep working.
    assert ocr.normalize_ocr_language("deu") == "deu"


def test_auto_language_resolves_to_chinese_and_english_per_backend():
    assert ocr.resolve_ocr_language("vision", "auto") == "zh-Hans+en-US"
    assert ocr.resolve_ocr_language("tesseract", "auto") == "chi_sim+eng"
    assert ocr.resolve_ocr_language("paddleocr", "auto") == "ch"
    assert ocr.resolve_ocr_language("rapidocr", "auto") == "ch"


def test_explicit_language_resolves_per_backend():
    assert ocr.resolve_ocr_language("vision", "zh-Hans") == "zh-Hans+en-US"
    assert ocr.resolve_ocr_language("vision", "zh-Hant") == "zh-Hant+en-US"
    assert ocr.resolve_ocr_language("vision", "eng") == "en-US"
    assert ocr.resolve_ocr_language("tesseract", "chi_sim") == "chi_sim+eng"
    assert ocr.resolve_ocr_language("tesseract", "chi_tra") == "chi_tra+eng"
    assert ocr.resolve_ocr_language("tesseract", "eng") == "eng"
    assert ocr.resolve_ocr_language("paddleocr", "zh-Hant") == "chinese_cht"
    assert ocr.resolve_ocr_language("rapidocr", "chi_tra") == "chinese_cht"


# ---------------------------------------------------------------------------
# extract_image_ocr records the resolved language/backend
# ---------------------------------------------------------------------------

def _fixture_image(tmp_path: Path) -> Path:
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    return image


def test_extract_image_ocr_records_resolved_language_and_backend(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    captured = {}

    def fake_vision(_path, *, language):
        captured["language"] = language
        return [{"text": "深圳", "bbox": [0.0, 0.0, 1.0, 1.0], "confidence": 1.0}]

    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: dict(ALL_AVAILABLE))
    monkeypatch.setattr(ocr, "_engine_version", lambda _backend: "engine-1")
    monkeypatch.setattr(ocr, "_run_vision", fake_vision)

    result = ocr.extract_image_ocr(image, profile="balanced", language="auto")

    assert result["backend"] == "vision"
    assert result["language"] == "zh-Hans+en-US"
    assert result["language_requested"] == "auto"
    assert result["engine_version"] == "engine-1"
    assert captured == {"language": "zh-Hans+en-US"}
    assert result["text"] == "深圳"


def test_extract_image_ocr_falls_back_to_rapidocr_and_resolves_chinese(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    captured = {}

    def fake_rapidocr(_path, *, language, profile):
        captured.update(language=language, profile=profile)
        return []

    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: {
        "vision": False,
        "paddleocr": False,
        "rapidocr": True,
        "tesseract": True,
    })
    monkeypatch.setattr(ocr, "_engine_version", lambda _backend: "engine-1")
    monkeypatch.setattr(ocr, "_run_rapidocr", fake_rapidocr)

    result = ocr.extract_image_ocr(image, profile="fast")

    assert result["backend"] == "rapidocr"
    assert result["language"] == "ch"
    assert captured == {"language": "ch", "profile": "fast"}


def test_extract_image_ocr_explicit_backend_uses_backend_language(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    captured = {}

    def fake_tesseract(_path, *, language):
        captured["language"] = language
        return []

    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: dict(ALL_AVAILABLE))
    monkeypatch.setattr(ocr, "_engine_version", lambda _backend: "engine-1")
    monkeypatch.setattr(ocr, "_run_tesseract", fake_tesseract)

    result = ocr.extract_image_ocr(
        image, profile="quality", language="chi_tra", backend="tesseract"
    )

    assert result["backend"] == "tesseract"
    assert result["language"] == "chi_tra+eng"
    assert captured == {"language": "chi_tra+eng"}


# ---------------------------------------------------------------------------
# Tesseract language availability
# ---------------------------------------------------------------------------

def test_adapt_tesseract_language_drops_missing_chinese_for_auto(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: frozenset({"eng"}))

    assert ocr._adapt_tesseract_language("chi_sim+eng", "auto") == "eng"


def test_adapt_tesseract_language_keeps_partially_available_auto_tokens(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: frozenset({"chi_sim"}))

    assert ocr._adapt_tesseract_language("chi_sim+eng", "auto") == "chi_sim"


def test_adapt_tesseract_language_keeps_explicit_requests(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: frozenset({"eng"}))

    assert ocr._adapt_tesseract_language("chi_sim+eng", "zh-Hans") == "chi_sim+eng"


def test_adapt_tesseract_language_leaves_auto_untouched_when_languages_unknown(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: None)

    assert ocr._adapt_tesseract_language("chi_sim+eng", "auto") == "chi_sim+eng"


def test_tesseract_languages_parses_installed_language_list(monkeypatch):
    class Completed:
        returncode = 0
        stdout = 'List of available languages in "/opt/homebrew/share/tessdata/" (2):\neng\nosd\n'
        stderr = ""

    monkeypatch.setattr(ocr.shutil, "which", lambda _name: "/opt/homebrew/bin/tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", lambda *args, **kwargs: Completed())
    ocr._tesseract_languages.cache_clear()
    try:
        assert ocr._tesseract_languages() == frozenset({"eng", "osd"})
    finally:
        ocr._tesseract_languages.cache_clear()


def test_extract_image_ocr_uses_installed_tesseract_language(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    captured = {}

    def fake_tesseract(_path, *, language):
        captured["language"] = language
        return []

    monkeypatch.setattr(ocr, "available_ocr_backends", lambda: {
        "vision": False,
        "paddleocr": False,
        "rapidocr": False,
        "tesseract": True,
    })
    monkeypatch.setattr(ocr, "_engine_version", lambda _backend: "engine-1")
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: frozenset({"eng"}))
    monkeypatch.setattr(ocr, "_run_tesseract", fake_tesseract)

    result = ocr.extract_image_ocr(image, profile="balanced")

    assert result["backend"] == "tesseract"
    assert result["language"] == "eng"
    assert result["language_requested"] == "auto"
    assert captured == {"language": "eng"}


# ---------------------------------------------------------------------------
# Vision backend
# ---------------------------------------------------------------------------

def test_vision_bbox_is_converted_to_top_left_origin():
    class Candidate:
        def string(self):
            return " 深圳 岗位 "

        def confidence(self):
            return 0.95

    class Observation:
        def topCandidates_(self, _limit):
            return [Candidate()]

        def boundingBox(self):
            return SimpleNamespace(
                origin=SimpleNamespace(x=0.1, y=0.2),
                size=SimpleNamespace(width=0.3, height=0.4),
            )

    regions = ocr._parse_vision_observations([Observation()])

    assert regions == [{
        "text": "深圳 岗位",
        # Vision y is bottom-left origin: top = 1 - 0.2 - 0.4 = 0.4.
        "bbox": [0.1, 0.4, 0.4, 0.8],
        "confidence": 0.95,
    }]


def test_vision_parser_skips_empty_and_degenerate_observations():
    class EmptyCandidate:
        def string(self):
            return "   "

        def confidence(self):
            return 1.0

    class DegenerateBox:
        def topCandidates_(self, _limit):
            return [EmptyCandidate()]

        def boundingBox(self):
            return SimpleNamespace(
                origin=SimpleNamespace(x=0.5, y=0.5),
                size=SimpleNamespace(width=0.0, height=0.0),
            )

    assert ocr._parse_vision_observations([DegenerateBox(), object()]) == []


def _fake_vision_modules(*, perform_outcome=True):
    observations = []

    class Candidate:
        def __init__(self, text, confidence):
            self._text = text
            self._confidence = confidence

        def string(self):
            return self._text

        def confidence(self):
            return self._confidence

    class Observation:
        def __init__(self, text, confidence, x, y, width, height):
            self._candidate = Candidate(text, confidence)
            self._box = SimpleNamespace(
                origin=SimpleNamespace(x=x, y=y),
                size=SimpleNamespace(width=width, height=height),
            )

        def topCandidates_(self, _limit):
            return [self._candidate]

        def boundingBox(self):
            return self._box

    observations.extend([
        Observation("深圳岗位", 0.96, 0.1, 0.2, 0.3, 0.4),
        Observation("English text", 0.9, 0.1, 0.7, 0.4, 0.1),
    ])

    class FakeRequest:
        created = []

        def __init__(self):
            self.recognition_level = None
            self.language_correction = None
            self.languages = None
            FakeRequest.created.append(self)

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setRecognitionLevel_(self, level):
            self.recognition_level = level

        def setUsesLanguageCorrection_(self, value):
            self.language_correction = value

        def setRecognitionLanguages_(self, languages):
            self.languages = list(languages)

        def results(self):
            return observations

    class FakeHandler:
        created = []

        def __init__(self):
            self.image = None
            self.options = None
            self.requests = None
            FakeHandler.created.append(self)

        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_options_(self, image, options):
            self.image = image
            self.options = options
            return self

        def performRequests_error_(self, requests, error):
            self.requests = list(requests)
            assert error is None
            return perform_outcome

    vision = SimpleNamespace(
        VNRecognizeTextRequest=FakeRequest,
        VNRequestTextRecognitionLevelAccurate=0,
        VNImageRequestHandler=FakeHandler,
    )
    quartz = SimpleNamespace(
        NSURL=SimpleNamespace(fileURLWithPath_=lambda path: ("url", path)),
        CGImageSourceCreateWithURL=lambda url, _options: ("source", url),
        CGImageSourceCreateImageAtIndex=lambda source, index, _options: (
            "image", source, index
        ),
    )
    return vision, quartz, FakeRequest, FakeHandler


def test_run_vision_uses_accurate_mode_and_language_preferences(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    vision, quartz, request_type, handler_type = _fake_vision_modules()
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Vision", vision)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    regions = ocr._run_vision(image, language="zh-Hans+en-US")

    request = request_type.created[-1]
    assert request.recognition_level == 0
    assert request.language_correction is True
    assert request.languages == ["zh-Hans", "en-US"]
    handler = handler_type.created[-1]
    assert handler.requests == [request]
    assert regions[0]["text"] == "深圳岗位"
    assert regions[0]["bbox"] == [0.1, 0.4, 0.4, 0.8]
    assert len(regions) == 2


def test_run_vision_surfaces_pyobjc_error_tuple(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    vision, quartz, _request_type, _handler_type = _fake_vision_modules(
        perform_outcome=(False, "unsupported image")
    )
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Vision", vision)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    with pytest.raises(RuntimeError, match="unsupported image"):
        ocr._run_vision(image, language="en-US")


def test_run_vision_missing_dependency_is_a_clear_runtime_error(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Vision", None)
    monkeypatch.setitem(sys.modules, "Quartz", None)

    with pytest.raises(RuntimeError, match="Vision OCR is not available"):
        ocr._run_vision(image, language="en-US")


def test_run_vision_requires_macos(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    monkeypatch.setattr(ocr.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="only available on macOS"):
        ocr._run_vision(image, language="en-US")


# ---------------------------------------------------------------------------
# RapidOCR backend
# ---------------------------------------------------------------------------

def test_rapidocr_parses_legacy_tuple_result(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)

    class FakeRapidOCR:
        def __call__(self, _path):
            return (
                [[
                    [[10, 20], [60, 20], [60, 40], [10, 40]],
                    "简体中文",
                    0.97,
                ]],
                [0.1],
            )

    monkeypatch.setitem(
        sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR)
    )
    monkeypatch.setattr(ocr, "_image_size", lambda _path: (100, 100))

    regions = ocr._run_rapidocr(image, language="ch", profile="balanced")

    assert regions == [{
        "text": "简体中文",
        "bbox": [0.1, 0.2, 0.6, 0.4],
        "confidence": 0.97,
    }]


def test_rapidocr_parses_newer_result_object_shape():
    result = SimpleNamespace(
        boxes=[[[0, 0], [50, 0], [50, 25], [0, 25]]],
        txts=["English"],
        scores=[0.5],
    )

    regions = ocr._parse_rapidocr_result(result, width=100, height=100)

    assert regions == [{
        "text": "English",
        "bbox": [0.0, 0.0, 0.5, 0.25],
        "confidence": 0.5,
    }]


def test_rapidocr_parser_accepts_plain_dict_shape():
    regions = ocr._parse_rapidocr_result(
        {
            "boxes": [[[0, 50], [100, 50], [100, 100], [0, 100]]],
            "txts": ["底部文字"],
            "scores": [0.8],
        },
        width=100,
        height=100,
    )

    assert regions[0]["text"] == "底部文字"
    assert regions[0]["bbox"] == [0.0, 0.5, 1.0, 1.0]
    assert regions[0]["confidence"] == pytest.approx(0.8)


def test_rapidocr_passes_supported_language_constructor_argument(monkeypatch):
    captured = {}

    class FakeRapidOCR:
        def __init__(self, lang="ch"):
            captured["lang"] = lang

    monkeypatch.setitem(
        sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR)
    )

    ocr._create_rapidocr_engine(language="chinese_cht", profile="balanced")

    assert captured == {"lang": "chinese_cht"}


def test_rapidocr_missing_dependency_is_a_clear_runtime_error(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    monkeypatch.setitem(sys.modules, "rapidocr", None)

    with pytest.raises(RuntimeError, match="RapidOCR is not installed"):
        ocr._run_rapidocr(image, language="ch", profile="balanced")


# ---------------------------------------------------------------------------
# Reader integration: env vars, resolved language, cache keys
# ---------------------------------------------------------------------------

def test_reader_cache_resolves_language_before_calling_backend(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    image_sha256 = "a" * 64
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "available_ocr_backends", lambda: dict(ALL_AVAILABLE))
    monkeypatch.setattr(reader, "_engine_version", lambda _backend: "engine-1")
    calls = []
    resolved_languages = {"auto": "zh-Hans+en-US", "eng": "en-US"}

    def fake_extract(_path, *, profile, language, backend, available):
        calls.append({
            "profile": profile,
            "language": language,
            "backend": backend,
            "available": available,
        })
        return {
            "backend": backend,
            "engine_version": "engine-1",
            "profile": profile,
            "language": resolved_languages[language],
            "regions": [],
            "source_sha256": image_sha256,
        }

    monkeypatch.setattr(reader, "extract_image_ocr", fake_extract)

    first = reader._read_cached_image_ocr(
        image, image_sha256, profile="balanced", language="auto"
    )
    second = reader._read_cached_image_ocr(
        image, image_sha256, profile="balanced", language="auto"
    )

    assert first["cache_hit"] is False
    assert first["language"] == "zh-Hans+en-US"
    assert second["cache_hit"] is True
    # The reader keeps passing the requested language so backends can apply
    # their own resolution (e.g. Tesseract traineddata fallback).
    assert calls == [{
        "profile": "balanced",
        "language": "auto",
        "backend": "vision",
        "available": ALL_AVAILABLE,
    }]

    reader._read_cached_image_ocr(
        image, image_sha256, profile="balanced", language="eng"
    )

    assert calls[-1]["language"] == "eng"
    assert calls[-1]["backend"] == "vision"


def test_old_english_cache_does_not_pollute_auto_chinese_cache(monkeypatch, tmp_path):
    import json

    image = _fixture_image(tmp_path)
    image_sha256 = "b" * 64
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "available_ocr_backends", lambda: {
        "vision": False,
        "paddleocr": False,
        "rapidocr": False,
        "tesseract": True,
    })
    monkeypatch.setattr(reader, "_engine_version", lambda _backend: "tesseract 5")
    cache_root = tmp_path / "materials" / "ocr-cache"
    cache_root.mkdir(parents=True)
    legacy_key = ocr.ocr_cache_key(
        image_sha256,
        profile="balanced",
        language="eng",
        backend="tesseract",
        engine_version=f"tesseract 5+{ocr.OCR_PIPELINE_REVISION}",
    )
    (cache_root / f"{legacy_key}.json").write_text(
        json.dumps({"source_sha256": image_sha256, "regions": [{"text": "old-eng"}]}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(reader, "extract_image_ocr", lambda *_args, **_kwargs: (
        calls.append("ocr")
        or {
            "backend": "tesseract",
            "engine_version": "tesseract 5",
            "language": "chi_sim+eng",
            "regions": [{"text": "中文结果", "bbox": [0.0, 0.0, 1.0, 1.0], "confidence": 0.9}],
            "source_sha256": image_sha256,
        }
    ))

    result = reader._read_cached_image_ocr(
        image, image_sha256, profile="balanced", language="auto"
    )

    assert calls == ["ocr"]
    assert result["cache_hit"] is False
    assert result["language"] == "chi_sim+eng"


def test_reader_reads_backend_and_language_from_environment(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    source_url = "https://images.example.org/source.jpg"
    captured = {}
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("ENVOY_OCR_BACKEND", "RapidOCR")
    monkeypatch.setenv("ENVOY_OCR_LANG", "chi_tra")
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 7,
    })
    monkeypatch.setattr(
        reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (image, "c" * 64)
    )

    def fake_read_cached(_path, _digest, *, profile, language, backend):
        captured.update(profile=profile, language=language, backend=backend)
        return {"backend": "rapidocr", "regions": []}

    monkeypatch.setattr(reader, "_read_cached_image_ocr", fake_read_cached)
    request = ReadRequest.model_validate({
        "request_id": "ocr-env-defaults",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "balanced",
    })

    reader._read_image_source(request, source_url, {})

    assert captured == {
        "profile": "balanced",
        "language": "chi_tra",
        "backend": "rapidocr",
    }


def test_reader_request_fields_override_ocr_environment(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    source_url = "https://images.example.org/source.jpg"
    captured = {}
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("ENVOY_OCR_BACKEND", "rapidocr")
    monkeypatch.setenv("ENVOY_OCR_LANG", "chi_tra")
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 7,
    })
    monkeypatch.setattr(
        reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (image, "d" * 64)
    )

    def fake_read_cached(_path, _digest, *, profile, language, backend):
        captured.update(profile=profile, language=language, backend=backend)
        return {"backend": "tesseract", "regions": []}

    monkeypatch.setattr(reader, "_read_cached_image_ocr", fake_read_cached)
    request = ReadRequest.model_validate({
        "request_id": "ocr-request-overrides",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "fast",
        "ocr_backend": "tesseract",
        "ocr_language": "eng",
    })

    reader._read_image_source(request, source_url, {})

    assert captured == {
        "profile": "fast",
        "language": "eng",
        "backend": "tesseract",
    }


def test_direct_image_coverage_records_resolved_language(monkeypatch, tmp_path):
    image = _fixture_image(tmp_path)
    source_url = "https://images.example.org/source.jpg"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "_fetch_source", lambda *_args: {
        "url": source_url,
        "mime_type": "image/jpeg",
        "size_bytes": 7,
    })
    monkeypatch.setattr(
        reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (image, "e" * 64)
    )
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "vision",
        "engine_version": "pyobjc-vision-11",
        "language": "zh-Hans+en-US",
        "regions": [{
            "text": "深圳岗位",
            "bbox": [0.1, 0.2, 0.5, 0.4],
            "confidence": 0.93,
        }],
    })
    request = ReadRequest.model_validate({
        "request_id": "ocr-resolved-language",
        "source": {"url": source_url, "kind": "image"},
        "ocr_profile": "balanced",
        "ocr_backend": "vision",
    })

    response = reader._read_image_source(request, source_url, {})

    details = response.coverage.modalities["ocr"].details
    assert details["language"] == "zh-Hans+en-US"
    assert details["profile"] == "balanced"


# ---------------------------------------------------------------------------
# Protocol surface
# ---------------------------------------------------------------------------

def test_read_request_accepts_ocr_backend_enum():
    request = ReadRequest.model_validate({
        "request_id": "ocr-backend-field",
        "source": {"url": "https://example.org/image.jpg", "kind": "image"},
        "ocr_backend": "vision",
    })
    assert request.ocr_backend == "vision"

    default = ReadRequest.model_validate({
        "request_id": "ocr-backend-default",
        "source": {"url": "https://example.org/image.jpg", "kind": "image"},
    })
    assert default.ocr_backend is None


def test_read_request_rejects_unknown_ocr_backend():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReadRequest.model_validate({
            "request_id": "ocr-backend-invalid",
            "source": {"url": "https://example.org/image.jpg", "kind": "image"},
            "ocr_backend": "easyocr",
        })


def test_mcp_envoy_read_schema_exposes_ocr_backend():
    from envoy_cli import mcp

    tool = next(item for item in mcp.TOOLS if item["name"] == "envoy_read")
    schema = tool["inputSchema"]["properties"]["ocr_backend"]

    assert schema["enum"] == [
        "auto",
        "vision",
        "paddleocr",
        "rapidocr",
        "tesseract",
        None,
    ]
    assert "auto" in schema["description"]
