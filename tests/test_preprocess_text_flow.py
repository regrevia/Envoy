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


if __name__ == "__main__":
    unittest.main()
