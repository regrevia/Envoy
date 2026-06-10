import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class TaskDispatchFlowTests(unittest.TestCase):
    def test_task_status_update_refreshes_updated_at_for_long_task_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-updated-at-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, update_task_status

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="heartbeat project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                old_updated_at = datetime(2020, 1, 1, 0, 0, 0)
                task.updated_at = old_updated_at
                db.commit()

                updated = update_task_status(db, task.id, "running", 35, result_summary="media split done")

                self.assertIsNotNone(updated)
                self.assertEqual(updated.status, "running")
                self.assertEqual(updated.progress, 35)
                self.assertGreater(updated.updated_at, old_updated_at)
            finally:
                db.close()
                engine.dispose()

    def test_task_api_marks_running_task_stale_when_updated_at_exceeds_threshold(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-stale-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
            os.environ["TASK_STALE_AFTER_SECONDS"] = "60"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="stale task project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                task.status = "running"
                task.progress = 55
                task.updated_at = datetime.utcnow() - timedelta(seconds=120)
                db.commit()
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.get(f"/api/v1/tasks/{task_id}")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["is_stale"])
                self.assertGreaterEqual(body["stale_seconds"], 60)
                self.assertEqual(body["stale_after_seconds"], 60)
            finally:
                engine.dispose()

    def test_task_diagnostics_api_returns_backend_action_for_stale_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-diagnostics-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
            os.environ["TASK_STALE_AFTER_SECONDS"] = "60"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="diagnostics project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                task.status = "running"
                task.progress = 45
                task.result_summary = "audio transcription done: 1/12 segment(s)"
                task.updated_at = datetime.utcnow() - timedelta(seconds=180)
                db.commit()
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.get(f"/api/v1/tasks/{task_id}/diagnostics")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["task_id"], task_id)
                self.assertEqual(body["status"], "running")
                self.assertEqual(body["severity"], "stale")
                self.assertTrue(body["is_stale"])
                self.assertTrue(body["can_cancel"])
                self.assertFalse(body["can_retry"])
                self.assertEqual(body["recommended_action"], "cancel_then_retry")
                self.assertGreaterEqual(body["stale_seconds"], 60)
                self.assertEqual(body["stale_after_seconds"], 60)
                self.assertIn("任务长时间未更新", body["summary"])
                self.assertIn("取消任务", body["next_actions"])
                self.assertIn("重试任务", body["next_actions"])
            finally:
                engine.dispose()

    def test_task_cancel_marks_running_task_canceled_and_preserves_progress(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-cancel-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="cancel task project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                task.status = "running"
                task.progress = 45
                db.commit()
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(f"/api/v1/tasks/{task_id}/cancel")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["status"], "canceled")
                self.assertEqual(body["progress"], 45)
                self.assertEqual(body["result_summary"], "Task canceled by user")

                db = SessionLocal()
                try:
                    stored = get_task(db, task_id)
                    self.assertEqual(stored.status, "canceled")
                    self.assertEqual(stored.progress, 45)
                    self.assertEqual(stored.result_summary, "Task canceled by user")
                finally:
                    db.close()
            finally:
                engine.dispose()

    def test_canceled_task_cannot_be_overwritten_by_late_worker_update(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-cancel-protect-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, update_task_status

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="late update project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                canceled = update_task_status(db, task.id, "canceled", 35, result_summary="Task canceled by user")
                late = update_task_status(db, task.id, "success", 100, result_summary="preprocess done")

                self.assertEqual(canceled.status, "canceled")
                self.assertEqual(late.status, "canceled")
                self.assertEqual(late.progress, 35)
                self.assertEqual(late.result_summary, "Task canceled by user")
            finally:
                db.close()
                engine.dispose()

    def test_finished_task_cannot_be_overwritten_by_late_worker_update(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-finished-protect-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, update_task_status

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="finished update project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                finished = update_task_status(db, task.id, "success", 100, result_summary="preprocess done")
                late_running = update_task_status(db, task.id, "running", 55, result_summary="late heartbeat")
                late_failed = update_task_status(db, task.id, "failed", 100, error_message="late failure")

                self.assertEqual(finished.status, "success")
                self.assertEqual(late_running.status, "success")
                self.assertEqual(late_running.progress, 100)
                self.assertEqual(late_running.result_summary, "preprocess done")
                self.assertIsNone(late_running.error_message)
                self.assertEqual(late_failed.status, "success")
                self.assertEqual(late_failed.progress, 100)
                self.assertEqual(late_failed.result_summary, "preprocess done")
                self.assertIsNone(late_failed.error_message)
            finally:
                db.close()
                engine.dispose()

    def test_task_cancel_rejects_already_completed_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-cancel-complete-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="completed task project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                task.status = "success"
                task.progress = 100
                task.result_summary = "preprocess done"
                db.commit()
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(f"/api/v1/tasks/{task_id}/cancel")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], "Task is already finished")

                db = SessionLocal()
                try:
                    stored = get_task(db, task_id)
                    self.assertEqual(stored.status, "success")
                    self.assertEqual(stored.progress, 100)
                    self.assertEqual(stored.result_summary, "preprocess done")
                finally:
                    db.close()
            finally:
                engine.dispose()

    def test_task_retry_creates_new_task_from_failed_task_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-retry-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task, list_tasks

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="retry project", description="", domain_type="video", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="retry.mp4",
                        file_path="missing-before-retry.mp4",
                        mime_type="video/mp4",
                    ),
                )
                task = create_task(
                    db,
                    TaskCreate(
                        project_id=project.id,
                        asset_id=asset.id,
                        task_type="preprocess",
                        input_config={"source_path": "missing-before-retry.mp4"},
                    ),
                )
                task.status = "failed"
                task.progress = 100
                task.error_message = "Source file not found"
                db.commit()
                project_id = project.id
                asset_id = asset.id
                original_task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.api.v1.tasks.preprocess_job.delay") as delay:
                    response = client.post(f"/api/v1/tasks/{original_task_id}/retry")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertNotEqual(body["id"], original_task_id)
                self.assertEqual(body["project_id"], project_id)
                self.assertEqual(body["asset_id"], asset_id)
                self.assertEqual(body["task_type"], "preprocess")
                self.assertEqual(body["status"], "pending")
                self.assertEqual(body["progress"], 0)
                self.assertEqual(body["input_config"]["source_path"], "missing-before-retry.mp4")
                self.assertEqual(body["input_config"]["retry_of_task_id"], original_task_id)
                delay.assert_called_once()

                db = SessionLocal()
                try:
                    original = get_task(db, original_task_id)
                    tasks = list_tasks(db)
                    self.assertEqual(original.status, "failed")
                    self.assertEqual(original.error_message, "Source file not found")
                    self.assertEqual(len(tasks), 2)
                finally:
                    db.close()
            finally:
                engine.dispose()

    def test_task_retry_rejects_active_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-retry-active-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, list_tasks

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="active retry project", description="", domain_type="video", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={}))
                task.status = "running"
                task.progress = 40
                db.commit()
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(f"/api/v1/tasks/{task_id}/retry")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], "Task is still active")
                db = SessionLocal()
                try:
                    self.assertEqual(len(list_tasks(db)), 1)
                finally:
                    db.close()
            finally:
                engine.dispose()

    def test_task_retry_rejects_non_failed_finished_tasks_without_creating_new_tasks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-retry-finished-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, list_tasks

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="finished retry project", description="", domain_type="video", status="active"))
                success_task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={"source_path": "done.mp4"}))
                success_task.status = "success"
                success_task.progress = 100
                canceled_task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="preprocess", input_config={"source_path": "canceled.mp4"}))
                canceled_task.status = "canceled"
                canceled_task.progress = 50
                db.commit()
                task_ids = [success_task.id, canceled_task.id]
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.api.v1.tasks.preprocess_job.delay") as delay:
                    for task_id in task_ids:
                        with self.subTest(task_id=task_id):
                            response = client.post(f"/api/v1/tasks/{task_id}/retry")

                            self.assertEqual(response.status_code, 400)
                            self.assertEqual(response.json()["detail"], "Only failed tasks can be retried")

                delay.assert_not_called()
                db = SessionLocal()
                try:
                    self.assertEqual(len(list_tasks(db)), 2)
                finally:
                    db.close()
            finally:
                engine.dispose()

    def test_task_list_and_detail_include_traceable_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-context-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="task context project", description="", domain_type="knowledge", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(project_id=project.id, asset_type="text", file_name="source.txt", file_path="source.txt", mime_type="text/plain"),
                )
                task = create_task(
                    db,
                    TaskCreate(
                        project_id=project.id,
                        asset_id=asset.id,
                        task_type="preprocess",
                        input_config={"source_path": "source.txt", "mode": "text"},
                    ),
                )
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                list_response = client.get("/api/v1/tasks")
                detail_response = client.get(f"/api/v1/tasks/{task_id}")

                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(detail_response.status_code, 200)
                listed = list_response.json()[0]
                detail = detail_response.json()

                for payload in (listed, detail):
                    self.assertEqual(payload["id"], task_id)
                    self.assertEqual(payload["project_id"], project_id)
                    self.assertEqual(payload["asset_id"], asset_id)
                    self.assertEqual(payload["input_config"]["source_path"], "source.txt")
                    self.assertEqual(payload["input_config"]["mode"], "text")
                    self.assertIsNotNone(payload["created_at"])
                    self.assertIsNotNone(payload["updated_at"])
            finally:
                engine.dispose()

    def test_task_create_rejects_missing_project_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-missing-project-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

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
                    "/api/v1/tasks/preprocess",
                    json={"project_id": "missing-project", "asset_id": "asset-1", "task_type": "preprocess", "input_config": {}},
                )

                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"], "Project not found")
                self.assertEqual(client.get("/api/v1/tasks").json(), [])
            finally:
                engine.dispose()

    def test_task_create_rejects_asset_from_another_project_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-task-wrong-asset-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_asset, create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project_a = create_project(db, ProjectCreate(name="A", description="", domain_type="knowledge", status="active"))
                project_b = create_project(db, ProjectCreate(name="B", description="", domain_type="knowledge", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project_b.id,
                        asset_type="text",
                        file_name="source.txt",
                        file_path="source.txt",
                        mime_type="text/plain",
                    ),
                )
                project_a_id = project_a.id
                asset_id = asset.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/tasks/preprocess",
                    json={"project_id": project_a_id, "asset_id": asset_id, "task_type": "preprocess", "input_config": {}},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "Asset does not belong to project")
                self.assertEqual(client.get("/api/v1/tasks").json(), [])
            finally:
                engine.dispose()

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
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_asset, create_project, get_task

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="direct skill export", description="", domain_type="knowledge", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="direct-skill.txt",
                        file_path="direct-skill.txt",
                        mime_type="text/plain",
                    ),
                )
                project_id = project.id
                asset_id = asset.id
            finally:
                db.close()

            client = TestClient(app)
            response = client.post(
                "/api/v1/tasks/export-skill",
                json={
                    "task_id": "stale-client-task-id",
                    "project_id": project_id,
                    "asset_id": asset_id,
                    "source_asset_id": asset_id,
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
                traces = db.query(TraceLink).filter(TraceLink.source_id == asset_id, TraceLink.target_type == "artifact").all()
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
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_asset, create_project, get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="dispatch failure", description="", domain_type="knowledge", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(project_id=project.id, asset_type="video", file_name="sample.mp4", file_path="sample.mp4", mime_type="video/mp4"),
                )
                project_id = project.id
                asset_id = asset.id
            finally:
                db.close()

            with patch("app.api.v1.tasks.preprocess_job.delay", side_effect=RuntimeError("redis offline")):
                response = client.post(
                    "/api/v1/tasks/preprocess",
                    json={"project_id": project_id, "asset_id": asset_id, "task_type": "preprocess", "input_config": {"source_path": "sample.mp4"}},
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
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project, get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="local dispatch", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

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
                    json={"project_id": project_id, "task_type": "analyze", "input_config": {"text": "source"}},
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
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project, get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="local analysis", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            with patch("app.api.v1.tasks.analyze_job.delay", side_effect=AssertionError("local mode should not call celery delay")):
                response = client.post(
                    "/api/v1/tasks/analyze",
                    json={
                        "project_id": project_id,
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
                units = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).all()
                self.assertEqual(len(units), 3)
                self.assertEqual({unit.unit_type for unit in units}, {"scene", "rule", "concept"})
            finally:
                db.close()
                engine.dispose()

    def test_generate_samples_task_accepts_legacy_unit_asset_id_when_source_unit_belongs_to_project(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-source-unit-api-test.db'}"
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
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project, get_task

            Base.metadata.create_all(bind=engine)
            client = TestClient(app)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="legacy unit sample", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="legacy-unit-001",
                    project_id=project.id,
                    asset_id="legacy-asset-not-in-assets-table",
                    segment_id=None,
                    unit_type="text",
                    title="Legacy imported unit",
                    content="Imported unit content should still generate reviewable samples.",
                    labels={"source": "legacy_import"},
                    confidence=0.9,
                )
                db.add(unit)
                db.commit()
                project_id = project.id
                legacy_asset_id = unit.asset_id
            finally:
                db.close()

            try:
                response = client.post(
                    "/api/v1/tasks/generate-samples",
                    json={
                        "project_id": project_id,
                        "asset_id": legacy_asset_id,
                        "task_type": "generate_samples",
                        "input_config": {"source_unit_id": "legacy-unit-001"},
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

                    samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()
                    self.assertIsNotNone(stored)
                    self.assertEqual(stored.status, "success")
                    self.assertEqual(stored.asset_id, legacy_asset_id)
                    self.assertEqual(len(samples), 1)
                    self.assertEqual(samples[0].source_unit_id, "legacy-unit-001")
                finally:
                    db.close()
            finally:
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
