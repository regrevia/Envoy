import os
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def build_pdf_bytes(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buffer.getvalue()


def build_docx_bytes(text: str) -> bytes:
    buffer = BytesIO()
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def build_pptx_bytes(text: str) -> bytes:
    buffer = BytesIO()
    slide_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>"""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr("ppt/slides/slide1.xml", slide_xml)
    return buffer.getvalue()


def build_xlsx_bytes(text: str) -> bytes:
    buffer = BytesIO()
    shared_strings_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>{text}</t></si>
</sst>"""
    worksheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>
</worksheet>"""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr("xl/sharedStrings.xml", shared_strings_xml)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buffer.getvalue()


class PreprocessMediaFlowTests(unittest.TestCase):
    def test_preprocess_extracts_pdf_text_into_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-pdf-text-test.db'}"
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
                project = create_project(db, ProjectCreate(name="pdf text project", description="", domain_type="knowledge", status="active"))
                pdf_storage = storage_client.upload_bytes(
                    "playbook.pdf",
                    build_pdf_bytes("Refund playbook requires manager approval before escalation."),
                    "application/pdf",
                )
                pdf_asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="playbook.pdf",
                        file_path=pdf_storage["object_name"],
                        mime_type="application/pdf",
                        metadata={"storage": pdf_storage["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=pdf_asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = pdf_asset.id
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
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "document")
                self.assertEqual(units[0].labels["source"], "document_text")
                self.assertEqual(units[0].labels["extraction_status"], "text_extracted")
                self.assertIn("Refund playbook requires manager approval", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_extracts_docx_text_into_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-docx-text-test.db'}"
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
                project = create_project(db, ProjectCreate(name="docx text project", description="", domain_type="knowledge", status="active"))
                docx_storage = storage_client.upload_bytes(
                    "refund-playbook.docx",
                    build_docx_bytes("Refund approval requires invoice evidence and manager confirmation."),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                docx_asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="refund-playbook.docx",
                        file_path=docx_storage["object_name"],
                        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        metadata={"storage": docx_storage["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=docx_asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = docx_asset.id
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
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "document")
                self.assertEqual(units[0].labels["source"], "document_text")
                self.assertIn("Refund approval requires invoice evidence", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_extracts_pptx_text_into_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-pptx-text-test.db'}"
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
                project = create_project(db, ProjectCreate(name="pptx text project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes(
                    "training-deck.pptx",
                    build_pptx_bytes("Escalation slides require timeline evidence."),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="training-deck.pptx",
                        file_path=storage_info["object_name"],
                        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
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
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "document")
                self.assertEqual(units[0].labels["source"], "document_text")
                self.assertIn("Escalation slides require timeline evidence", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_extracts_xlsx_text_into_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-xlsx-text-test.db'}"
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
                project = create_project(db, ProjectCreate(name="xlsx text project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes(
                    "training-table.xlsx",
                    build_xlsx_bytes("Refund matrix requires regional owner review."),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="training-table.xlsx",
                        file_path=storage_info["object_name"],
                        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "document")
                self.assertEqual(units[0].labels["source"], "document_text")
                self.assertIn("Refund matrix requires regional owner review", units[0].content)
            finally:
                db.close()
                engine.dispose()

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
            from app.services.crud_service import create_asset, create_project, create_task, get_task, update_task_status as real_update_task_status
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

            progress_updates = []

            def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary, "error": error_message})
                return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", side_effect=fake_frames), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[]), \
                patch("app.workers.jobs.update_task_status", side_effect=record_update):
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
                summaries = [item["summary"] for item in progress_updates if item["summary"]]
                self.assertIn("media split done: 1 segment(s)", summaries)
                self.assertIn("frame extraction done: 1 frame pattern(s)", summaries)
                self.assertIn("audio transcription done", summaries)
                self.assertIn("ocr done: 0 frame result(s)", summaries)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_stops_media_pipeline_when_task_is_canceled_after_split(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-cancel-test.db'}"
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
            from app.services.crud_service import create_asset, create_project, create_task, get_task, update_task_status
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="cancel media project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("cancel-me.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="cancel-me.mp4",
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

            def cancel_after_split(video_path, output_dir):
                db = SessionLocal()
                try:
                    update_task_status(db, task_id, "canceled", 35, result_summary="Task canceled by user")
                finally:
                    db.close()
                return [str(Path(output_dir) / "segment_000001.mp4")]

            with patch("app.workers.jobs.split_video", side_effect=cancel_after_split), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("canceled media task should not extract frames")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=AssertionError("canceled media task should not transcribe audio")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("canceled media task should not run OCR")):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(stored_task.progress, 35)
                self.assertEqual(stored_task.result_summary, "Task canceled by user")
                self.assertEqual(units, [])
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
