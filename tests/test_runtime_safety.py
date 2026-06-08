import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class RuntimeSafetyTests(unittest.TestCase):
    def test_media_commands_have_timeout(self):
        from app.services import media_service

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            media_service.split_video("input.mp4", "tmp/test-segments")
            media_service.extract_frames("input.mp4", "tmp/test-frames")

        self.assertEqual(len(calls), 2)
        for _cmd, kwargs in calls:
            self.assertIn("timeout", kwargs)
            self.assertGreater(kwargs["timeout"], 0)

    def test_ollama_returns_disabled_result_without_model(self):
        os.environ["OLLAMA_MODEL"] = ""
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        result = generate_with_ollama("Summarize this", "content")

        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["text"], "")

    def test_ollama_uses_configured_model_and_endpoint(self):
        os.environ["OLLAMA_BASE_URL"] = "http://ollama.local:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response": "better content"}

        class FakeClient:
            def __init__(self):
                self.calls = []

            def post(self, url, json, timeout):
                self.calls.append({"url": url, "json": json, "timeout": timeout})
                return FakeResponse()

        client = FakeClient()
        result = generate_with_ollama("Improve", "raw content", client=client)

        self.assertTrue(result["enabled"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], "better content")
        self.assertEqual(client.calls[0]["url"], "http://ollama.local:11434/api/generate")
        self.assertEqual(client.calls[0]["json"]["model"], "qwen2.5:7b")
        self.assertIn("raw content", client.calls[0]["json"]["prompt"])

    def test_storage_local_mode_skips_remote_client(self):
        os.environ["ENVOY_STORAGE_MODE"] = "local"
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.core.storage", None)
        from app.core.storage import StorageClient

        storage = StorageClient()
        result = storage.upload_bytes("sample.txt", b"hello", "text/plain")

        self.assertIsNone(storage.client)
        self.assertEqual(result["storage"], "local-placeholder")
        self.assertEqual(result["reason"], "local-storage-mode")


if __name__ == "__main__":
    unittest.main()
