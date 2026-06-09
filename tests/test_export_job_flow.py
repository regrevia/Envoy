import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ExportJobFlowTests(unittest.TestCase):
    def test_export_skill_job_writes_manifest_and_registers_both_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-skill-job-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.artifact import Artifact
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import export_skill_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="skill job project", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-skill-001", task_type="export_skill", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = export_skill_job.run(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "source_asset_id": "asset-skill-001",
                    "title": "Support Skill",
                    "payload": [{"topic": "refund", "content": "Refund approval must be documented."}],
                }
            )

            manifest_path = Path(result["paths"]["manifest"])
            markdown_path = Path(result["paths"]["markdown"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                artifacts = db.query(Artifact).filter(Artifact.project_id == project_id, Artifact.artifact_type == "skill").all()
                traces = db.query(TraceLink).filter(TraceLink.source_id == "asset-skill-001", TraceLink.target_type == "artifact").all()

                self.assertEqual(result["status"], "skill_exported")
                self.assertEqual(stored_task.status, "success")
                self.assertTrue(markdown_path.exists())
                self.assertTrue(manifest_path.exists())
                self.assertEqual(manifest["schema_version"], "envoy.skill.v1")
                self.assertEqual(manifest["title"], "Support Skill")
                self.assertEqual(manifest["materials"][0]["topic"], "refund")
                self.assertEqual({Path(item.file_path).name for item in artifacts}, {"skill.md", "skill.json"})
                self.assertEqual(len(traces), 2)
            finally:
                db.close()
                engine.dispose()

    def test_export_skill_job_is_idempotent_for_artifacts_and_trace_links(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-skill-idempotent-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.artifact import Artifact
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task
            from app.workers.jobs import export_skill_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="skill idempotent project", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-skill-idem", task_type="export_skill", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            payload = {
                "task_id": task_id,
                "project_id": project_id,
                "source_asset_id": "asset-skill-idem",
                "title": "Idempotent Skill",
                "payload": [{"topic": "retry", "content": "Repeated worker runs must not duplicate artifacts."}],
            }
            first = export_skill_job.run(payload)
            second = export_skill_job.run(payload)

            db = SessionLocal()
            try:
                artifacts = db.query(Artifact).filter(Artifact.project_id == project_id, Artifact.artifact_type == "skill").all()
                traces = db.query(TraceLink).filter(TraceLink.source_id == "asset-skill-idem", TraceLink.target_type == "artifact").all()

                self.assertEqual(first["status"], "skill_exported")
                self.assertEqual(second["status"], "skill_exported")
                self.assertEqual({Path(item.file_path).name for item in artifacts}, {"skill.md", "skill.json"})
                self.assertEqual(len(artifacts), 2)
                self.assertEqual(len(traces), 2)
            finally:
                db.close()
                engine.dispose()

    def test_export_job_writes_real_project_units_samples_and_trace_links(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-job-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
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
            from app.workers.jobs import export_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="export project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="semantic-export-001",
                    project_id=project.id,
                    asset_id="asset-export-001",
                    segment_id=None,
                    unit_type="text",
                    title="Incident policy",
                    content="Escalate incidents within 15 minutes and attach audit evidence.",
                    labels={"source": "text_asset", "stage": "preprocess"},
                    confidence=0.91,
                )
                sample = GeneratedSample(
                    id="sample-export-001",
                    project_id=project.id,
                    source_unit_id=unit.id,
                    sample_type="qa",
                    input_text="When should incidents be escalated?",
                    output_text="Within 15 minutes with audit evidence attached.",
                    quality_score=0.94,
                )
                trace = TraceLink(
                    id="trace-export-001",
                    source_type="semantic_unit",
                    source_id=unit.id,
                    target_type="generated_sample",
                    target_id=sample.id,
                    relation_type="derived_from",
                )
                db.add_all([unit, sample, trace])
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-export-001", task_type="export", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = export_job.run({"task_id": task_id, "project_id": project_id})

            jsonl_path = export_dir / f"{task_id}.jsonl"
            md_path = export_dir / f"{task_id}.md"
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            report = md_path.read_text(encoding="utf-8")

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)

                self.assertEqual(result["status"], "exported")
                self.assertEqual(result["row_count"], 2)
                self.assertEqual(stored_task.status, "success")
                self.assertEqual({row["record_type"] for row in rows}, {"semantic_unit", "generated_sample"})
                self.assertTrue(any(row.get("title") == "Incident policy" and "audit evidence" in row.get("content", "") for row in rows))
                self.assertTrue(any(row.get("input_text") == "When should incidents be escalated?" for row in rows))
                self.assertFalse(any(row.get("status") == "exported" for row in rows))
                self.assertIn("## 摘要", report)
                self.assertIn("## 语义单元", report)
                self.assertIn("## 样本", report)
                self.assertIn("## 追踪关系", report)
                self.assertIn("Incident policy", report)
                self.assertIn("derived_from", report)
            finally:
                db.close()
                engine.dispose()

    def test_export_job_is_idempotent_for_registered_artifacts_and_trace_links(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-job-idempotent-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.artifact import Artifact
            from app.models.generated_sample import GeneratedSample
            from app.models.semantic_unit import SemanticUnit
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task
            from app.workers.jobs import export_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="export idempotent project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="semantic-export-idempotent",
                    project_id=project.id,
                    asset_id="asset-export-idempotent",
                    segment_id=None,
                    unit_type="text",
                    title="Retry policy",
                    content="Export retries should replace files without duplicating registered artifacts.",
                    labels={"source": "text_asset", "stage": "preprocess"},
                    confidence=0.91,
                )
                sample = GeneratedSample(
                    id="sample-export-idempotent",
                    project_id=project.id,
                    source_unit_id=unit.id,
                    sample_type="qa",
                    input_text="What should export retries avoid?",
                    output_text="Duplicate registered artifacts.",
                    quality_score=0.94,
                )
                db.add_all([unit, sample])
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-export-idempotent", task_type="export", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            first = export_job.run({"task_id": task_id, "project_id": project_id})
            second = export_job.run({"task_id": task_id, "project_id": project_id})

            db = SessionLocal()
            try:
                artifacts = db.query(Artifact).filter(Artifact.project_id == project_id).all()
                traces = db.query(TraceLink).filter(TraceLink.source_id == task_id, TraceLink.target_type == "artifact").all()

                self.assertEqual(first["status"], "exported")
                self.assertEqual(second["status"], "exported")
                self.assertEqual({Path(item.file_path).name for item in artifacts}, {f"{task_id}.jsonl", f"{task_id}.md"})
                self.assertEqual(len(artifacts), 2)
                self.assertEqual(len(traces), 2)
            finally:
                db.close()
                engine.dispose()

    def test_export_job_rejects_empty_project_without_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-empty-export-job-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.artifact import Artifact
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import export_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="empty export project", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="export", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = export_job.run({"task_id": task_id, "project_id": project_id})

            jsonl_path = export_dir / f"{task_id}.jsonl"
            md_path = export_dir / f"{task_id}.md"

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)
                artifacts = db.query(Artifact).filter(Artifact.project_id == project_id).all()

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "No exportable rows found for project")
                self.assertEqual(stored_task.status, "failed")
                self.assertEqual(stored_task.error_message, "No exportable rows found for project")
                self.assertFalse(jsonl_path.exists())
                self.assertFalse(md_path.exists())
                self.assertEqual(artifacts, [])
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
