import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def reload_backend_modules(*names: str):
    for name in names:
        sys.modules.pop(name, None)
    return [importlib.import_module(name) for name in names]


class ExportConfigurationTests(unittest.TestCase):
    def test_numeric_configuration_falls_back_when_environment_is_invalid(self):
        values = {
            "OLLAMA_TIMEOUT_SEC": "abc",
            "TASK_STALE_AFTER_SECONDS": "0",
            "MEDIA_COMMAND_TIMEOUT_SEC": "",
            "MEDIA_TRANSCRIPTION_TIMEOUT_SEC": "bad",
            "MEDIA_SEGMENT_SECONDS": "-1",
            "MEDIA_MAX_SEGMENT_COUNT": "0",
            "MEDIA_FRAME_INTERVAL_SECONDS": "bad",
            "MEDIA_MAX_FRAME_COUNT": "0",
            "TEXT_PREPROCESS_CHUNK_CHARS": "-20",
            "TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS": "bad",
            "SAMPLE_GENERATION_BATCH_LIMIT": "0",
        }
        previous = {key: os.environ.get(key) for key in values}
        try:
            os.environ.update(values)
            config = reload_backend_modules("app.core.config")[0]

            self.assertEqual(config.OLLAMA_TIMEOUT_SEC, 120)
            self.assertEqual(config.TASK_STALE_AFTER_SECONDS, 1800)
            self.assertEqual(config.MEDIA_COMMAND_TIMEOUT_SEC, 300)
            self.assertEqual(config.MEDIA_TRANSCRIPTION_TIMEOUT_SEC, 900)
            self.assertEqual(config.MEDIA_SEGMENT_SECONDS, 600)
            self.assertEqual(config.MEDIA_MAX_SEGMENT_COUNT, 12)
            self.assertEqual(config.MEDIA_FRAME_INTERVAL_SECONDS, 15)
            self.assertEqual(config.MEDIA_MAX_FRAME_COUNT, 240)
            self.assertEqual(config.TEXT_PREPROCESS_CHUNK_CHARS, 6000)
            self.assertEqual(config.TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS, 400)
            self.assertEqual(config.SAMPLE_GENERATION_BATCH_LIMIT, 50)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            sys.modules.pop("app.core.config", None)

    def test_export_dir_comes_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["ENVOY_EXPORT_DIR"] = tmp_dir
            config, paths = reload_backend_modules("app.core.config", "app.core.paths")

            self.assertEqual(Path(config.EXPORT_DIR), Path(tmp_dir))
            self.assertEqual(paths.EXPORT_DIR, Path(tmp_dir))
            self.assertTrue(paths.EXPORT_DIR.exists())

    def test_ollama_configuration_comes_from_environment(self):
        os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
        config = reload_backend_modules("app.core.config")[0]

        self.assertEqual(config.OLLAMA_BASE_URL, "http://127.0.0.1:11434")
        self.assertEqual(config.OLLAMA_MODEL, "qwen2.5:7b")
        self.assertEqual(config.OLLAMA_TIMEOUT_SEC, 120)

    def test_export_markdown_uses_readable_chinese_headings(self):
        from app.services.train_export_service import export_markdown_from_video_summary, export_skill_markdown

        with tempfile.TemporaryDirectory() as tmp_dir:
            skill = export_skill_markdown([{"topic": "alpha"}], tmp_dir, "task-001", "Skill Title")
            report = export_markdown_from_video_summary([{"summary": "beta"}], tmp_dir, "task-002", "Report Title")

            skill_text = Path(skill["path"]).read_text(encoding="utf-8")
            skill_manifest = json.loads(Path(skill["paths"]["manifest"]).read_text(encoding="utf-8"))
            report_text = Path(report["path"]).read_text(encoding="utf-8")

            self.assertIn("## 输入摘要", skill_text)
            self.assertIn("样本 1", skill_text)
            self.assertEqual(skill_manifest["schema_version"], "envoy.skill.v1")
            self.assertEqual(skill_manifest["title"], "Skill Title")
            self.assertEqual(skill_manifest["source_task_id"], "task-001")
            self.assertEqual(skill_manifest["materials"][0]["topic"], "alpha")
            self.assertIn("## 视频转化摘要", report_text)
            self.assertIn("task-001", skill["root"])
            self.assertIn("task-002", report["root"])

    def test_export_manifest_ignores_invalid_media_limit_metadata(self):
        from app.services.train_export_service import export_markdown_from_video_summary

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = export_markdown_from_video_summary(
                [
                    {
                        "unit_type": "transcript",
                        "content": "valid transcript should still export",
                        "labels": {
                            "source": "media_transcript",
                            "segment_seconds": "bad",
                            "max_segment_count": 2,
                            "max_input_seconds": 600,
                        },
                    }
                ],
                tmp_dir,
                "task-invalid-media-limit",
                "Invalid Media Limit",
            )

            manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))

            self.assertEqual(manifest["row_count"], 1)
            self.assertEqual(manifest["source_counts"], {"media_transcript": 1})
            self.assertEqual(manifest["unit_type_counts"], {"transcript": 1})
            self.assertNotIn("media_processing_limits", manifest)

    def test_worker_export_jobs_use_configured_export_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["ENVOY_EXPORT_DIR"] = tmp_dir
            reload_backend_modules("app.core.config", "app.core.paths")
            jobs = reload_backend_modules("app.workers.jobs")[0]

            self.assertEqual(jobs.EXPORT_DIR, Path(tmp_dir))


if __name__ == "__main__":
    unittest.main()
