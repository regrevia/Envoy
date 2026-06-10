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

    def test_export_job_filters_canceled_preprocess_units_and_polluted_samples(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-job-filter-canceled-test.db'}"
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
            from app.models.processing_task import ProcessingTask
            from app.models.semantic_unit import SemanticUnit
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task, get_task
            from app.workers.jobs import export_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="export filter canceled project", description="", domain_type="knowledge", status="active"))
                db.add_all(
                    [
                        ProcessingTask(
                            id="export-success-preprocess",
                            project_id=project.id,
                            asset_id="asset-stable",
                            task_type="preprocess",
                            status="success",
                            progress=100,
                            input_config={},
                        ),
                        ProcessingTask(
                            id="export-canceled-preprocess",
                            project_id=project.id,
                            asset_id="asset-canceled",
                            task_type="preprocess",
                            status="canceled",
                            progress=45,
                            input_config={},
                            result_summary="Task canceled by user",
                        ),
                    ]
                )
                db.add_all(
                    [
                        SemanticUnit(
                            id="export-stable-unit",
                            project_id=project.id,
                            asset_id="asset-stable",
                            segment_id=None,
                            unit_type="text",
                            title="稳定导出素材",
                            content="稳定完成的导出内容。",
                            labels={"source": "document_text", "stage": "preprocess", "task_id": "export-success-preprocess"},
                            confidence=0.91,
                        ),
                        SemanticUnit(
                            id="export-canceled-unit",
                            project_id=project.id,
                            asset_id="asset-canceled",
                            segment_id=None,
                            unit_type="document",
                            title="取消导出半成品",
                            content="取消任务半成品不能进入通用导出。",
                            labels={"source": "document_text", "stage": "preprocess", "task_id": "export-canceled-preprocess"},
                            confidence=0.4,
                        ),
                        SemanticUnit(
                            id="export-canceled-checkpoint",
                            project_id=project.id,
                            asset_id="asset-canceled",
                            segment_id=None,
                            unit_type="transcript",
                            title="取消导出 checkpoint",
                            content="取消任务 checkpoint 不能进入通用导出。",
                            labels={
                                "source": "media_transcript",
                                "stage": "preprocess",
                                "task_id": "export-canceled-preprocess",
                                "checkpoint": "segment_transcript",
                            },
                            confidence=0.4,
                        ),
                    ]
                )
                db.add_all(
                    [
                        GeneratedSample(
                            id="export-stable-sample",
                            project_id=project.id,
                            source_unit_id="export-stable-unit",
                            sample_type="qa",
                            input_text="稳定样本输入",
                            output_text="稳定样本输出可以进入通用导出。",
                            quality_score=0.92,
                        ),
                        GeneratedSample(
                            id="export-canceled-sample",
                            project_id=project.id,
                            source_unit_id="export-canceled-unit",
                            sample_type="qa",
                            input_text="取消样本输入",
                            output_text="取消任务样本不能进入通用导出。",
                            quality_score=0.3,
                        ),
                        GeneratedSample(
                            id="export-canceled-checkpoint-sample",
                            project_id=project.id,
                            source_unit_id="export-canceled-checkpoint",
                            sample_type="qa",
                            input_text="取消 checkpoint 样本输入",
                            output_text="取消 checkpoint 样本不能进入通用导出。",
                            quality_score=0.3,
                        ),
                        GeneratedSample(
                            id="export-placeholder-sample",
                            project_id=project.id,
                            source_unit_id="export-stable-unit",
                            sample_type="qa",
                            input_text="稳定导出素材\n\nWhat are the key points from 稳定导出素材?",
                            output_text="No source content was available.",
                            quality_score=0.45,
                        ),
                    ]
                )
                db.add_all(
                    [
                        TraceLink(id="trace-stable-sample", source_type="semantic_unit", source_id="export-stable-unit", target_type="generated_sample", target_id="export-stable-sample", relation_type="derived_from"),
                        TraceLink(id="trace-canceled-sample", source_type="semantic_unit", source_id="export-canceled-unit", target_type="generated_sample", target_id="export-canceled-sample", relation_type="derived_from"),
                    ]
                )
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="export", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = export_job.run({"task_id": task_id, "project_id": project_id})

            jsonl_path = export_dir / f"{task_id}.jsonl"
            md_path = export_dir / f"{task_id}.md"
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            report = md_path.read_text(encoding="utf-8")
            exported_text = json.dumps(rows, ensure_ascii=False) + "\n" + report

            db = SessionLocal()
            try:
                stored_task = get_task(db, task_id)

                self.assertEqual(result["status"], "exported")
                self.assertEqual(result["row_count"], 2)
                self.assertEqual(stored_task.status, "success")
                self.assertEqual({row["id"] for row in rows}, {"export-stable-unit", "export-stable-sample"})
                self.assertIn("稳定完成的导出内容。", exported_text)
                self.assertIn("稳定样本输出可以进入通用导出。", exported_text)
                self.assertNotIn("No source content was available", exported_text)
                self.assertNotIn("取消任务半成品", exported_text)
                self.assertNotIn("取消任务 checkpoint", exported_text)
                self.assertNotIn("取消任务样本", exported_text)
                self.assertNotIn("取消 checkpoint 样本", exported_text)
                self.assertNotIn("trace-canceled-sample", exported_text)
            finally:
                db.close()
                engine.dispose()

    def test_export_job_renders_structured_semantic_content_as_readable_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_dir = Path(tmp_dir) / "exports"
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-structured-json-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(export_dir)
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.schemas.task import TaskCreate
            from app.services.crud_service import create_project, create_task
            from app.workers.jobs import export_job

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="structured export project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="semantic-export-structured",
                    project_id=project.id,
                    asset_id="asset-export-structured",
                    segment_id=None,
                    unit_type="scene",
                    title="结构化导出摘要",
                    content=json.dumps(
                        {
                            "stage": "preprocess",
                            "transcript": {"text": "讲解了稳定处理长视频。"},
                            "ocr": [{"text": "画面文字：RAG Skill"}],
                            "rules": ["长任务必须可取消"],
                        },
                        ensure_ascii=False,
                    ),
                    labels={"source": "media_asset", "stage": "preprocess"},
                    confidence=0.82,
                )
                db.add(unit)
                db.commit()
                task = create_task(db, TaskCreate(project_id=project.id, asset_id="asset-export-structured", task_type="export", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            result = export_job.run({"task_id": task_id, "project_id": project_id})

            jsonl_path = export_dir / f"{task_id}.jsonl"
            md_path = export_dir / f"{task_id}.md"
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            report = md_path.read_text(encoding="utf-8")
            semantic_row = next(row for row in rows if row["record_type"] == "semantic_unit")
            engine.dispose()

            self.assertEqual(result["status"], "exported")
            self.assertEqual(semantic_row["content_format"], "json")
            for rendered_text in (semantic_row["content"], report):
                self.assertIn("讲解了稳定处理长视频。", rendered_text)
                self.assertIn("画面文字：RAG Skill", rendered_text)
                self.assertIn("长任务必须可取消", rendered_text)
                self.assertNotIn('"transcript"', rendered_text)
                self.assertNotIn('{"stage"', rendered_text)

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
