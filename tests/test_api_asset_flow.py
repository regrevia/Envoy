import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiAssetFlowTests(unittest.TestCase):
    def test_asset_upload_rejects_missing_project_without_creating_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-missing-project-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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

                response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": "missing-project", "asset_type": "text", "language": "zh"},
                    files={"file": ("source.txt", b"orphan content", "text/plain")},
                )

                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"], "Project not found")

                assets_response = client.get("/api/v1/assets")
                self.assertEqual(assets_response.status_code, 200)
                self.assertEqual(assets_response.json(), [])
                storage_dir = Path(tmp_dir) / "storage"
                stored_files = [path for path in storage_dir.rglob("*") if path.is_file()] if storage_dir.exists() else []
                self.assertEqual(stored_files, [])
            finally:
                engine.dispose()

    def test_project_and_text_asset_upload_flow(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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

                project_response = client.post(
                    "/api/v1/projects",
                    json={"name": "真实链路项目", "description": "集成测试", "domain_type": "knowledge", "status": "active"},
                )
                self.assertEqual(project_response.status_code, 200)
                project_id = project_response.json()["id"]

                upload_response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": project_id, "asset_type": "text", "language": "zh"},
                    files={"file": ("source.txt", b"long text content", "text/plain")},
                )
                self.assertEqual(upload_response.status_code, 200)
                uploaded = upload_response.json()
                self.assertEqual(uploaded["asset"]["project_id"], project_id)
                self.assertEqual(uploaded["asset"]["asset_type"], "text")
                self.assertEqual(uploaded["asset"]["mime_type"], "text/plain")
                self.assertEqual(uploaded["asset"]["language"], "zh")
                self.assertEqual(uploaded["asset"]["metadata"]["original_name"], "source.txt")
                self.assertEqual(uploaded["asset"]["metadata"]["storage"], "local")
                self.assertIsNotNone(uploaded["asset"]["created_at"])
                self.assertEqual(uploaded["storage"]["storage"], "local")
                self.assertTrue(Path(uploaded["storage"]["file_path"]).exists())

                assets_response = client.get("/api/v1/assets")
                self.assertEqual(assets_response.status_code, 200)
                assets = assets_response.json()
                self.assertEqual(len(assets), 1)
                self.assertEqual(assets[0]["file_name"], "source.txt")
                self.assertEqual(assets[0]["mime_type"], "text/plain")
                self.assertEqual(assets[0]["language"], "zh")
                self.assertEqual(assets[0]["metadata"]["original_name"], "source.txt")
                self.assertEqual(assets[0]["metadata"]["storage"], "local")
                self.assertIsNotNone(assets[0]["created_at"])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
