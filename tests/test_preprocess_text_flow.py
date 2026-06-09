import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class PreprocessTextFlowTests(unittest.TestCase):
    def test_preprocess_reads_text_asset_and_creates_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="长文本项目", description="", domain_type="knowledge", status="active"))
                text = "第一段核心知识。\n第二段可训练内容。"
                storage_info = storage_client.upload_bytes("long-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="long-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "text")
                self.assertIn("第一段核心知识", units[0].content)
                self.assertEqual(units[0].labels["source"], "text_asset")
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_rerun_replaces_existing_semantic_units_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-idempotent-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.models.trace_link import TraceLink
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="idempotent preprocess", description="", domain_type="knowledge", status="active"))
                text = "first block\nsecond block"
                storage_info = storage_client.upload_bytes("repeat-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="repeat-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                first_task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                second_task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                first_task_id = first_task.id
                second_task_id = second_task.id
            finally:
                db.close()

            first_result = preprocess_job.run({"task_id": first_task_id, "project_id": project_id, "asset_id": asset_id})
            db = SessionLocal()
            try:
                first_unit_count = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).count()
                first_trace_count = db.query(TraceLink).filter(TraceLink.source_type == "asset", TraceLink.source_id == asset_id, TraceLink.target_type == "semantic_unit").count()
            finally:
                db.close()

            second_result = preprocess_job.run({"task_id": second_task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                traces = db.query(TraceLink).filter(TraceLink.source_type == "asset", TraceLink.source_id == asset_id, TraceLink.target_type == "semantic_unit").all()

                self.assertEqual(first_result["status"], "preprocessed")
                self.assertEqual(second_result["status"], "preprocessed")
                self.assertEqual(first_unit_count, 1)
                self.assertEqual(first_trace_count, 1)
                self.assertEqual(len(units), first_unit_count)
                self.assertEqual(len(traces), first_trace_count)
                self.assertEqual(units[0].labels["source"], "text_asset")
                self.assertIn("first block", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_canceled_preprocess_rerun_preserves_previous_successful_units(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-canceled-rerun-preserve-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["TEXT_PREPROCESS_CHUNK_CHARS"] = "80"
            os.environ["TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS"] = "1"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task, update_task_status as real_update_task_status
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="canceled rerun preserve", description="", domain_type="knowledge", status="active"))
                text = "\n".join([f"paragraph-{index} " + ("content " * 8) for index in range(6)])
                storage_info = storage_client.upload_bytes("stable-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="stable-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                first_task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                second_task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                first_task_id = first_task.id
                second_task_id = second_task.id
            finally:
                db.close()

            first_result = preprocess_job.run({"task_id": first_task_id, "project_id": project_id, "asset_id": asset_id})
            db = SessionLocal()
            try:
                previous_units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()
                previous_count = len(previous_units)
                previous_contents = [unit.content for unit in previous_units]
            finally:
                db.close()

            progress_updates = []

            def cancel_rerun_after_first_chunk(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary})
                updated = real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)
                if task_id_arg == second_task_id and result_summary and "text chunks persisted" in result_summary:
                    real_update_task_status(db, task_id_arg, "canceled", progress, result_summary="Task canceled by user")
                return updated

            from unittest.mock import patch
            with patch("app.workers.jobs.update_task_status", side_effect=cancel_rerun_after_first_chunk):
                second_result = preprocess_job.run({"task_id": second_task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, second_task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()

                self.assertEqual(first_result["status"], "preprocessed")
                self.assertEqual(second_result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(stored_task.result_summary, "Task canceled by user")
                self.assertEqual(len(units), previous_count)
                self.assertEqual([unit.content for unit in units], previous_contents)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_splits_long_text_asset_into_ordered_chunks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-long-text-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["TEXT_PREPROCESS_CHUNK_CHARS"] = "120"
            os.environ["TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS"] = "20"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="long text chunks", description="", domain_type="knowledge", status="active"))
                text = "\n\n".join([f"section-{idx} " + ("content " * 35) for idx in range(5)])
                storage_info = storage_client.upload_bytes("long-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="long-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertGreater(len(units), 1)
                self.assertIn("chunks", stored_task.result_summary)
                self.assertEqual(result["semantic"]["chunk_count"], len(units))

                chunk_count = units[0].labels["chunk_count"]
                self.assertEqual(chunk_count, len(units))
                for index, unit in enumerate(units):
                    self.assertEqual(unit.unit_type, "text")
                    self.assertLessEqual(len(unit.content), 120)
                    self.assertEqual(unit.labels["source"], "text_asset")
                    self.assertEqual(unit.labels["chunk_index"], index)
                    self.assertEqual(unit.labels["chunk_count"], chunk_count)
                    self.assertLess(unit.labels["char_start"], unit.labels["char_end"])
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_reports_progress_while_splitting_long_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-long-text-progress-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["TEXT_PREPROCESS_CHUNK_CHARS"] = "80"
            os.environ["TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS"] = "0"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, update_task_status as real_update_task_status
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="long text progress", description="", domain_type="knowledge", status="active"))
                text = "\n".join([f"paragraph-{index} " + ("content " * 20) for index in range(10)])
                storage_info = storage_client.upload_bytes("progress-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="progress-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            progress_updates = []

            def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary})
                return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

            from unittest.mock import patch
            with patch("app.workers.jobs.update_task_status", side_effect=record_update):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            self.assertEqual(result["status"], "preprocessed")
            summaries = [item["summary"] for item in progress_updates if item["summary"]]
            self.assertTrue(any("text chunks persisted" in summary for summary in summaries))
            self.assertTrue(any(item["status"] == "running" and item["progress"] and 10 < item["progress"] < 100 for item in progress_updates))

            engine.dispose()

    def test_preprocess_stops_long_text_chunking_when_task_is_canceled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-long-text-cancel-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["TEXT_PREPROCESS_CHUNK_CHARS"] = "80"
            os.environ["TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS"] = "0"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task, update_task_status as real_update_task_status
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="long text cancel", description="", domain_type="knowledge", status="active"))
                text = "\n".join([f"paragraph-{index} " + ("content " * 20) for index in range(10)])
                storage_info = storage_client.upload_bytes("cancel-note.txt", text.encode("utf-8"), "text/plain")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="text",
                        file_name="cancel-note.txt",
                        file_path=storage_info["object_name"],
                        mime_type="text/plain",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            progress_updates = []

            def cancel_during_chunking(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary})
                updated = real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)
                if result_summary and "text chunks persisted" in result_summary:
                    real_update_task_status(db, task_id_arg, "canceled", progress, result_summary="Task canceled by user")
                return updated

            from unittest.mock import patch
            with patch("app.workers.jobs.update_task_status", side_effect=cancel_during_chunking):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(stored_task.result_summary, "Task canceled by user")
                self.assertGreater(len(units), 0)
                self.assertLess(len(units), result["semantic"]["chunk_count"])
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
