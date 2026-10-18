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
