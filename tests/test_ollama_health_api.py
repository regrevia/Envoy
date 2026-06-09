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


if __name__ == "__main__":
    unittest.main()
