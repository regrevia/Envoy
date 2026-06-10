import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def clear_app_modules():
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)


class OllamaHealthApiTests(unittest.TestCase):
    def test_ollama_health_reports_disabled_without_network_probe(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-ollama-disabled.db'}"
            os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
            os.environ["OLLAMA_MODEL"] = ""
            os.environ["OLLAMA_TIMEOUT_SEC"] = "120"
            clear_app_modules()

            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app)
            with patch("app.services.ollama_service.httpx.get") as get:
                response = client.get("/api/v1/health/ollama")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(
                body,
                {
                    "enabled": False,
                    "status": "disabled",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "",
                    "timeout_sec": 120,
                    "model_available": False,
                    "available_models": [],
                    "error": None,
                },
            )
            get.assert_not_called()

    def test_ollama_health_probes_tags_when_model_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-ollama-enabled.db'}"
            os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
            os.environ["OLLAMA_TIMEOUT_SEC"] = "5"
            clear_app_modules()

            from fastapi.testclient import TestClient
            from app.main import app

            tags_response = Mock()
            tags_response.raise_for_status.return_value = None
            tags_response.json.return_value = {
                "models": [
                    {"name": "qwen2.5:7b"},
                    {"name": "nomic-embed-text"},
                ]
            }

            client = TestClient(app)
            with patch("app.services.ollama_service.httpx.get", return_value=tags_response) as get:
                response = client.get("/api/v1/health/ollama")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["enabled"])
            self.assertEqual(body["status"], "available")
            self.assertEqual(body["base_url"], "http://127.0.0.1:11434")
            self.assertEqual(body["model"], "qwen2.5:7b")
            self.assertEqual(body["timeout_sec"], 5)
            self.assertTrue(body["model_available"])
            self.assertEqual(body["available_models"], ["qwen2.5:7b", "nomic-embed-text"])
            self.assertIsNone(body["error"])
            get.assert_called_once_with("http://127.0.0.1:11434/api/tags", timeout=5)

    def test_media_health_reports_runtime_processing_limits(self):
        keys = (
            "MEDIA_SEGMENT_SECONDS",
            "MEDIA_MAX_SEGMENT_COUNT",
            "MEDIA_FRAME_INTERVAL_SECONDS",
            "MEDIA_MAX_FRAME_COUNT",
            "MEDIA_ENABLE_FRAME_EXTRACTION",
            "MEDIA_ENABLE_OCR",
            "MEDIA_TRANSCRIPTION_TIMEOUT_SEC",
            "MEDIA_OCR_TIMEOUT_SEC",
        )
        previous = {key: os.environ.get(key) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-media-health.db'}"
                os.environ["MEDIA_SEGMENT_SECONDS"] = "300"
                os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "3"
                os.environ["MEDIA_FRAME_INTERVAL_SECONDS"] = "20"
                os.environ["MEDIA_MAX_FRAME_COUNT"] = "40"
                os.environ["MEDIA_ENABLE_FRAME_EXTRACTION"] = "false"
                os.environ["MEDIA_ENABLE_OCR"] = "false"
                os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = "120"
                os.environ["MEDIA_OCR_TIMEOUT_SEC"] = "45"
                clear_app_modules()

                from fastapi.testclient import TestClient
                from app.main import app

                client = TestClient(app)
                response = client.get("/api/v1/health/media")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "segment_seconds": 300,
                        "max_segment_count": 3,
                        "max_input_seconds": 900,
                        "frame_interval_seconds": 20,
                        "max_frame_count": 40,
                        "frame_extraction_enabled": False,
                        "ocr_enabled": False,
                        "transcription_timeout_sec": 120,
                        "ocr_timeout_sec": 45,
                    },
                )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            clear_app_modules()


if __name__ == "__main__":
    unittest.main()
