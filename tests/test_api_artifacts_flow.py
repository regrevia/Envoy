import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
