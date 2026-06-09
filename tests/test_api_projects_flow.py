import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiProjectsFlowTests(unittest.TestCase):
    def test_project_create_list_and_detail_return_traceable_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-projects-api-test.db'}"
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
                    "/api/v1/projects",
                    json={"name": "项目元数据", "description": "检查字段", "domain_type": "knowledge", "status": "active"},
                )
                self.assertEqual(create_response.status_code, 200)
                created = create_response.json()
                project_id = created["id"]

                list_response = client.get("/api/v1/projects")
                detail_response = client.get(f"/api/v1/projects/{project_id}")

                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(detail_response.status_code, 200)
                listed = list_response.json()[0]
                detail = detail_response.json()

                for payload in (created, listed, detail):
                    self.assertEqual(payload["id"], project_id)
                    self.assertEqual(payload["name"], "项目元数据")
                    self.assertEqual(payload["description"], "检查字段")
                    self.assertEqual(payload["domain_type"], "knowledge")
                    self.assertEqual(payload["status"], "active")
                    self.assertIn("user_id", payload)
                    self.assertIsNotNone(payload["created_at"])
                    self.assertIsNotNone(payload["updated_at"])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
