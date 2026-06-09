import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class SampleGenerationFlowTests(unittest.TestCase):
    def test_sample_job_generates_sample_from_semantic_unit_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="sample project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="unit-001",
                    project_id=project.id,
                    asset_id="asset-001",
                    segment_id=None,
                    unit_type="text",
                    title="Refund policy",
                    content="Customers can request a refund within 30 days when they provide the original receipt.",
                    labels={"source": "text_asset", "stage": "preprocess"},
                    confidence=0.93,
                )
                db.add(unit)
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-001", task_type="generate_samples", input_config={"source_unit_id": unit.id}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = sample_job.run({"task_id": task_id, "project_id": project_id, "source_unit_id": "unit-001"})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()
                trace_links = db.query(TraceLink).filter(TraceLink.source_id == "unit-001", TraceLink.target_type == "generated_sample").all()

                self.assertEqual(result["status"], "samples_generated")
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0].source_unit_id, "unit-001")
                self.assertEqual(samples[0].sample_type, "qa")
                self.assertIn("Refund policy", samples[0].input_text)
                self.assertIn("30 days", samples[0].output_text)
                self.assertNotEqual(samples[0].input_text, "sample input")
                self.assertNotEqual(samples[0].output_text, "sample output")
                self.assertEqual(len(trace_links), 1)
            finally:
                db.close()
                engine.dispose()

    def test_sample_job_uses_ollama_structured_sample_when_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-ollama-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="ollama sample project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="unit-ollama",
                    project_id=project.id,
                    asset_id="asset-ollama",
                    segment_id=None,
                    unit_type="text",
                    title="Escalation rule",
                    content="Escalate security incidents to the on-call owner within 15 minutes.",
                    labels={"source": "text_asset", "stage": "preprocess"},
                    confidence=0.93,
                )
                db.add(unit)
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-ollama", task_type="generate_samples", input_config={"source_unit_id": unit.id}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            with patch("app.workers.jobs.generate_with_ollama") as generate:
                generate.return_value = {
                    "enabled": True,
                    "status": "success",
                    "text": '{"sample_type":"qa","input":"When should security incidents be escalated?","output":"Within 15 minutes to the on-call owner.","quality_score":0.94}',
                    "error": None,
                }
                result = sample_job.run({"task_id": task_id, "project_id": project_id, "source_unit_id": "unit-ollama"})

            db = SessionLocal()
            try:
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()

                self.assertEqual(result["status"], "samples_generated")
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0].input_text, "When should security incidents be escalated?")
                self.assertEqual(samples[0].output_text, "Within 15 minutes to the on-call owner.")
                self.assertEqual(samples[0].quality_score, 0.94)
            finally:
                db.close()
                engine.dispose()

    def test_sample_job_parses_fenced_ollama_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-ollama-fenced-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="fenced ollama sample project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="unit-ollama-fenced",
                    project_id=project.id,
                    asset_id="asset-ollama-fenced",
                    segment_id=None,
                    unit_type="text",
                    title="RAG policy",
                    content="Keep source metadata when generating RAG chunks.",
                    labels={"source": "text_asset", "stage": "preprocess"},
                    confidence=0.93,
                )
                db.add(unit)
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-ollama-fenced", task_type="generate_samples", input_config={"source_unit_id": unit.id}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            with patch("app.workers.jobs.generate_with_ollama") as generate:
                generate.return_value = {
                    "enabled": True,
                    "status": "success",
                    "text": 'Here is the sample:\n```json\n{"sample_type":"qa","input":"What metadata should RAG chunks keep?","output":"They should keep source metadata.","quality_score":0.91}\n```',
                    "error": None,
                }
                result = sample_job.run({"task_id": task_id, "project_id": project_id, "source_unit_id": "unit-ollama-fenced"})

            db = SessionLocal()
            try:
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()

                self.assertEqual(result["status"], "samples_generated")
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0].input_text, "What metadata should RAG chunks keep?")
                self.assertEqual(samples[0].output_text, "They should keep source metadata.")
                self.assertEqual(samples[0].quality_score, 0.91)
            finally:
                db.close()
                engine.dispose()

    def test_sample_job_generates_samples_for_project_units_when_source_is_omitted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-batch-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="batch sample project", description="", domain_type="knowledge", status="active"))
                units = [
                    SemanticUnit(
                        id=f"unit-batch-{index}",
                        project_id=project.id,
                        asset_id="asset-batch",
                        segment_id=None,
                        unit_type="text",
                        title=f"Policy chunk {index}",
                        content=f"Policy chunk {index} says reviewers must check evidence item {index}.",
                        labels={"source": "text_asset", "stage": "preprocess", "chunk_index": index},
                        confidence=0.91,
                    )
                    for index in range(3)
                ]
                db.add_all(units)
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-batch", task_type="generate_samples", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = sample_job.run({"task_id": task_id, "project_id": project_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).order_by(GeneratedSample.source_unit_id.asc()).all()
                trace_links = db.query(TraceLink).filter(TraceLink.target_type == "generated_sample").all()

                self.assertEqual(result["status"], "samples_generated")
                self.assertEqual(result["sample_count"], 3)
                self.assertEqual(stored_task.status, "success")
                self.assertEqual(len(samples), 3)
                self.assertEqual({sample.source_unit_id for sample in samples}, {"unit-batch-0", "unit-batch-1", "unit-batch-2"})
                self.assertTrue(all("Policy chunk" in sample.input_text for sample in samples))
                self.assertTrue(all("evidence item" in sample.output_text for sample in samples))
                self.assertEqual(len(trace_links), 3)
            finally:
                db.close()
                engine.dispose()

    def test_sample_job_rejects_empty_project_without_creating_placeholder_sample(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-empty-project-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="empty sample project", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="generate_samples", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = sample_job.run({"task_id": task_id, "project_id": project_id})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "No semantic units available for sample generation")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "No semantic units available for sample generation")
                self.assertEqual(samples, [])
            finally:
                db.close()
                engine.dispose()

    def test_sample_job_rejects_missing_source_unit_without_creating_placeholder_sample(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-sample-missing-unit-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.generated_sample import GeneratedSample
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import sample_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="missing source unit project", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="generate_samples", input_config={"source_unit_id": "missing-unit"}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = sample_job.run({"task_id": task_id, "project_id": project_id, "source_unit_id": "missing-unit"})

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "Source semantic unit not found for project")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "Source semantic unit not found for project")
                self.assertEqual(samples, [])
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
