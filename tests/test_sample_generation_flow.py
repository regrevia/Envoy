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


if __name__ == "__main__":
    unittest.main()
