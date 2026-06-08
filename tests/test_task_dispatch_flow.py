import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class TaskDispatchFlowTests(unittest.TestCase):
    def test_task_create_records_dispatch_failure_when_celery_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import engine, SessionLocal
            import app.models  # noqa: F401
            from app.main import app
            from app.services.crud_service import get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)

            with patch("app.api.v1.tasks.preprocess_job.delay", side_effect=RuntimeError("redis offline")):
                response = client.post(
                    "/api/v1/tasks/preprocess",
                    json={"project_id": "project-1", "asset_id": "asset-1", "task_type": "preprocess", "input_config": {"source_path": "sample.mp4"}},
                )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "dispatch_failed")
            self.assertEqual(body["progress"], 0)
            self.assertIn("redis offline", body["error_message"])

            db = SessionLocal()
            try:
                stored = get_task(db, body["id"])
                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "dispatch_failed")
                self.assertIn("redis offline", stored.error_message)
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
