import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_ocr_profile_selects_small_v6_models(monkeypatch):
    from app.services import media_service

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    monkeypatch.setattr(media_service, "MEDIA_OCR_LANG", "ch")
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=FakePaddleOCR)}):
        media_service._run_ocr_worker_logic(["frame.jpg"])

    assert captured["text_detection_model_name"] == "PP-OCRv6_small_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv6_small_rec"
    assert captured["lang"] == "ch"


def test_ocr_profile_falls_back_for_old_paddleocr_signature(monkeypatch):
    from app.services import media_service

    calls = []

    class LegacyPaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            if "text_detection_model_name" in kwargs:
                raise TypeError("legacy signature")

        def ocr(self, _path, cls=True):
            return []

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "quality")
    monkeypatch.setattr(media_service, "MEDIA_OCR_LANG", "en")
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=LegacyPaddleOCR)}):
        media_service._run_ocr_worker_logic(["frame.jpg"])

    assert calls == [
        {
            "text_detection_model_name": "PP-OCRv5_server_det",
            "text_recognition_model_name": "PP-OCRv5_server_rec",
            "lang": "en",
        },
        {"use_angle_cls": True, "lang": "en"},
    ]


def test_ocr_runtime_profile_overrides_default_profile(monkeypatch):
    from app.services import media_service

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=FakePaddleOCR)}):
        media_service._run_ocr_worker_logic(["frame.jpg"], profile="quality")

    assert captured["text_detection_model_name"] == "PP-OCRv5_server_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv5_server_rec"


def test_ocr_quality_profile_uses_server_models(monkeypatch):
    from app.services import media_service

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=FakePaddleOCR)}):
        media_service._run_ocr_worker_logic(["frame.jpg"], profile="quality")

    assert captured["text_detection_model_name"] == "PP-OCRv5_server_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv5_server_rec"


def test_ocr_pro_profile_uses_current_best_general_models(monkeypatch):
    from app.services import media_service

    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=FakePaddleOCR)}):
        media_service._run_ocr_worker_logic(["frame.jpg"], profile="pro")

    assert captured["text_detection_model_name"] == "PP-OCRv6_medium_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv6_medium_rec"


def test_ocr_model_download_failure_is_reported_with_fallback_guidance(monkeypatch):
    from app.services import media_service

    class UnavailablePaddleOCR:
        def __init__(self, **_kwargs):
            raise RuntimeError("model download failed: proxy connection refused")

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    monkeypatch.setattr(media_service, "MEDIA_OCR_LANG", "ch")
    monkeypatch.setattr(media_service.shutil, "which", lambda name: None)
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=UnavailablePaddleOCR)}):
        result = media_service._run_ocr_worker_logic(["frame.jpg"], profile="pro")

    assert result[0]["text"] == ""
    assert result[0]["diagnostic"]["status"] == "model_unavailable"
    assert result[0]["diagnostic"]["requested_profile"] == "pro"
    assert result[0]["diagnostic"]["fallback_available"] is False
    assert "proxy connection refused" in result[0]["diagnostic"]["error"]
    assert result[0]["diagnostic"]["suggested_action"] == "ask_to_use_default_ocr"


def test_ocr_model_download_failure_reports_tesseract_fallback(monkeypatch):
    from app.services import media_service

    class UnavailablePaddleOCR:
        def __init__(self, **_kwargs):
            raise RuntimeError("model download failed")

    monkeypatch.setattr(media_service, "MEDIA_OCR_PROFILE", "balanced")
    monkeypatch.setattr(media_service, "MEDIA_OCR_LANG", "ch")
    monkeypatch.setattr(media_service.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    monkeypatch.setattr(media_service, "_run_tesseract_ocr_logic", lambda paths: [
        {"frame": path, "text": "default OCR text", "confidence": 0.7}
        for path in paths
    ])
    with patch.dict(sys.modules, {"paddleocr": types.SimpleNamespace(PaddleOCR=UnavailablePaddleOCR)}):
        result = media_service._run_ocr_worker_logic(["frame.jpg"], profile="pro")

    assert result[0]["text"] == "default OCR text"
    assert result[0]["diagnostic"]["status"] == "fallback"
    assert result[0]["diagnostic"]["fallback_engine"] == "tesseract"
    assert result[0]["diagnostic"]["suggested_action"] == "ask_to_use_default_ocr"


def test_ocr_failure_is_exposed_as_degraded_capability_and_user_action():
    from app.workers.jobs import _media_degraded_capabilities, _ocr_diagnostic_summary

    diagnostics = [{
        "status": "model_unavailable",
        "requested_profile": "pro",
        "error": "proxy connection refused",
        "suggested_action": "ask_to_use_default_ocr",
    }]
    degraded = _media_degraded_capabilities(
        {"success_count": 1, "failed_count": 0},
        False,
        "Transcript is available",
        "",
        [{"diagnostic": diagnostics[0]}],
    )

    assert "ocr_unavailable" in degraded
    assert "ocr_model_unavailable" in degraded
    summary = _ocr_diagnostic_summary(diagnostics)
    assert "pro OCR unavailable" in summary
    assert "proxy connection refused" in summary
    assert "ask whether to use default OCR" in summary


def test_empty_tesseract_fallback_asks_before_claiming_ocr_completed():
    from app.workers.jobs import _ocr_diagnostic_summary

    summary = _ocr_diagnostic_summary([{
        "status": "fallback",
        "requested_profile": "pro",
        "fallback_engine": "tesseract",
        "fallback_available": True,
        "fallback_output_empty": True,
        "error": "proxy connection refused",
    }])

    assert "Tesseract fallback returned no text" in summary
    assert "ask whether to use default OCR" in summary


def test_ocr_error_diagnostic_redacts_proxy_credentials_and_tokens():
    from app.services.media_service import _safe_ocr_error

    error = _safe_ocr_error(RuntimeError(
        "download failed at https://user:password@example.org/model?token=secret"
    ))

    assert "password" not in error
    assert "secret" not in error
    assert "[REDACTED]" in error


def test_task_ocr_runner_preserves_pro_profile(monkeypatch):
    from types import SimpleNamespace
    from app.workers import jobs

    captured = {}

    def fake_ocr(frame_paths, profile=None):
        captured["profile"] = profile
        return []

    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(input_config={"ocr_profile": "pro"})

    class DB:
        def query(self, *_args, **_kwargs):
            return Query()

    monkeypatch.setattr(jobs, "run_ocr_on_frames", fake_ocr)
    jobs._run_ocr_for_task(DB(), "task-pro", ["frame.jpg"])

    assert captured["profile"] == "pro"
