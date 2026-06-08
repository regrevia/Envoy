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


class PreprocessMediaFlowTests(unittest.TestCase):
    def test_preprocess_builds_semantic_units_for_pdf_and_image_without_media_commands(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-document-image-test.db'}"
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
            from app.services.crud_service import create_asset, create_project, create_task, get_task
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="multimodal project", description="", domain_type="knowledge", status="active"))
                pdf_storage = storage_client.upload_bytes("source.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")
                image_storage = storage_client.upload_bytes("diagram.png", b"\x89PNG\r\n\x1a\nfake image bytes", "image/png")
                pdf_asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="source.pdf",
                        file_path=pdf_storage["object_name"],
                        mime_type="application/pdf",
                        metadata={"storage": pdf_storage["storage"]},
                    ),
                )
                image_asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="image",
                        file_name="diagram.png",
                        file_path=image_storage["object_name"],
                        mime_type="image/png",
                        metadata={"storage": image_storage["storage"]},
                    ),
                )
                pdf_task = create_task(db, TaskCreate(project_id=project.id, asset_id=pdf_asset.id, task_type="preprocess", input_config={}))
                image_task = create_task(db, TaskCreate(project_id=project.id, asset_id=image_asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                pdf_asset_id = pdf_asset.id
                image_asset_id = image_asset.id
                pdf_task_id = pdf_task.id
                image_task_id = image_task.id
            finally:
                db.close()

            with patch("app.workers.jobs.split_video", side_effect=AssertionError("document and image preprocessing should not run video commands")):
                pdf_result = preprocess_job.run({"task_id": pdf_task_id, "project_id": project_id, "asset_id": pdf_asset_id})
                image_result = preprocess_job.run({"task_id": image_task_id, "project_id": project_id, "asset_id": image_asset_id})

            db = SessionLocal()
            try:
                pdf_task = get_task(db, pdf_task_id)
                image_task = get_task(db, image_task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).order_by(SemanticUnit.created_at.asc()).all()
                traces = db.query(TraceLink).filter(TraceLink.source_type == "asset", TraceLink.target_type == "semantic_unit").all()

                self.assertEqual(pdf_result["status"], "preprocessed")
                self.assertEqual(image_result["status"], "preprocessed")
                self.assertEqual(pdf_task.status, "success")
                self.assertEqual(image_task.status, "success")
                self.assertEqual(len(units), 2)
                self.assertEqual({unit.unit_type for unit in units}, {"document", "image"})
                self.assertEqual({unit.labels["source"] for unit in units}, {"document_asset", "image_asset"})
                self.assertTrue(any(unit.title == "source.pdf" and "application/pdf" in unit.content for unit in units))
                self.assertTrue(any(unit.title == "diagram.png" and "image/png" in unit.content for unit in units))
                self.assertEqual(len(traces), 2)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_resolves_media_asset_file_when_source_path_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-test.db'}"
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
                project = create_project(db, ProjectCreate(name="media project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("clip.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="clip.mp4",
                        file_path=storage_info["object_name"],
                        mime_type="video/mp4",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            calls = {}

            def fake_split(video_path, output_dir):
                calls["split_video"] = video_path
                return [str(Path(output_dir) / "segment_000001.mp4")]

            def fake_frames(video_path, output_dir):
                calls["extract_frames"] = video_path
                return [str(Path(output_dir) / "frame_000001.jpg")]

            def fake_transcribe(audio_path):
                calls["transcribe_audio"] = audio_path
                return {"text": "transcript", "segments": []}

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", side_effect=fake_frames), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[]):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                expected_path = str(Path(storage_info["file_path"]))

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(calls["split_video"], expected_path)
                self.assertEqual(calls["extract_frames"], expected_path)
                self.assertEqual(calls["transcribe_audio"], expected_path)
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].labels["source"], "media_asset")
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_fails_fast_when_media_source_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-missing-test.db'}"
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
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="missing media project", description="", domain_type="video", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="missing.mp4",
                        file_path="missing.mp4",
                        mime_type="video/mp4",
                        metadata={"storage": "local"},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            with patch("app.workers.jobs.split_video", side_effect=AssertionError("media command should not run")):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)

                self.assertEqual(result["status"], "failed")
                self.assertEqual(stored_task.status, "failed")
                self.assertIn("Source file not found", stored_task.error_message)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_records_media_processing_error_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-error-test.db'}"
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
            from app.schemas.asset import AssetCreate
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_asset, create_project, create_task, get_task
            from app.core.storage import storage_client
            from app.services.media_service import MediaProcessingError
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="bad media project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("bad.mp4", b"bad video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="bad.mp4",
                        file_path=storage_info["object_name"],
                        mime_type="video/mp4",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            with patch("app.workers.jobs.split_video", side_effect=MediaProcessingError("split_video failed: invalid media")):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)

                self.assertEqual(result["status"], "failed")
                self.assertEqual(stored_task.status, "failed")
                self.assertIn("invalid media", stored_task.error_message)
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
