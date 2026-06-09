import os
import sys
import tempfile
import unittest
import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiArtifactsFlowTests(unittest.TestCase):
    def test_artifact_list_detail_and_create_return_traceable_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_path = Path(tmp_dir) / "skill.md"
            artifact_path.write_text("# Skill\n", encoding="utf-8")
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-artifacts-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import engine
            import app.models  # noqa: F401
            from app.main import app

            Base.metadata.create_all(bind=engine)
            try:
                client = TestClient(app)
                create_response = client.post(
                    "/api/v1/artifacts",
                    json={
                        "project_id": "project-artifact-001",
                        "artifact_type": "skill",
                        "name": "skill.md",
                        "file_path": str(artifact_path),
                        "version": "v1",
                    },
                )
                self.assertEqual(create_response.status_code, 200)
                created = create_response.json()
                artifact_id = created["id"]

                list_response = client.get("/api/v1/artifacts")
                detail_response = client.get(f"/api/v1/artifacts/{artifact_id}")
                download_response = client.get(f"/api/v1/artifacts/{artifact_id}/download")

                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(download_response.status_code, 200)
                listed = list_response.json()[0]
                detail = detail_response.json()

                for payload in (created, listed, detail):
                    self.assertEqual(payload["id"], artifact_id)
                    self.assertEqual(payload["project_id"], "project-artifact-001")
                    self.assertEqual(payload["artifact_type"], "skill")
                    self.assertEqual(payload["name"], "skill.md")
                    self.assertEqual(payload["file_path"], str(artifact_path))
                    self.assertEqual(payload["version"], "v1")
                    self.assertIsNotNone(payload["created_at"])

                self.assertIn("# Skill", download_response.text)
            finally:
                engine.dispose()

    def test_artifact_detail_includes_export_manifest_quality_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_dir = Path(tmp_dir) / "skill"
            artifact_dir.mkdir()
            manifest_path = artifact_dir / "skill.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "envoy.skill.v1",
                        "source_task_id": "task-skill-001",
                        "task_name": "客服知识转 Skill",
                        "project_id": "project-manifest-001",
                        "ollama_status": "success",
                        "ollama_truncated": True,
                        "ollama_original_prompt_chars": 24000,
                        "ollama_sent_prompt_chars": 12000,
                        "ollama_max_prompt_chars": 12000,
                        "llm_summary": "Skill package is usable for refund guidance.",
                        "llm_usage": "Support agent training",
                        "row_count": 3,
                        "source_counts": {"text_asset": 1, "media_transcript": 1, "media_ocr": 1},
                        "unit_type_counts": {"text": 1, "transcript": 1, "ocr": 1},
                        "media_processing_limits": [
                            {
                                "segment_seconds": 300,
                                "max_segment_count": 2,
                                "max_input_seconds": 600,
                                "unit_count": 2,
                            }
                        ],
                        "files": {"markdown": str(artifact_dir / "skill.md"), "manifest": str(manifest_path)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-artifacts-manifest-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import engine
            import app.models  # noqa: F401
            from app.main import app

            Base.metadata.create_all(bind=engine)
            try:
                client = TestClient(app)
                create_response = client.post(
                    "/api/v1/artifacts",
                    json={
                        "project_id": "project-manifest-001",
                        "artifact_type": "skill",
                        "name": "skill.json",
                        "file_path": str(manifest_path),
                        "version": "v1",
                    },
                )
                artifact_id = create_response.json()["id"]
                detail_response = client.get(f"/api/v1/artifacts/{artifact_id}")

                self.assertEqual(detail_response.status_code, 200)
                detail = detail_response.json()
                self.assertEqual(detail["manifest"]["schema_version"], "envoy.skill.v1")
                self.assertEqual(detail["manifest"]["source_task_id"], "task-skill-001")
                self.assertEqual(detail["manifest"]["task_name"], "客服知识转 Skill")
                self.assertEqual(detail["manifest"]["ollama_status"], "success")
                self.assertTrue(detail["manifest"]["ollama_truncated"])
                self.assertEqual(detail["manifest"]["ollama_original_prompt_chars"], 24000)
                self.assertEqual(detail["manifest"]["ollama_sent_prompt_chars"], 12000)
                self.assertEqual(detail["manifest"]["ollama_max_prompt_chars"], 12000)
                self.assertEqual(detail["manifest"]["llm_summary"], "Skill package is usable for refund guidance.")
                self.assertEqual(detail["manifest"]["llm_usage"], "Support agent training")
                self.assertEqual(detail["manifest"]["row_count"], 3)
                self.assertEqual(detail["manifest"]["source_counts"], {"text_asset": 1, "media_transcript": 1, "media_ocr": 1})
                self.assertEqual(detail["manifest"]["unit_type_counts"], {"text": 1, "transcript": 1, "ocr": 1})
                self.assertEqual(
                    detail["manifest"]["media_processing_limits"],
                    [{"segment_seconds": 300, "max_segment_count": 2, "max_input_seconds": 600, "unit_count": 2}],
                )
                self.assertEqual(detail["manifest"]["manifest_path"], str(manifest_path))
            finally:
                engine.dispose()

    def test_artifact_detail_filters_invalid_manifest_media_limits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_dir = Path(tmp_dir) / "markdown"
            artifact_dir.mkdir()
            manifest_path = artifact_dir / "markdown_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "envoy.markdown.v1",
                        "project_id": "project-invalid-media-limits",
                        "row_count": 1,
                        "media_processing_limits": [
                            "not-a-limit",
                            {
                                "segment_seconds": "bad",
                                "max_segment_count": 2,
                                "max_input_seconds": 600,
                                "unit_count": 2,
                            },
                            {
                                "segment_seconds": 300,
                                "max_segment_count": -1,
                                "max_input_seconds": 600,
                                "unit_count": 2,
                            },
                            {
                                "segment_seconds": 300,
                                "max_segment_count": 2,
                                "max_input_seconds": 600,
                            },
                            {
                                "segment_seconds": "300",
                                "max_segment_count": 2,
                                "max_input_seconds": 600,
                                "unit_count": 2,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-artifacts-invalid-media-limits.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import engine
            import app.models  # noqa: F401
            from app.main import app

            Base.metadata.create_all(bind=engine)
            try:
                client = TestClient(app)
                create_response = client.post(
                    "/api/v1/artifacts",
                    json={
                        "project_id": "project-invalid-media-limits",
                        "artifact_type": "report",
                        "name": "markdown_manifest.json",
                        "file_path": str(manifest_path),
                        "version": "v1",
                    },
                )
                artifact_id = create_response.json()["id"]
                detail_response = client.get(f"/api/v1/artifacts/{artifact_id}")

                self.assertEqual(detail_response.status_code, 200)
                detail = detail_response.json()
                self.assertEqual(
                    detail["manifest"]["media_processing_limits"],
                    [{"segment_seconds": 300, "max_segment_count": 2, "max_input_seconds": 600, "unit_count": 2}],
                )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
