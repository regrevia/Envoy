import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class AnalysisServiceTests(unittest.TestCase):
    def setUp(self):
        os.environ["OLLAMA_MODEL"] = ""
        for name in list(sys.modules):
            if name in {"app.core.config", "app.services.analysis_service", "app.services.ollama_service"}:
                sys.modules.pop(name, None)

    def test_analysis_payload_uses_stable_fallback_when_ollama_is_disabled(self):
        from app.services.analysis_service import build_analysis_payload

        payload = build_analysis_payload({"text": "raw transcript"}, asset_type="text")

        self.assertEqual(payload["stage"], "analyze")
        self.assertEqual(payload["llm"]["status"], "disabled")
        self.assertIn("scenes", payload)
        self.assertIn("questions", payload)
        self.assertGreaterEqual(payload["confidence"], 0.7)

    def test_analysis_payload_includes_ollama_summary_when_available(self):
        from app.services.analysis_service import build_analysis_payload

        with patch("app.services.analysis_service.generate_with_ollama") as generate:
            generate.return_value = {
                "enabled": True,
                "status": "success",
                "text": '{"summary": "本地模型增强摘要", "labels": ["安全", "流程"]}',
                "error": None,
            }

            payload = build_analysis_payload({"transcript": {"text": "一段较长的文本"}}, asset_type="video")

        self.assertEqual(payload["llm"]["status"], "success")
        self.assertEqual(payload["llm"]["summary"], "本地模型增强摘要")
        self.assertIn("安全", payload["labels"])
        self.assertIn("流程", payload["labels"])
        self.assertGreater(payload["confidence"], 0.8)

    def test_analysis_payload_records_ollama_failure_without_breaking_fallback(self):
        from app.services.analysis_service import build_analysis_payload

        with patch("app.services.analysis_service.generate_with_ollama") as generate:
            generate.return_value = {
                "enabled": True,
                "status": "failed",
                "text": "",
                "error": "connection refused",
            }

            payload = build_analysis_payload({"text": "content"}, asset_type="text")

        self.assertEqual(payload["llm"]["status"], "failed")
        self.assertIn("connection refused", payload["llm"]["error"])
        self.assertIn("scenes", payload)
        self.assertEqual(payload["stage"], "analyze")


if __name__ == "__main__":
    unittest.main()
