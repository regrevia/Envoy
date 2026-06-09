import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class TaskDispatchFlowTests(unittest.TestCase):
    def test_direct_export_skill_uses_created_task_id_when_payload_contains_stale_task_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-direct-export-skill-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.artifact import Artifact
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project, get_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="direct skill export", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            client = TestClient(app)
            response = client.post(
                "/api/v1/tasks/export-skill",
                json={
                    "task_id": "stale-client-task-id",
                    "project_id": project_id,
                    "asset_id": "asset-direct-skill-001",
                    "source_asset_id": "asset-direct-skill-001",
                    "title": "Direct API Skill",
                    "payload": [{"topic": "handoff", "content": "Keep task context owned by the server."}],
                },
            )

            self.assertEqual(response.status_code, 200)
            task_id = response.json()["id"]

            db = SessionLocal()
            try:
                stored = None
                for _ in range(50):
                    db.expire_all()
                    stored = get_task(db, task_id)
                    if stored is not None and stored.status in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "success")
                self.assertEqual(stored.progress, 100)
                self.assertEqual(stored.result_summary, "skill exported")
                artifacts = db.query(Artifact).filter(Artifact.project_id == project_id, Artifact.artifact_type == "skill").all()
                traces = db.query(TraceLink).filter(TraceLink.source_id == "asset-direct-skill-001", TraceLink.target_type == "artifact").all()
                self.assertEqual({Path(item.file_path).name for item in artifacts}, {"skill.md", "skill.json"})
                self.assertTrue(all(Path(item.file_path).exists() for item in artifacts))
                self.assertTrue(all(task_id in item.file_path for item in artifacts))
                self.assertFalse(any("stale-client-task-id" in item.file_path for item in artifacts))
                self.assertEqual(len(traces), 2)
            finally:
                db.close()
                engine.dispose()

    def test_task_create_records_dispatch_failure_when_celery_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

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
            self.assertIn("Task dispatch failed", body["error_message"])
            self.assertIn("redis offline", body["error_message"])

            db = SessionLocal()
            try:
                stored = get_task(db, body["id"])
                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "dispatch_failed")
                self.assertIn("Task dispatch failed", stored.error_message)
                self.assertIn("redis offline", stored.error_message)
            finally:
                db.close()
                engine.dispose()

    def test_local_task_execution_mode_runs_without_celery_worker(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-local-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

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

            started = []

            class FakeThread:
                def __init__(self, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon

                def start(self):
                    started.append({"target": self.target, "args": self.args, "daemon": self.daemon})

            with patch("app.services.task_dispatcher.Thread", FakeThread), \
                patch("app.api.v1.tasks.analyze_job.delay", side_effect=AssertionError("local mode should not call celery delay")):
                response = client.post(
                    "/api/v1/tasks/analyze",
                    json={"project_id": "project-1", "asset_id": "asset-1", "task_type": "analyze", "input_config": {"text": "source"}},
                )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "running")
            self.assertEqual(body["progress"], 1)
            self.assertIn("Local task started", body["result_summary"])
            self.assertEqual(len(started), 1)
            self.assertTrue(started[0]["daemon"])
            self.assertEqual(started[0]["args"][1]["task_id"], body["id"])

            db = SessionLocal()
            try:
                stored = get_task(db, body["id"])
                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "running")
                self.assertEqual(stored.progress, 1)
                self.assertIn("Local task started", stored.result_summary)
            finally:
                db.close()
                engine.dispose()

    def test_local_task_execution_mode_completes_analyze_job_without_celery_worker(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-local-run-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import engine, SessionLocal
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.services.crud_service import get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)

            with patch("app.api.v1.tasks.analyze_job.delay", side_effect=AssertionError("local mode should not call celery delay")):
                response = client.post(
                    "/api/v1/tasks/analyze",
                    json={
                        "project_id": "project-1",
                        "asset_id": "asset-1",
                        "task_type": "analyze",
                        "input_config": {"text": "source material for local analysis"},
                    },
                )

            self.assertEqual(response.status_code, 200)
            task_id = response.json()["id"]

            db = SessionLocal()
            try:
                stored = None
                for _ in range(50):
                    db.expire_all()
                    stored = get_task(db, task_id)
                    if stored is not None and stored.status in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "success")
                self.assertEqual(stored.progress, 100)
                self.assertEqual(stored.result_summary, "analysis done")
                units = db.query(SemanticUnit).filter(SemanticUnit.project_id == "project-1").all()
                self.assertEqual(len(units), 3)
                self.assertEqual({unit.unit_type for unit in units}, {"scene", "rule", "concept"})
            finally:
                db.close()
                engine.dispose()

    def test_task_detail_and_status_update_return_404_when_task_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-404-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

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

                detail_response = client.get("/api/v1/tasks/missing-task")
                status_response = client.post("/api/v1/tasks/missing-task/status", json={"status": "success"})

                self.assertEqual(detail_response.status_code, 404)
                self.assertEqual(status_response.status_code, 404)
                self.assertEqual(detail_response.json()["detail"], "Task not found")
                self.assertEqual(status_response.json()["detail"], "Task not found")
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
