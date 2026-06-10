import json
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

    def test_preprocess_reports_progress_while_splitting_long_document(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-long-document-progress-test.db'}"
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
                project = create_project(db, ProjectCreate(name="long document progress", description="", domain_type="knowledge", status="active"))
                text = "\n".join([f"section-{index} " + ("document content " * 12) for index in range(10)])
                storage_info = storage_client.upload_bytes(
                    "long-guide.docx",
                    build_docx_bytes(text),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="long-guide.docx",
                        file_path=storage_info["object_name"],
                        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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

            with patch("app.workers.jobs.update_task_status", side_effect=record_update):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            self.assertEqual(result["status"], "preprocessed")
            summaries = [item["summary"] for item in progress_updates if item["summary"]]
            self.assertTrue(any("document chunks persisted" in summary for summary in summaries))
            self.assertTrue(any(item["status"] == "running" and item["progress"] and 10 < item["progress"] < 100 for item in progress_updates))

            engine.dispose()

    def test_preprocess_stops_long_document_chunking_when_task_is_canceled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-long-document-cancel-test.db'}"
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
                project = create_project(db, ProjectCreate(name="long document cancel", description="", domain_type="knowledge", status="active"))
                text = "\n".join([f"section-{index} " + ("document content " * 12) for index in range(10)])
                storage_info = storage_client.upload_bytes(
                    "cancel-guide.docx",
                    build_docx_bytes(text),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="document",
                        file_name="cancel-guide.docx",
                        file_path=storage_info["object_name"],
                        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
                if result_summary and "document chunks persisted" in result_summary:
                    real_update_task_status(db, task_id_arg, "canceled", progress, result_summary="Task canceled by user")
                return updated

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

    def test_preprocess_extracts_image_ocr_text_into_semantic_unit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-image-ocr-test.db'}"
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
                project = create_project(db, ProjectCreate(name="image ocr project", description="", domain_type="knowledge", status="active"))
                image_storage = storage_client.upload_bytes("receipt.png", b"\x89PNG\r\n\x1a\nfake image bytes", "image/png")
                image_asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="image",
                        file_name="receipt.png",
                        file_path=image_storage["object_name"],
                        mime_type="image/png",
                        metadata={"storage": image_storage["storage"]},
                    ),
                )
                image_task = create_task(db, TaskCreate(project_id=project.id, asset_id=image_asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                image_asset_id = image_asset.id
                image_task_id = image_task.id
                expected_path = str(storage_client._local_path(image_storage["object_name"]))
            finally:
                db.close()

            ocr_payload = [
                {
                    "frame": expected_path,
                    "text": "Invoice approval requires manager review.",
                    "confidence": 0.86,
                }
            ]
            with patch("app.workers.jobs.split_video", side_effect=AssertionError("image preprocessing should not run video commands")), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=ocr_payload) as ocr_mock:
                result = preprocess_job.run({"task_id": image_task_id, "project_id": project_id, "asset_id": image_asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, image_task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == image_asset_id).all()

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(result["semantic"]["source"], "image_text")
                self.assertEqual(result["semantic"]["ocr_result_count"], 1)
                ocr_mock.assert_called_once_with([expected_path])
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "image")
                self.assertEqual(units[0].labels["source"], "image_text")
                self.assertEqual(units[0].labels["extraction_status"], "text_extracted")
                self.assertIn("Invoice approval requires manager review.", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_resolves_media_asset_file_when_source_path_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["MEDIA_SEGMENT_SECONDS"] = "300"
            os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "2"
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
                return [str(Path(output_dir) / "segment_000001.mp4"), str(Path(output_dir) / "segment_000002.mp4")]

            def fake_frames(video_path, output_dir):
                calls["extract_frames"] = video_path
                return [str(Path(output_dir) / "frame_000001.jpg")]

            def fake_transcribe(audio_path):
                calls.setdefault("transcribe_audio", []).append(audio_path)
                segment_name = Path(audio_path).stem
                return {
                    "text": f"{segment_name} explains refund approval before escalation.",
                    "segments": [{"start": 0.0, "end": 6.2, "text": f"{segment_name} explains refund approval before escalation."}],
                }

            progress_updates = []

            def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary, "error": error_message})
                return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", side_effect=fake_frames), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[{"frame": "frame_000001.jpg", "text": "Slide: approval checklist", "confidence": 0.81}]), \
                patch("app.workers.jobs.update_task_status", side_effect=record_update):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                expected_path = str(Path(storage_info["file_path"]))

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(stored_task.result_summary, "media preprocess done: 2 segment(s), 1 frame(s), max 600 sec")
                self.assertEqual(calls["split_video"], expected_path)
                self.assertEqual(calls["extract_frames"], expected_path)
                self.assertEqual(calls["transcribe_audio"], result["semantic"]["segments"])
                self.assertEqual(len(calls["transcribe_audio"]), 2)
                self.assertEqual(result["semantic"]["segment_seconds"], 300)
                self.assertEqual(result["semantic"]["max_segment_count"], 2)
                self.assertEqual(result["semantic"]["max_input_seconds"], 600)
                sources = {unit.labels["source"] for unit in units}
                self.assertIn("media_asset", sources)
                self.assertIn("media_transcript", sources)
                self.assertIn("media_ocr", sources)
                media_summary = next(unit for unit in units if unit.labels["source"] == "media_asset")
                media_summary_payload = json.loads(media_summary.content)
                self.assertEqual(media_summary_payload["stage"], "preprocess")
                self.assertEqual(media_summary_payload["max_input_seconds"], 600)
                self.assertTrue(any(unit.unit_type == "transcript" and "segment_000001" in unit.content and "segment_000002" in unit.content for unit in units))
                self.assertTrue(any(unit.unit_type == "ocr" and "approval checklist" in unit.content for unit in units))
                summaries = [item["summary"] for item in progress_updates if item["summary"]]
                self.assertIn("media split running", summaries)
                self.assertIn("media split done: 2 segment(s)", summaries)
                self.assertIn("frame extraction running", summaries)
                self.assertIn("frame extraction done: 1 frame pattern(s)", summaries)
                self.assertIn("audio transcription running: 1/2 segment(s)", summaries)
                self.assertIn("audio transcription running: 2/2 segment(s)", summaries)
                self.assertIn("audio transcription done: 2/2 segment(s)", summaries)
                self.assertIn("ocr running: 1 frame(s)", summaries)
                self.assertIn("ocr done: 1 frame result(s)", summaries)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_video_truncates_overlong_segment_input_and_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-video-truncation-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["MEDIA_SEGMENT_SECONDS"] = "300"
            os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "2"
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
                project = create_project(db, ProjectCreate(name="video truncation project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("long-video.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="long-video.mp4",
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

            calls = {"split_video": None, "extract_frames": None, "transcribe_audio": []}
            progress_updates = []

            def fake_split(video_path, output_dir):
                calls["split_video"] = video_path
                return [
                    str(Path(output_dir) / "segment_000001.mp4"),
                    str(Path(output_dir) / "segment_000002.mp4"),
                    str(Path(output_dir) / "segment_000003.mp4"),
                    str(Path(output_dir) / "segment_000004.mp4"),
                    str(Path(output_dir) / "segment_000005.mp4"),
                ]

            def fake_frames(video_path, output_dir):
                calls["extract_frames"] = video_path
                return [str(Path(output_dir) / "frame_000001.jpg")]

            def fake_transcribe(audio_path):
                calls["transcribe_audio"].append(audio_path)
                segment_name = Path(audio_path).stem
                return {
                    "status": "success",
                    "text": f"{segment_name} media transcript",
                    "segments": [{"start": 0.0, "end": 5.0, "text": segment_name}],
                }

            def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary, "error": error_message})
                return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", side_effect=fake_frames), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[{"frame": "frame_000001.jpg", "text": "video frame text", "confidence": 0.8}]), \
                patch("app.workers.jobs.update_task_status", side_effect=record_update):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                summaries = [item["summary"] for item in progress_updates if item["summary"]]

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(len(calls["transcribe_audio"]), 2)
                self.assertEqual(result["semantic"]["segment_count"], 2)
                self.assertEqual(result["semantic"]["segment_source_count"], 5)
                self.assertTrue(result["semantic"]["segment_truncated"])
                self.assertEqual(calls["transcribe_audio"], result["semantic"]["segments"])
                self.assertIn("audio transcription running: 1/2 segment(s)", summaries)
                self.assertIn("audio transcription running: 2/2 segment(s)", summaries)
                self.assertIn("segment input truncated to 2/5 segment(s)", stored_task.result_summary)
                self.assertTrue(any(unit.labels.get("source") == "media_transcript" for unit in units))
                self.assertTrue(any(unit.labels.get("source") == "media_ocr" for unit in units))
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_video_can_skip_ocr_when_disabled(self):
        previous_media_enable_ocr = os.environ.get("MEDIA_ENABLE_OCR")
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-video-no-ocr-test.db'}"
                os.environ["ENVOY_STORAGE_MODE"] = "local"
                os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
                os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
                os.environ["MEDIA_ENABLE_OCR"] = "false"
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
                    project = create_project(db, ProjectCreate(name="video no ocr project", description="", domain_type="video", status="active"))
                    storage_info = storage_client.upload_bytes("lecture.mp4", b"fake video bytes", "video/mp4")
                    asset = create_asset(
                        db,
                        AssetCreate(
                            project_id=project.id,
                            asset_type="video",
                            file_name="lecture.mp4",
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

                progress_updates = []

                def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                    progress_updates.append({"status": status, "progress": progress, "summary": result_summary, "error": error_message})
                    return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

                segment_path = str(Path(tmp_dir) / "segment_000001.mp4")
                frame_path = str(Path(tmp_dir) / "frame_000001.jpg")
                with patch("app.workers.jobs.split_video", return_value=[segment_path]), \
                    patch("app.workers.jobs.extract_frames", return_value=[frame_path]), \
                    patch("app.workers.jobs.transcribe_audio", return_value={"text": "lecture transcript for stable export", "segments": []}), \
                    patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("OCR should be skipped when MEDIA_ENABLE_OCR=false")), \
                    patch("app.workers.jobs.update_task_status", side_effect=record_update):
                    result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

                db = SessionLocal()
                try:
                    stored_task = get_task(db, task_id)
                    units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                    self.assertEqual(result["status"], "preprocessed")
                    self.assertEqual(stored_task.status, "success")
                    self.assertTrue(result["semantic"]["ocr_skipped"])
                    self.assertEqual(result["semantic"]["ocr"], [])
                    self.assertTrue(any(unit.labels.get("source") == "media_transcript" for unit in units))
                    self.assertFalse(any(unit.labels.get("source") == "media_ocr" for unit in units))
                    summaries = [item["summary"] for item in progress_updates if item["summary"]]
                    self.assertIn("ocr skipped: disabled", summaries)
                finally:
                    db.close()
                    engine.dispose()
        finally:
            if previous_media_enable_ocr is None:
                os.environ.pop("MEDIA_ENABLE_OCR", None)
            else:
                os.environ["MEDIA_ENABLE_OCR"] = previous_media_enable_ocr

    def test_preprocess_video_can_skip_frame_extraction_when_disabled(self):
        previous_env = {
            "MEDIA_ENABLE_FRAME_EXTRACTION": os.environ.get("MEDIA_ENABLE_FRAME_EXTRACTION"),
            "MEDIA_SEGMENT_SECONDS": os.environ.get("MEDIA_SEGMENT_SECONDS"),
            "MEDIA_MAX_SEGMENT_COUNT": os.environ.get("MEDIA_MAX_SEGMENT_COUNT"),
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-video-no-frames-test.db'}"
                os.environ["ENVOY_STORAGE_MODE"] = "local"
                os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
                os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
                os.environ["MEDIA_ENABLE_FRAME_EXTRACTION"] = "false"
                os.environ["MEDIA_SEGMENT_SECONDS"] = "600"
                os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "12"
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
                    project = create_project(db, ProjectCreate(name="video no frames project", description="", domain_type="video", status="active"))
                    storage_info = storage_client.upload_bytes("long-lecture.mp4", b"fake video bytes", "video/mp4")
                    asset = create_asset(
                        db,
                        AssetCreate(
                            project_id=project.id,
                            asset_type="video",
                            file_name="long-lecture.mp4",
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

                progress_updates = []

                def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                    progress_updates.append({"status": status, "progress": progress, "summary": result_summary, "error": error_message})
                    return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

                segment_path = str(Path(tmp_dir) / "segment_000001.mp4")
                with patch("app.workers.jobs.split_video", return_value=[segment_path]), \
                    patch("app.workers.jobs.extract_frames", side_effect=AssertionError("frame extraction should be skipped when MEDIA_ENABLE_FRAME_EXTRACTION=false")), \
                    patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("OCR should be skipped when frame extraction is disabled")), \
                    patch("app.workers.jobs.transcribe_audio", return_value={"text": "transcript without frame extraction", "segments": []}), \
                    patch("app.workers.jobs.update_task_status", side_effect=record_update):
                    result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

                db = SessionLocal()
                try:
                    stored_task = get_task(db, task_id)
                    units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                    self.assertEqual(result["status"], "preprocessed")
                    self.assertEqual(stored_task.status, "success")
                    self.assertTrue(result["semantic"]["frame_extraction_skipped"])
                    self.assertTrue(result["semantic"]["ocr_skipped"])
                    self.assertEqual(result["semantic"]["ocr"], [])
                    self.assertEqual(result["semantic"]["frames"], [])
                    self.assertEqual(stored_task.result_summary, "media preprocess done: 1 segment(s), 0 frame(s), max 7200 sec")
                    self.assertTrue(any(unit.labels.get("source") == "media_transcript" for unit in units))
                    self.assertFalse(any(unit.labels.get("source") == "media_ocr" for unit in units))
                    summaries = [item["summary"] for item in progress_updates if item["summary"]]
                    self.assertIn("frame extraction skipped: disabled", summaries)
                    self.assertIn("ocr skipped: frame extraction disabled", summaries)
                finally:
                    db.close()
                    engine.dispose()
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_preprocess_cleans_task_media_temp_directories_after_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-cleanup-test.db'}"
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
            from app.services.crud_service import create_asset, create_project, create_task
            from app.core.storage import storage_client
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="media cleanup project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("cleanup.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="cleanup.mp4",
                        file_path=storage_info["object_name"],
                        mime_type="video/mp4",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
                source_path = str(Path(storage_info["file_path"]))
            finally:
                db.close()

            temp_dirs = []

            def fake_split(video_path, output_dir):
                self.assertEqual(video_path, source_path)
                segment_dir = Path(output_dir)
                segment_dir.mkdir(parents=True, exist_ok=True)
                segment_path = segment_dir / "segment_000001.mp4"
                segment_path.write_bytes(b"segment")
                temp_dirs.append(segment_dir)
                return [str(segment_path)]

            def fake_frames(video_path, output_dir):
                self.assertEqual(video_path, source_path)
                frame_dir = Path(output_dir)
                frame_dir.mkdir(parents=True, exist_ok=True)
                frame_path = frame_dir / "frame_000001.jpg"
                frame_path.write_bytes(b"frame")
                temp_dirs.append(frame_dir)
                return [str(frame_path)]

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", side_effect=fake_frames), \
                patch("app.workers.jobs.transcribe_audio", return_value={"text": "cleanup segment transcript", "segments": []}), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[{"frame": "frame_000001.jpg", "text": "cleanup frame text", "confidence": 0.8}]):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            try:
                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(len(temp_dirs), 2)
                for temp_dir in temp_dirs:
                    self.assertIn(task_id, temp_dir.name)
                    self.assertFalse(temp_dir.exists())
                self.assertTrue(Path(source_path).is_file())
            finally:
                engine.dispose()

    def test_preprocess_transcribes_audio_without_video_commands(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-audio-test.db'}"
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
                project = create_project(db, ProjectCreate(name="audio project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes("briefing.mp3", b"fake audio bytes", "audio/mpeg")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="audio",
                        file_name="briefing.mp3",
                        file_path=storage_info["object_name"],
                        mime_type="audio/mpeg",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
                expected_path = str(storage_client._local_path(storage_info["object_name"]))
            finally:
                db.close()

            transcript = {
                "text": "Quarterly roadmap focuses on stable multimodal conversion.",
                "segments": [{"start": 0.0, "end": 4.2, "text": "Quarterly roadmap focuses on stable multimodal conversion."}],
            }
            audio_segment_path = str(Path(tmp_dir) / "audio_segment_000001.wav")
            with patch("app.workers.jobs.split_audio", return_value=[audio_segment_path]) as split_audio_mock, \
                patch("app.workers.jobs.split_video", side_effect=AssertionError("audio preprocessing should not split video")), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("audio preprocessing should not extract frames")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("audio preprocessing should not run OCR")), \
                patch("app.workers.jobs.transcribe_audio", return_value=transcript) as transcribe_mock:
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(result["semantic"]["extraction_status"], "text_extracted")
                self.assertEqual(result["semantic"]["source"], "audio_transcript")
                self.assertEqual(result["semantic"]["segment_count"], 1)
                split_audio_mock.assert_called_once()
                self.assertEqual(split_audio_mock.call_args[0][0], expected_path)
                transcribe_mock.assert_called_once_with(audio_segment_path)
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0].unit_type, "audio")
                self.assertEqual(units[0].labels["source"], "audio_transcript")
                self.assertEqual(units[0].labels["extraction_status"], "text_extracted")
                self.assertIn("stable multimodal conversion", units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_transcribes_audio_in_segments_with_progress(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-audio-segments-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["MEDIA_SEGMENT_SECONDS"] = "300"
            os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "2"
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
                project = create_project(db, ProjectCreate(name="audio segments project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes("long-briefing.mp3", b"fake long audio bytes", "audio/mpeg")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="audio",
                        file_name="long-briefing.mp3",
                        file_path=storage_info["object_name"],
                        mime_type="audio/mpeg",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
                expected_path = str(storage_client._local_path(storage_info["object_name"]))
            finally:
                db.close()

            calls = {"split_audio": None, "transcribe_audio": []}

            def fake_split_audio(audio_path, output_dir):
                calls["split_audio"] = audio_path
                return [
                    str(Path(output_dir) / "audio_segment_000001.wav"),
                    str(Path(output_dir) / "audio_segment_000002.wav"),
                ]

            def fake_transcribe(audio_path):
                calls["transcribe_audio"].append(audio_path)
                segment_name = Path(audio_path).stem
                return {
                    "text": f"{segment_name} stable conversion transcript.",
                    "segments": [{"start": 0.0, "end": 10.0, "text": segment_name}],
                }

            progress_updates = []
            real_update_task_status = update_task_status

            def record_update(db, task_id_arg, status, progress=None, result_summary=None, error_message=None):
                progress_updates.append({"status": status, "progress": progress, "summary": result_summary})
                return real_update_task_status(db, task_id_arg, status, progress, result_summary, error_message)

            with patch("app.workers.jobs.split_audio", side_effect=fake_split_audio), \
                patch("app.workers.jobs.split_video", side_effect=AssertionError("audio preprocessing should not split video")), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("audio preprocessing should not extract frames")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("audio preprocessing should not run OCR")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
                patch("app.workers.jobs.update_task_status", side_effect=record_update):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                summaries = [item["summary"] for item in progress_updates if item["summary"]]

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(calls["split_audio"], expected_path)
                self.assertEqual(len(calls["transcribe_audio"]), 2)
                self.assertEqual(calls["transcribe_audio"], result["semantic"]["segments"])
                self.assertEqual(result["semantic"]["segment_count"], 2)
                self.assertEqual(result["semantic"]["segment_seconds"], 300)
                self.assertEqual(result["semantic"]["max_segment_count"], 2)
                self.assertEqual(result["semantic"]["max_input_seconds"], 600)
                self.assertEqual(result["semantic"]["source"], "audio_transcript")
                self.assertIn("audio split running", summaries)
                self.assertIn("audio split done: 2 segment(s)", summaries)
                self.assertIn("audio transcription running: 1/2 segment(s)", summaries)
                self.assertIn("audio transcription running: 2/2 segment(s)", summaries)
                self.assertIn("audio transcription done: 2/2 segment(s)", summaries)
                self.assertTrue(any(unit.labels.get("source") == "audio_transcript" for unit in units))
                self.assertTrue(any("audio_segment_000001 stable conversion transcript" in unit.content for unit in units))
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_fails_when_all_audio_transcription_segments_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-audio-transcription-failure-test.db'}"
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
                project = create_project(db, ProjectCreate(name="audio failure project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes("broken-audio.mp3", b"fake broken audio bytes", "audio/mpeg")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="audio",
                        file_name="broken-audio.mp3",
                        file_path=storage_info["object_name"],
                        mime_type="audio/mpeg",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            with patch("app.workers.jobs.split_audio", return_value=[str(Path(tmp_dir) / "audio_segment_000001.wav")]), \
                patch("app.workers.jobs.split_video", side_effect=AssertionError("audio preprocessing should not split video")), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("audio preprocessing should not extract frames")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("audio preprocessing should not run OCR")), \
                patch("app.workers.jobs.transcribe_audio", return_value={"status": "failed", "text": "", "segments": [], "error": "whisper model crashed"}):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "audio transcription failed: whisper model crashed")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "audio transcription failed: whisper model crashed")
                self.assertEqual(units, [])
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_audio_records_partial_transcription_failures_without_failing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-audio-partial-transcription-test.db'}"
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
                project = create_project(db, ProjectCreate(name="audio partial transcription project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes("partial-audio.mp3", b"fake audio bytes", "audio/mpeg")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="audio",
                        file_name="partial-audio.mp3",
                        file_path=storage_info["object_name"],
                        mime_type="audio/mpeg",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            expected_segments = []

            def fake_split_audio(audio_path, output_dir):
                expected_segments[:] = [
                    str(Path(output_dir) / "audio_segment_000001.wav"),
                    str(Path(output_dir) / "audio_segment_000002.wav"),
                    str(Path(output_dir) / "audio_segment_000003.wav"),
                ]
                return expected_segments

            def fake_transcribe(segment_path):
                segment_name = Path(segment_path).stem
                if segment_name == "audio_segment_000002":
                    return {"status": "failed", "text": "", "segments": [], "error": "transcription timeout after 900 sec"}
                return {
                    "status": "success",
                    "text": f"{segment_name} 可用音频转写",
                    "segments": [{"start": 0.0, "end": 8.0, "text": f"{segment_name} 可用音频转写"}],
                    "error": None,
                }

            with patch("app.workers.jobs.split_audio", side_effect=fake_split_audio), \
                patch("app.workers.jobs.split_video", side_effect=AssertionError("audio preprocessing should not split video")), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("audio preprocessing should not extract frames")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("audio preprocessing should not run OCR")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                transcript_units = [unit for unit in units if unit.labels.get("source") == "audio_transcript"]

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(result["semantic"]["extraction_status"], "partial_text_extracted")
                self.assertEqual(result["semantic"]["transcription_success_count"], 2)
                self.assertEqual(result["semantic"]["transcription_failed_count"], 1)
                self.assertEqual(result["semantic"]["transcription_failed_segments"][0]["segment"], expected_segments[1])
                self.assertEqual(result["semantic"]["transcription_failed_segments"][0]["error"], "transcription timeout after 900 sec")
                self.assertIn("partial transcription", stored_task.result_summary)
                self.assertEqual(len(transcript_units), 1)
                self.assertIn("audio_segment_000001 可用音频转写", transcript_units[0].content)
                self.assertIn("audio_segment_000003 可用音频转写", transcript_units[0].content)
                self.assertNotIn("transcription timeout", transcript_units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_preserves_audio_segment_checkpoint_when_canceled_mid_transcription(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-audio-segment-cancel-test.db'}"
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
                project = create_project(db, ProjectCreate(name="audio cancel project", description="", domain_type="knowledge", status="active"))
                storage_info = storage_client.upload_bytes("cancel-audio.mp3", b"fake cancel audio bytes", "audio/mpeg")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="audio",
                        file_name="cancel-audio.mp3",
                        file_path=storage_info["object_name"],
                        mime_type="audio/mpeg",
                        metadata={"storage": storage_info["storage"]},
                    ),
                )
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
                project_id = project.id
                asset_id = asset.id
                task_id = task.id
            finally:
                db.close()

            def fake_split_audio(audio_path, output_dir):
                return [
                    str(Path(output_dir) / "audio_segment_000001.wav"),
                    str(Path(output_dir) / "audio_segment_000002.wav"),
                ]

            def cancel_after_first_audio_segment(audio_path):
                db = SessionLocal()
                try:
                    update_task_status(db, task_id, "canceled", 65, result_summary="Task canceled by user")
                finally:
                    db.close()
                return {
                    "text": "audio segment one already produced useful transcript.",
                    "segments": [{"start": 0.0, "end": 8.0, "text": "audio segment one already produced useful transcript."}],
                }

            with patch("app.workers.jobs.split_audio", side_effect=fake_split_audio), \
                patch("app.workers.jobs.split_video", side_effect=AssertionError("audio preprocessing should not split video")), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("audio preprocessing should not extract frames")), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("audio preprocessing should not run OCR")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=cancel_after_first_audio_segment):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                transcript_units = [unit for unit in units if unit.labels.get("source") == "audio_transcript"]

                self.assertEqual(result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(len(transcript_units), 1)
                self.assertEqual(transcript_units[0].unit_type, "transcript")
                self.assertIn("audio segment one already produced useful transcript", transcript_units[0].content)
                self.assertEqual(transcript_units[0].labels["checkpoint"], "segment_transcript")
                self.assertEqual(transcript_units[0].labels["segment_count"], 2)
                self.assertEqual(transcript_units[0].labels["segment_completed_count"], 1)
                self.assertEqual(transcript_units[0].labels["extraction_status"], "partial_text_extracted")
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

            temp_dirs = []

            def cancel_after_split(video_path, output_dir):
                segment_dir = Path(output_dir)
                segment_dir.mkdir(parents=True, exist_ok=True)
                (segment_dir / "segment_000001.mp4").write_bytes(b"partial segment")
                temp_dirs.append(segment_dir)
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
                self.assertEqual(len(temp_dirs), 1)
                self.assertFalse(temp_dirs[0].exists())
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_keeps_canceled_status_when_media_command_is_canceled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-command-cancel-test.db'}"
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
            from app.services.media_service import MediaProcessingError
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="cancel command media project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("cancel-command.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="cancel-command.mp4",
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

            def cancel_during_split(video_path, output_dir):
                db = SessionLocal()
                try:
                    update_task_status(db, task_id, "canceled", 35, result_summary="Task canceled by user")
                finally:
                    db.close()
                raise MediaProcessingError("split_video canceled")

            with patch("app.workers.jobs.split_video", side_effect=cancel_during_split), \
                patch("app.workers.jobs.extract_frames", side_effect=AssertionError("canceled command should not extract frames")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=AssertionError("canceled command should not transcribe")):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(stored_task.progress, 35)
                self.assertEqual(stored_task.result_summary, "Task canceled by user")
                self.assertEqual(stored_task.error_message, None)
                self.assertEqual(units, [])
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_preserves_completed_segment_transcripts_when_canceled_mid_transcription(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-segment-cancel-test.db'}"
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
                project = create_project(db, ProjectCreate(name="segment cancel media project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("segment-cancel.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="segment-cancel.mp4",
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

            def fake_split(video_path, output_dir):
                return [str(Path(output_dir) / "segment_000001.mp4"), str(Path(output_dir) / "segment_000002.mp4")]

            def cancel_after_first_segment(audio_path):
                db = SessionLocal()
                try:
                    update_task_status(db, task_id, "canceled", 65, result_summary="Task canceled by user")
                finally:
                    db.close()
                return {
                    "text": "segment one already produced useful transcript.",
                    "segments": [{"start": 0.0, "end": 8.0, "text": "segment one already produced useful transcript."}],
                }

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=["frame_000001.jpg"]), \
                patch("app.workers.jobs.transcribe_audio", side_effect=cancel_after_first_segment), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("canceled media task should not run OCR")):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                transcript_units = [unit for unit in units if unit.labels.get("source") == "media_transcript"]

                self.assertEqual(result["status"], "canceled")
                self.assertEqual(stored_task.status, "canceled")
                self.assertEqual(len(transcript_units), 1)
                self.assertEqual(transcript_units[0].unit_type, "transcript")
                self.assertIn("segment one already produced useful transcript", transcript_units[0].content)
                self.assertEqual(transcript_units[0].labels["segment_index"], 0)
                self.assertEqual(transcript_units[0].labels["segment_count"], 2)
                self.assertEqual(transcript_units[0].labels["extraction_status"], "partial_text_extracted")
            finally:
                db.close()
                engine.dispose()

    def test_failed_media_preprocess_removes_partial_segment_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-failed-checkpoint-cleanup-test.db'}"
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
            from app.services.media_service import MediaProcessingError
            from app.workers.jobs import preprocess_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="failed checkpoint cleanup project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("failed-checkpoint.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="failed-checkpoint.mp4",
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

            def fake_split(video_path, output_dir):
                return [
                    str(Path(output_dir) / "segment_000001.mp4"),
                    str(Path(output_dir) / "segment_000002.mp4"),
                ]

            def fail_after_first_checkpoint(segment_path):
                if Path(segment_path).stem == "segment_000001":
                    return {
                        "status": "success",
                        "text": "first segment should be cleaned after failure.",
                        "segments": [{"start": 0.0, "end": 8.0, "text": "first segment should be cleaned after failure."}],
                        "error": None,
                    }
                raise MediaProcessingError("transcription worker crashed")

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=[]), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("failed transcription should not reach OCR")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fail_after_first_checkpoint):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                traces = db.query(TraceLink).filter(
                    TraceLink.source_type == "asset",
                    TraceLink.source_id == asset_id,
                    TraceLink.target_type == "semantic_unit",
                ).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "transcription worker crashed")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "transcription worker crashed")
                self.assertEqual(units, [])
                self.assertEqual(traces, [])
            finally:
                db.close()
                engine.dispose()

    def test_unexpected_media_preprocess_retry_removes_partial_segment_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-unexpected-checkpoint-cleanup-test.db'}"
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
                project = create_project(db, ProjectCreate(name="unexpected checkpoint cleanup project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("unexpected-checkpoint.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="unexpected-checkpoint.mp4",
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

            def fake_split(video_path, output_dir):
                return [
                    str(Path(output_dir) / "segment_000001.mp4"),
                    str(Path(output_dir) / "segment_000002.mp4"),
                ]

            def crash_after_first_checkpoint(segment_path):
                if Path(segment_path).stem == "segment_000001":
                    return {
                        "status": "success",
                        "text": "first segment checkpoint must be removed before retry.",
                        "segments": [{"start": 0.0, "end": 8.0, "text": "first segment checkpoint must be removed before retry."}],
                        "error": None,
                    }
                raise RuntimeError("unexpected decoder crash")

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=[]), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("unexpected transcription crash should not reach OCR")), \
                patch("app.workers.jobs.transcribe_audio", side_effect=crash_after_first_checkpoint):
                with self.assertRaisesRegex(RuntimeError, "unexpected decoder crash"):
                    preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                traces = db.query(TraceLink).filter(
                    TraceLink.source_type == "asset",
                    TraceLink.source_id == asset_id,
                    TraceLink.target_type == "semantic_unit",
                ).all()

                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "unexpected decoder crash")
                self.assertEqual(units, [])
                self.assertEqual(traces, [])
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_fails_when_all_video_transcription_segments_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-video-transcription-failure-test.db'}"
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
                project = create_project(db, ProjectCreate(name="video failure project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("broken-video.mp4", b"fake broken video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="broken-video.mp4",
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

            with patch("app.workers.jobs.split_video", return_value=[str(Path(tmp_dir) / "segment_000001.mp4")]), \
                patch("app.workers.jobs.extract_frames", return_value=[]), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[]), \
                patch("app.workers.jobs.transcribe_audio", return_value={"status": "failed", "text": "", "segments": [], "error": "whisper gpu unavailable"}):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "media transcription failed: whisper gpu unavailable")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "media transcription failed: whisper gpu unavailable")
                self.assertEqual(units, [])
            finally:
                db.close()
                engine.dispose()

    def test_preprocess_video_records_partial_transcription_failures_without_failing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-video-partial-transcription-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "3"
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
                project = create_project(db, ProjectCreate(name="video partial transcription project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("partial-video.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="partial-video.mp4",
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

            expected_segments = []

            def fake_split(video_path, output_dir):
                expected_segments[:] = [
                    str(Path(output_dir) / "segment_000001.mp4"),
                    str(Path(output_dir) / "segment_000002.mp4"),
                    str(Path(output_dir) / "segment_000003.mp4"),
                ]
                return expected_segments

            def fake_transcribe(segment_path):
                segment_name = Path(segment_path).stem
                if segment_name == "segment_000002":
                    return {"status": "failed", "text": "", "segments": [], "error": "transcription timeout after 900 sec"}
                return {
                    "status": "success",
                    "text": f"{segment_name} 可用转写内容",
                    "segments": [{"start": 0.0, "end": 8.0, "text": f"{segment_name} 可用转写内容"}],
                    "error": None,
                }

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=[]), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[]), \
                patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
                transcript_units = [unit for unit in units if unit.labels.get("source") == "media_transcript"]
                summary_unit = next(unit for unit in units if unit.labels.get("source") == "media_asset")
                summary_payload = json.loads(summary_unit.content)

                self.assertEqual(result["status"], "preprocessed")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(summary_payload["extraction_status"], "partial_text_extracted")
                self.assertEqual(summary_payload["transcription_success_count"], 2)
                self.assertEqual(summary_payload["transcription_failed_count"], 1)
                self.assertEqual(summary_payload["transcription_failed_segments"][0]["segment"], expected_segments[1])
                self.assertEqual(summary_payload["transcription_failed_segments"][0]["error"], "transcription timeout after 900 sec")
                self.assertIn("partial transcription", stored_task.result_summary)
                self.assertEqual(len(transcript_units), 1)
                self.assertIn("segment_000001 可用转写内容", transcript_units[0].content)
                self.assertIn("segment_000003 可用转写内容", transcript_units[0].content)
                self.assertNotIn("transcription timeout", transcript_units[0].content)
            finally:
                db.close()
                engine.dispose()

    def test_canceled_media_rerun_removes_partial_segment_checkpoint_and_preserves_previous_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-preprocess-media-canceled-rerun-test.db'}"
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
                project = create_project(db, ProjectCreate(name="media canceled rerun project", description="", domain_type="video", status="active"))
                storage_info = storage_client.upload_bytes("rerun-cancel.mp4", b"fake video bytes", "video/mp4")
                asset = create_asset(
                    db,
                    AssetCreate(
                        project_id=project.id,
                        asset_type="video",
                        file_name="rerun-cancel.mp4",
                        file_path=storage_info["object_name"],
                        mime_type="video/mp4",
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

            def fake_split(video_path, output_dir):
                return [str(Path(output_dir) / "segment_000001.mp4"), str(Path(output_dir) / "segment_000002.mp4")]

            def successful_transcribe(audio_path):
                segment_name = Path(audio_path).stem
                return {
                    "text": f"{segment_name} stable transcript from first run.",
                    "segments": [{"start": 0.0, "end": 8.0, "text": f"{segment_name} stable transcript from first run."}],
                }

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=["frame_000001.jpg"]), \
                patch("app.workers.jobs.transcribe_audio", side_effect=successful_transcribe), \
                patch("app.workers.jobs.run_ocr_on_frames", return_value=[{"frame": "frame_000001.jpg", "text": "first run frame text", "confidence": 0.8}]):
                first_result = preprocess_job.run({"task_id": first_task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                previous_units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()
                previous_contents = [unit.content for unit in previous_units]
                previous_sources = [unit.labels.get("source") for unit in previous_units]
            finally:
                db.close()

            def cancel_after_first_segment(audio_path):
                db = SessionLocal()
                try:
                    update_task_status(db, second_task_id, "canceled", 65, result_summary="Task canceled by user")
                finally:
                    db.close()
                return {
                    "text": "second run partial transcript should be discarded.",
                    "segments": [{"start": 0.0, "end": 8.0, "text": "second run partial transcript should be discarded."}],
                }

            with patch("app.workers.jobs.split_video", side_effect=fake_split), \
                patch("app.workers.jobs.extract_frames", return_value=["frame_000001.jpg"]), \
                patch("app.workers.jobs.transcribe_audio", side_effect=cancel_after_first_segment), \
                patch("app.workers.jobs.run_ocr_on_frames", side_effect=AssertionError("canceled media rerun should not run OCR")):
                second_result = preprocess_job.run({"task_id": second_task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_second_task = get_task(db, second_task_id)
                units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()

                self.assertEqual(first_result["status"], "preprocessed")
                self.assertEqual(second_result["status"], "canceled")
                self.assertEqual(stored_second_task.status, "canceled")
                self.assertEqual(stored_second_task.result_summary, "Task canceled by user")
                self.assertEqual([unit.content for unit in units], previous_contents)
                self.assertEqual([unit.labels.get("source") for unit in units], previous_sources)
                self.assertFalse(any("second run partial transcript" in unit.content for unit in units))
                self.assertFalse(any(unit.labels.get("checkpoint") == "segment_transcript" for unit in units))
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

            temp_dirs = []

            def fail_after_temp_file(video_path, output_dir):
                segment_dir = Path(output_dir)
                segment_dir.mkdir(parents=True, exist_ok=True)
                (segment_dir / "segment_000001.mp4").write_bytes(b"bad segment")
                temp_dirs.append(segment_dir)
                raise MediaProcessingError("split_video failed: invalid media")

            with patch("app.workers.jobs.split_video", side_effect=fail_after_temp_file):
                result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)

                self.assertEqual(result["status"], "failed")
                self.assertEqual(stored_task.status, "failed")
                self.assertIn("invalid media", stored_task.error_message)
                self.assertEqual(len(temp_dirs), 1)
                self.assertFalse(temp_dirs[0].exists())
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
