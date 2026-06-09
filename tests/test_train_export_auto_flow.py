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


class TrainExportAutoFlowTests(unittest.TestCase):
    def test_manual_export_resolves_project_from_source_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-source-asset-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

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
                project = create_project(db, ProjectCreate(name="source asset export", description="", domain_type="knowledge", status="active"))
                asset = create_asset(
                    db,
                    AssetCreate(project_id=project.id, asset_type="text", file_name="source.txt", file_path="source.txt", mime_type="text/plain"),
                )
                project_id = project.id
                asset_id = asset.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports",
                    json={
                        "source_asset_id": asset_id,
                        "export_type": "rag",
                        "payload": [{"text": "source asset row", "metadata": {"source": "test"}}],
                    },
                )
                self.assertEqual(response.status_code, 200)

                history = client.get("/api/v1/train-exports/history").json()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["project_id"], project_id)
                self.assertEqual(history[0]["task_id"], asset_id)
                self.assertNotEqual(history[0]["project_id"], asset_id)
            finally:
                engine.dispose()

    def test_manual_export_resolves_project_from_source_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-source-task-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

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
                project = create_project(db, ProjectCreate(name="source task export", description="", domain_type="knowledge", status="active"))
                task = create_task(db, TaskCreate(project_id=project.id, asset_id=None, task_type="analyze", input_config={}))
                project_id = project.id
                task_id = task.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports",
                    json={
                        "source_task_id": task_id,
                        "export_type": "markdown",
                        "payload": [{"summary": "source task row"}],
                    },
                )
                self.assertEqual(response.status_code, 200)

                history = client.get("/api/v1/train-exports/history").json()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["project_id"], project_id)
                self.assertEqual(history[0]["task_id"], task_id)
                self.assertNotEqual(history[0]["project_id"], task_id)
            finally:
                engine.dispose()

    def test_auto_exports_markdown_skill_and_rag_from_project_semantic_units(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="导出链路项目", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-001",
                        project_id=project.id,
                        asset_id="asset-001",
                        segment_id=None,
                        unit_type="text",
                        title="核心知识片段",
                        content="第一段核心知识。第二段可训练内容。",
                        labels={"source": "text_asset", "stage": "preprocess"},
                        confidence=0.91,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                results = {}
                for export_type in ("markdown", "skill", "rag"):
                    response = client.post(
                        "/api/v1/train-exports/auto",
                        json={"project_id": project_id, "export_type": export_type, "title": f"{export_type} export"},
                    )
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertNotIn("error", body)
                    results[export_type] = body

                markdown_text = Path(results["markdown"]["path"]).read_text(encoding="utf-8")
                markdown_manifest = json.loads(Path(results["markdown"]["paths"]["manifest"]).read_text(encoding="utf-8"))
                skill_text = Path(results["skill"]["path"]).read_text(encoding="utf-8")
                skill_manifest = json.loads(Path(results["skill"]["paths"]["manifest"]).read_text(encoding="utf-8"))
                rag_rows = [
                    json.loads(line)
                    for line in Path(results["rag"]["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                rag_manifest = json.loads(Path(results["rag"]["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertIn("## 视频转化摘要", markdown_text)
                self.assertIn("第一段核心知识", markdown_text)
                self.assertEqual(markdown_manifest["schema_version"], "envoy.markdown.v1")
                self.assertEqual(markdown_manifest["source_task_id"], results["markdown"]["task_id"])
                self.assertEqual(markdown_manifest["project_id"], project_id)
                self.assertEqual(markdown_manifest["row_count"], 1)
                self.assertEqual(Path(markdown_manifest["files"]["markdown"]).name, "video_summary.md")
                self.assertEqual(Path(markdown_manifest["files"]["manifest"]).name, "markdown_manifest.json")
                self.assertIn("## 输入摘要", skill_text)
                self.assertIn("样本 1", skill_text)
                self.assertIn("第一段核心知识", skill_text)
                self.assertEqual(skill_manifest["schema_version"], "envoy.skill.v1")
                self.assertEqual(skill_manifest["title"], "skill export")
                self.assertEqual(skill_manifest["materials"][0]["content"], "第一段核心知识。第二段可训练内容。")
                self.assertEqual(rag_rows[0]["text"], "第一段核心知识。第二段可训练内容。")
                self.assertEqual(rag_rows[0]["metadata"]["source"], "text_asset")
                self.assertEqual(rag_rows[0]["metadata"]["semantic_unit_id"], "semantic-001")
                self.assertEqual(rag_rows[0]["metadata"]["project_id"], project_id)
                self.assertEqual(rag_rows[0]["metadata"]["asset_id"], "asset-001")
                self.assertEqual(rag_rows[0]["metadata"]["title"], "核心知识片段")
                self.assertEqual(rag_rows[0]["metadata"]["unit_type"], "text")
                self.assertEqual(rag_rows[0]["metadata"]["confidence"], 0.91)
                self.assertEqual(rag_manifest["schema_version"], "envoy.rag.v1")
                self.assertEqual(rag_manifest["source_task_id"], results["rag"]["task_id"])
                self.assertEqual(rag_manifest["project_id"], project_id)
                self.assertEqual(rag_manifest["row_count"], 1)
                self.assertEqual(Path(rag_manifest["files"]["corpus"]).name, "rag_corpus.jsonl")
                self.assertEqual(Path(rag_manifest["files"]["manifest"]).name, "rag_manifest.json")

                artifacts = client.get("/api/v1/artifacts").json()
                artifact_types = {item["artifact_type"] for item in artifacts}
                self.assertIn("markdown", artifact_types)
                self.assertIn("skill", artifact_types)
                self.assertIn("rag", artifact_types)
                skill_artifacts = [item for item in artifacts if item["artifact_type"] == "skill"]
                self.assertEqual({Path(item["file_path"]).name for item in skill_artifacts}, {"skill.md", "skill.json"})
                rag_artifacts = [item for item in artifacts if item["artifact_type"] == "rag"]
                self.assertEqual({Path(item["file_path"]).name for item in rag_artifacts}, {"rag_corpus.jsonl", "rag_manifest.json"})
                markdown_artifacts = [item for item in artifacts if item["artifact_type"] == "markdown"]
                self.assertEqual({Path(item["file_path"]).name for item in markdown_artifacts}, {"video_summary.md", "markdown_manifest.json"})

                markdown_artifact = next(item for item in markdown_artifacts if Path(item["file_path"]).name == "video_summary.md")
                download = client.get(f"/api/v1/artifacts/{markdown_artifact['id']}/download")
                self.assertEqual(download.status_code, 200)
                self.assertIn("第一段核心知识", download.text)
            finally:
                engine.dispose()

    def test_auto_rag_export_uses_ollama_metadata_when_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-rag-ollama-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="RAG Ollama project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-rag-ollama",
                        project_id=project.id,
                        asset_id="asset-rag-ollama",
                        segment_id=None,
                        unit_type="text",
                        title="Incident escalation",
                        content="Escalate incidents within 15 minutes and attach evidence.",
                        labels={"source": "text_asset"},
                        confidence=0.92,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.services.train_export_service.generate_with_ollama") as generate:
                    generate.return_value = {
                        "enabled": True,
                        "status": "success",
                        "text": '{"summary":"Incident response timing and evidence requirements.","keywords":["incident","15 minutes","evidence"],"usage":"RAG retrieval for response playbooks"}',
                        "error": None,
                    }
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "rag"})

                self.assertEqual(response.status_code, 200)
                rows = [
                    json.loads(line)
                    for line in Path(response.json()["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                metadata = rows[0]["metadata"]

                self.assertEqual(metadata["ollama_status"], "success")
                self.assertEqual(metadata["llm_summary"], "Incident response timing and evidence requirements.")
                self.assertEqual(metadata["llm_keywords"], ["incident", "15 minutes", "evidence"])
                self.assertEqual(metadata["llm_usage"], "RAG retrieval for response playbooks")
                generate.assert_called_once()
            finally:
                engine.dispose()

    def test_auto_rag_export_keeps_stable_output_when_ollama_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-rag-ollama-failure-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="RAG Ollama failure project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-rag-fallback",
                        project_id=project.id,
                        asset_id="asset-rag-fallback",
                        segment_id=None,
                        unit_type="text",
                        title="Fallback policy",
                        content="Fallback RAG content must remain exportable.",
                        labels={"source": "text_asset"},
                        confidence=0.88,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.services.train_export_service.generate_with_ollama") as generate:
                    generate.return_value = {"enabled": True, "status": "failed", "text": "", "error": "ollama timeout"}
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "rag"})

                self.assertEqual(response.status_code, 200)
                rows = [
                    json.loads(line)
                    for line in Path(response.json()["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                metadata = rows[0]["metadata"]

                self.assertEqual(rows[0]["text"], "Fallback RAG content must remain exportable.")
                self.assertEqual(metadata["ollama_status"], "failed")
                self.assertEqual(metadata["ollama_error"], "ollama timeout")
                self.assertNotIn("llm_summary", metadata)
            finally:
                engine.dispose()

    def test_auto_markdown_export_uses_ollama_summary_when_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-markdown-ollama-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="Markdown Ollama project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-md-ollama",
                        project_id=project.id,
                        asset_id="asset-md-ollama",
                        segment_id=None,
                        unit_type="text",
                        title="Release process",
                        content="Release notes must include risk, rollback, and owner.",
                        labels={"source": "text_asset"},
                        confidence=0.9,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.services.train_export_service.generate_with_ollama") as generate:
                    generate.return_value = {
                        "enabled": True,
                        "status": "success",
                        "text": '{"summary":"Release notes require risk, rollback, and owner fields."}',
                        "error": None,
                    }
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "markdown", "title": "Release report"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                markdown_text = Path(body["path"]).read_text(encoding="utf-8")
                manifest = json.loads(Path(body["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertIn("## AI 摘要", markdown_text)
                self.assertIn("Release notes require risk, rollback, and owner fields.", markdown_text)
                self.assertEqual(manifest["ollama_status"], "success")
                self.assertEqual(manifest["llm_summary"], "Release notes require risk, rollback, and owner fields.")
                generate.assert_called_once()
            finally:
                engine.dispose()

    def test_auto_markdown_export_keeps_report_when_ollama_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-markdown-ollama-failure-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from unittest.mock import patch
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="Markdown Ollama fallback project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-md-fallback",
                        project_id=project.id,
                        asset_id="asset-md-fallback",
                        segment_id=None,
                        unit_type="text",
                        title="Fallback report",
                        content="Markdown content must remain exportable.",
                        labels={"source": "text_asset"},
                        confidence=0.89,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.services.train_export_service.generate_with_ollama") as generate:
                    generate.return_value = {"enabled": True, "status": "failed", "text": "", "error": "ollama offline"}
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "markdown", "title": "Fallback report"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                markdown_text = Path(body["path"]).read_text(encoding="utf-8")
                manifest = json.loads(Path(body["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertIn("Markdown content must remain exportable.", markdown_text)
                self.assertNotIn("## AI 摘要", markdown_text)
                self.assertEqual(manifest["ollama_status"], "failed")
                self.assertEqual(manifest["ollama_error"], "ollama offline")
                self.assertNotIn("llm_summary", manifest)
            finally:
                engine.dispose()

    def test_train_export_requires_project_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-context-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

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

                auto_response = client.post("/api/v1/train-exports/auto", json={"export_type": "rag"})
                manual_response = client.post(
                    "/api/v1/train-exports",
                    json={"export_type": "rag", "payload": [{"text": "orphan content", "metadata": {}}]},
                )

                self.assertEqual(auto_response.status_code, 400)
                self.assertEqual(manual_response.status_code, 400)
                self.assertEqual(auto_response.json()["detail"], "project_id or source context required")
                self.assertEqual(manual_response.json()["detail"], "project_id or source context required")

                history_response = client.get("/api/v1/train-exports/history")
                self.assertEqual(history_response.status_code, 200)
                self.assertEqual(history_response.json(), [])
            finally:
                engine.dispose()

    def test_auto_export_rejects_empty_project_without_creating_empty_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-empty-project-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["OLLAMA_MODEL"] = ""

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from app.db.base import Base
            from app.db.session import SessionLocal, engine
            import app.models  # noqa: F401
            from app.main import app
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="空项目", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports/auto",
                    json={"project_id": project_id, "export_type": "rag"},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "No exportable materials found for project")
                self.assertEqual(client.get("/api/v1/train-exports/history").json(), [])
                self.assertEqual(client.get("/api/v1/artifacts").json(), [])
                export_files = [path for path in (Path(tmp_dir) / "exports").rglob("*") if path.is_file()]
                self.assertEqual(export_files, [])
            finally:
                engine.dispose()

    def test_auto_export_does_not_include_trace_links_from_other_projects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-isolation-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
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
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="目标项目", description="", domain_type="knowledge", status="active"))
                other_project = create_project(db, ProjectCreate(name="其他项目", description="", domain_type="knowledge", status="active"))
                target_unit = SemanticUnit(
                    id="target-unit",
                    project_id=project.id,
                    asset_id="target-asset",
                    segment_id=None,
                    unit_type="text",
                    title="目标知识",
                    content="目标项目内容",
                    labels={"source": "target"},
                    confidence=0.9,
                )
                other_unit = SemanticUnit(
                    id="other-unit",
                    project_id=other_project.id,
                    asset_id="other-asset",
                    segment_id=None,
                    unit_type="text",
                    title="其他知识",
                    content="其他项目内容",
                    labels={"source": "other"},
                    confidence=0.9,
                )
                sample = GeneratedSample(
                    id="target-sample",
                    project_id=project.id,
                    source_unit_id=target_unit.id,
                    sample_type="qa",
                    input_text="目标问题",
                    output_text="目标答案",
                    quality_score=0.9,
                )
                target_trace = TraceLink(
                    id="target-trace",
                    source_type="semantic_unit",
                    source_id=target_unit.id,
                    target_type="generated_sample",
                    target_id=sample.id,
                    relation_type="derived_from",
                )
                unrelated_trace = TraceLink(
                    id="other-trace",
                    source_type="semantic_unit",
                    source_id=other_unit.id,
                    target_type="artifact",
                    target_id="other-artifact",
                    relation_type="exported_to",
                )
                db.add_all([target_unit, other_unit, sample, target_trace, unrelated_trace])
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports/auto",
                    json={"project_id": project_id, "export_type": "skill", "title": "隔离导出"},
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                skill_text = Path(body["path"]).read_text(encoding="utf-8")
                skill_manifest_text = Path(body["paths"]["manifest"]).read_text(encoding="utf-8")
                self.assertIn("target-trace", skill_text)
                self.assertIn("target-unit", skill_text)
                self.assertNotIn("other-trace", skill_text)
                self.assertNotIn("other-unit", skill_text)
                self.assertNotIn("其他项目内容", skill_text)
                self.assertIn("target-trace", skill_manifest_text)
                self.assertNotIn("other-trace", skill_manifest_text)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
