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
            from app.models.trace_link import TraceLink
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
                db.add(
                    SemanticUnit(
                        id="semantic-002",
                        project_id=project.id,
                        asset_id="asset-002",
                        segment_id=None,
                        unit_type="transcript",
                        title="视频转写片段",
                        content="视频中讲解了长任务处理和稳定导出。",
                        labels={
                            "source": "media_transcript",
                            "stage": "preprocess",
                            "segment_seconds": 300,
                            "max_segment_count": 2,
                            "max_input_seconds": 600,
                        },
                        confidence=0.88,
                    )
                )
                db.add(
                    SemanticUnit(
                        id="semantic-003",
                        project_id=project.id,
                        asset_id="asset-002",
                        segment_id=None,
                        unit_type="ocr",
                        title="视频画面 OCR",
                        content="画面文字：核心链路、RAG、Skill。",
                        labels={
                            "source": "media_ocr",
                            "stage": "preprocess",
                            "segment_seconds": 300,
                            "max_segment_count": 2,
                            "max_input_seconds": 600,
                        },
                        confidence=0.82,
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
                self.assertIn("## 素材概览", markdown_text)
                self.assertIn("## 核心内容", markdown_text)
                self.assertIn("来源：文本素材", markdown_text)
                self.assertIn("来源：视频转写", markdown_text)
                self.assertIn("来源：视频 OCR", markdown_text)
                self.assertIn("第一段核心知识", markdown_text)
                self.assertNotIn('"content":', markdown_text)
                self.assertEqual(markdown_manifest["schema_version"], "envoy.markdown.v1")
                self.assertEqual(markdown_manifest["source_task_id"], results["markdown"]["task_id"])
                self.assertEqual(markdown_manifest["project_id"], project_id)
                self.assertEqual(markdown_manifest["row_count"], 3)
                self.assertEqual(markdown_manifest["source_counts"], {"text_asset": 1, "media_transcript": 1, "media_ocr": 1})
                self.assertEqual(markdown_manifest["unit_type_counts"], {"text": 1, "transcript": 1, "ocr": 1})
                self.assertEqual(markdown_manifest["media_processing_limits"], [{"segment_seconds": 300, "max_segment_count": 2, "max_input_seconds": 600, "unit_count": 2}])
                self.assertEqual(Path(markdown_manifest["files"]["markdown"]).name, "video_summary.md")
                self.assertEqual(Path(markdown_manifest["files"]["manifest"]).name, "markdown_manifest.json")
                self.assertIn("## 输入摘要", skill_text)
                self.assertIn("## 能力用途", skill_text)
                self.assertIn("## 输入素材", skill_text)
                self.assertIn("## 执行步骤", skill_text)
                self.assertIn("样本 1", skill_text)
                self.assertIn("来源：视频转写", skill_text)
                self.assertIn("来源：视频 OCR", skill_text)
                self.assertIn("第一段核心知识", skill_text)
                self.assertNotIn('"content":', skill_text)
                self.assertEqual(skill_manifest["schema_version"], "envoy.skill.v1")
                self.assertEqual(skill_manifest["title"], "skill export")
                self.assertEqual(skill_manifest["source_counts"], {"text_asset": 1, "media_transcript": 1, "media_ocr": 1})
                self.assertEqual(skill_manifest["unit_type_counts"], {"text": 1, "transcript": 1, "ocr": 1})
                self.assertEqual(skill_manifest["media_processing_limits"], [{"segment_seconds": 300, "max_segment_count": 2, "max_input_seconds": 600, "unit_count": 2}])
                self.assertTrue(any(item["content"] == "第一段核心知识。第二段可训练内容。" for item in skill_manifest["materials"]))
                rag_by_id = {row["metadata"]["semantic_unit_id"]: row for row in rag_rows}
                self.assertEqual(rag_by_id["semantic-001"]["text"], "第一段核心知识。第二段可训练内容。")
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["source"], "text_asset")
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["project_id"], project_id)
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["asset_id"], "asset-001")
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["title"], "核心知识片段")
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["unit_type"], "text")
                self.assertEqual(rag_by_id["semantic-001"]["metadata"]["confidence"], 0.91)
                self.assertEqual(rag_manifest["schema_version"], "envoy.rag.v1")
                self.assertEqual(rag_manifest["source_task_id"], results["rag"]["task_id"])
                self.assertEqual(rag_manifest["project_id"], project_id)
                self.assertEqual(rag_manifest["row_count"], 3)
                self.assertEqual(rag_manifest["source_counts"], {"text_asset": 1, "media_transcript": 1, "media_ocr": 1})
                self.assertEqual(rag_manifest["unit_type_counts"], {"text": 1, "transcript": 1, "ocr": 1})
                self.assertEqual(rag_manifest["media_processing_limits"], [{"segment_seconds": 300, "max_segment_count": 2, "max_input_seconds": 600, "unit_count": 2}])
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

                db = SessionLocal()
                try:
                    exported_links = (
                        db.query(TraceLink)
                        .filter(
                            TraceLink.source_type == "semantic_unit",
                            TraceLink.source_id.in_(["semantic-001", "semantic-002", "semantic-003"]),
                            TraceLink.target_type == "artifact",
                            TraceLink.relation_type == "exported_to",
                        )
                        .all()
                    )
                    traced_artifact_ids = {link.target_id for link in exported_links}
                finally:
                    db.close()

                for artifact in markdown_artifacts + skill_artifacts + rag_artifacts:
                    self.assertIn(artifact["id"], traced_artifact_ids)

                trace_response = client.get(f"/api/v1/trace/artifact/{markdown_artifact['id']}")
                self.assertEqual(trace_response.status_code, 200)
                trace_links = trace_response.json()
                self.assertEqual(
                    {link["source_id"] for link in trace_links if link["target_id"] == markdown_artifact["id"]},
                    {"semantic-001", "semantic-002", "semantic-003"},
                )
            finally:
                engine.dispose()

    def test_auto_exports_render_structured_semantic_json_as_readable_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-structured-json-test.db'}"
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
                project = create_project(db, ProjectCreate(name="结构化语义导出", description="", domain_type="knowledge", status="active"))
                db.add_all(
                    [
                        SemanticUnit(
                            id="structured-media-summary",
                            project_id=project.id,
                            asset_id="asset-video-structured",
                            segment_id=None,
                            unit_type="scene",
                            title="媒体预处理摘要",
                            content=json.dumps(
                                {
                                    "stage": "preprocess",
                                    "transcript": {"text": "讲解了稳定处理长视频。"},
                                    "ocr": [{"text": "画面文字：RAG Skill"}],
                                    "max_input_seconds": 600,
                                },
                                ensure_ascii=False,
                            ),
                            labels={"source": "media_asset", "stage": "preprocess"},
                            confidence=0.8,
                        ),
                        SemanticUnit(
                            id="structured-analysis",
                            project_id=project.id,
                            asset_id="asset-video-structured",
                            segment_id=None,
                            unit_type="rule",
                            title="分析结果",
                            content=json.dumps(
                                {
                                    "scenes": [{"summary": "用户上传长视频"}],
                                    "rules": ["长任务必须可取消"],
                                    "concepts": ["RAG", "Skill"],
                                },
                                ensure_ascii=False,
                            ),
                            labels={"stage": "analyze", "kind": "rule"},
                            confidence=0.76,
                        ),
                    ]
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
                        json={"project_id": project_id, "export_type": export_type, "title": f"{export_type} structured export"},
                    )
                    self.assertEqual(response.status_code, 200)
                    results[export_type] = response.json()

                markdown_text = Path(results["markdown"]["path"]).read_text(encoding="utf-8")
                skill_text = Path(results["skill"]["path"]).read_text(encoding="utf-8")
                rag_rows = [
                    json.loads(line)
                    for line in Path(results["rag"]["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                rag_text = "\n".join(row["text"] for row in rag_rows)
                skill_manifest = json.loads(Path(results["skill"]["paths"]["manifest"]).read_text(encoding="utf-8"))

                for rendered_text in (markdown_text, skill_text, rag_text):
                    self.assertIn("讲解了稳定处理长视频。", rendered_text)
                    self.assertIn("画面文字：RAG Skill", rendered_text)
                    self.assertIn("用户上传长视频", rendered_text)
                    self.assertIn("长任务必须可取消", rendered_text)
                    self.assertIn("RAG", rendered_text)
                    self.assertIn("Skill", rendered_text)
                    self.assertNotIn('"transcript"', rendered_text)
                    self.assertNotIn('"scenes"', rendered_text)
                    self.assertNotIn('{"stage"', rendered_text)

                media_rag = next(row for row in rag_rows if row["metadata"]["semantic_unit_id"] == "structured-media-summary")
                self.assertEqual(media_rag["metadata"]["content_format"], "json")
                self.assertIn("讲解了稳定处理长视频。", media_rag["text"])
                self.assertIn("画面文字：RAG Skill", media_rag["text"])
                self.assertTrue(any(item["id"] == "structured-media-summary" for item in skill_manifest["materials"]))
            finally:
                engine.dispose()

    def test_auto_exports_ignore_semantic_units_from_canceled_preprocess_tasks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-canceled-preprocess-test.db'}"
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
            from app.models.processing_task import ProcessingTask
            from app.models.semantic_unit import SemanticUnit
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="取消任务导出过滤", description="", domain_type="knowledge", status="active"))
                db.add_all(
                    [
                        ProcessingTask(
                            id="task-success-preprocess",
                            project_id=project.id,
                            asset_id="asset-stable",
                            task_type="preprocess",
                            status="success",
                            progress=100,
                            input_config={},
                        ),
                        ProcessingTask(
                            id="task-canceled-preprocess",
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
                            id="stable-unit",
                            project_id=project.id,
                            asset_id="asset-stable",
                            segment_id=None,
                            unit_type="text",
                            title="稳定素材",
                            content="这是成功预处理后的正式内容。",
                            labels={"source": "document_text", "stage": "preprocess", "task_id": "task-success-preprocess"},
                            confidence=0.9,
                        ),
                        SemanticUnit(
                            id="canceled-partial-unit",
                            project_id=project.id,
                            asset_id="asset-canceled",
                            segment_id=None,
                            unit_type="document",
                            title="取消任务半成品",
                            content="这段取消任务产生的半成品不应进入任何自动导出。",
                            labels={"source": "document_text", "stage": "preprocess", "task_id": "task-canceled-preprocess"},
                            confidence=0.5,
                        ),
                        SemanticUnit(
                            id="canceled-checkpoint-unit",
                            project_id=project.id,
                            asset_id="asset-canceled",
                            segment_id=None,
                            unit_type="transcript",
                            title="取消任务 checkpoint",
                            content="取消中的分段 checkpoint 也不能进入导出。",
                            labels={
                                "source": "media_transcript",
                                "stage": "preprocess",
                                "task_id": "task-canceled-preprocess",
                                "checkpoint": "segment_transcript",
                            },
                            confidence=0.5,
                        ),
                    ]
                )
                db.add_all(
                    [
                        GeneratedSample(
                            id="stable-sample",
                            project_id=project.id,
                            source_unit_id="stable-unit",
                            sample_type="qa",
                            input_text="稳定样本输入",
                            output_text="稳定样本输出，可进入自动导出。",
                            quality_score=0.9,
                        ),
                        GeneratedSample(
                            id="canceled-partial-sample",
                            project_id=project.id,
                            source_unit_id="canceled-partial-unit",
                            sample_type="qa",
                            input_text="取消半成品样本输入",
                            output_text="取消半成品样本输出不应进入自动导出。",
                            quality_score=0.4,
                        ),
                        GeneratedSample(
                            id="canceled-checkpoint-sample",
                            project_id=project.id,
                            source_unit_id="canceled-checkpoint-unit",
                            sample_type="qa",
                            input_text="取消 checkpoint 样本输入",
                            output_text="取消 checkpoint 样本输出不应进入自动导出。",
                            quality_score=0.4,
                        ),
                    ]
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                results = {}
                for export_type in ("markdown", "skill", "rag", "sft"):
                    response = client.post(
                        "/api/v1/train-exports/auto",
                        json={"project_id": project_id, "export_type": export_type, "title": f"{export_type} clean export"},
                    )
                    self.assertEqual(response.status_code, 200)
                    results[export_type] = response.json()

                markdown_text = Path(results["markdown"]["path"]).read_text(encoding="utf-8")
                skill_text = Path(results["skill"]["path"]).read_text(encoding="utf-8")
                sft_text = "\n".join(
                    Path(path).read_text(encoding="utf-8")
                    for path in results["sft"]["paths"].values()
                    if Path(path).exists()
                )
                rag_rows = [
                    json.loads(line)
                    for line in Path(results["rag"]["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                rag_text = "\n".join(row["text"] for row in rag_rows)

                for exported_text in (markdown_text, skill_text, rag_text, sft_text):
                    self.assertIn("这是成功预处理后的正式内容。", exported_text)
                    self.assertNotIn("这段取消任务产生的半成品", exported_text)
                    self.assertNotIn("取消中的分段 checkpoint", exported_text)
                    self.assertNotIn("取消半成品样本", exported_text)
                    self.assertNotIn("取消 checkpoint 样本", exported_text)
                for exported_text in (markdown_text, skill_text, sft_text):
                    self.assertIn("稳定样本输出，可进入自动导出。", exported_text)
                self.assertEqual({row["metadata"]["semantic_unit_id"] for row in rag_rows}, {"stable-unit"})
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

    def test_auto_rag_export_skips_empty_semantic_unit_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-rag-skip-empty-test.db'}"
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
                project = create_project(db, ProjectCreate(name="skip empty rag project", description="", domain_type="knowledge", status="active"))
                db.add_all(
                    [
                        SemanticUnit(
                            id="empty-rag-unit",
                            project_id=project.id,
                            asset_id="asset-empty",
                            segment_id=None,
                            unit_type="text",
                            title="空内容",
                            content="   ",
                            labels={"source": "text_asset"},
                            confidence=0.2,
                        ),
                        SemanticUnit(
                            id="valid-rag-unit",
                            project_id=project.id,
                            asset_id="asset-valid",
                            segment_id=None,
                            unit_type="text",
                            title="有效知识",
                            content="有效知识内容，可进入 RAG 语料。",
                            labels={"source": "text_asset"},
                            confidence=0.9,
                        ),
                    ]
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "rag"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                rag_rows = [
                    json.loads(line)
                    for line in Path(body["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                manifest = json.loads(Path(body["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertEqual(len(rag_rows), 1)
                self.assertEqual(rag_rows[0]["text"], "有效知识内容，可进入 RAG 语料。")
                self.assertEqual(rag_rows[0]["metadata"]["semantic_unit_id"], "valid-rag-unit")
                self.assertEqual(manifest["row_count"], 1)
                self.assertEqual(manifest["source_counts"], {"text_asset": 1})
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
                        "truncated": True,
                        "original_prompt_chars": 24000,
                        "sent_prompt_chars": 12000,
                        "max_prompt_chars": 12000,
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
                self.assertTrue(manifest["ollama_truncated"])
                self.assertEqual(manifest["ollama_original_prompt_chars"], 24000)
                self.assertEqual(manifest["ollama_sent_prompt_chars"], 12000)
                self.assertEqual(manifest["ollama_max_prompt_chars"], 12000)
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

    def test_auto_skill_export_uses_ollama_quality_review_when_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-skill-ollama-test.db'}"
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
                project = create_project(db, ProjectCreate(name="Skill Ollama project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-skill-ollama",
                        project_id=project.id,
                        asset_id="asset-skill-ollama",
                        segment_id=None,
                        unit_type="rule",
                        title="Refund workflow",
                        content="Refund approvals require receipt review and manager approval.",
                        labels={"source": "text_asset"},
                        confidence=0.93,
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
                        "text": '{"summary":"Refund workflow skill is actionable.","usage":"Support agent refund approval guidance","risks":["Needs manager policy version"]}',
                        "error": None,
                    }
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "skill", "title": "Refund Skill"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                skill_text = Path(body["path"]).read_text(encoding="utf-8")
                manifest = json.loads(Path(body["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertIn("## AI 质检", skill_text)
                self.assertIn("Refund workflow skill is actionable.", skill_text)
                self.assertEqual(manifest["ollama_status"], "success")
                self.assertEqual(manifest["llm_summary"], "Refund workflow skill is actionable.")
                self.assertEqual(manifest["llm_usage"], "Support agent refund approval guidance")
                self.assertEqual(manifest["llm_risks"], ["Needs manager policy version"])
                generate.assert_called_once()
            finally:
                engine.dispose()

    def test_auto_skill_export_keeps_package_when_ollama_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-skill-ollama-failure-test.db'}"
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
                project = create_project(db, ProjectCreate(name="Skill Ollama fallback project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-skill-fallback",
                        project_id=project.id,
                        asset_id="asset-skill-fallback",
                        segment_id=None,
                        unit_type="rule",
                        title="Stable skill",
                        content="Skill package must remain exportable.",
                        labels={"source": "text_asset"},
                        confidence=0.87,
                    )
                )
                db.commit()
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                with patch("app.services.train_export_service.generate_with_ollama") as generate:
                    generate.return_value = {"enabled": True, "status": "failed", "text": "", "error": "ollama overloaded"}
                    response = client.post("/api/v1/train-exports/auto", json={"project_id": project_id, "export_type": "skill", "title": "Stable Skill"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                skill_text = Path(body["path"]).read_text(encoding="utf-8")
                manifest = json.loads(Path(body["paths"]["manifest"]).read_text(encoding="utf-8"))

                self.assertIn("Skill package must remain exportable.", skill_text)
                self.assertNotIn("## AI 质检", skill_text)
                self.assertEqual(manifest["ollama_status"], "failed")
                self.assertEqual(manifest["ollama_error"], "ollama overloaded")
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

    def test_train_export_rejects_unsupported_export_type_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-unsupported-type-test.db'}"
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
                project = create_project(db, ProjectCreate(name="unsupported export project", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports",
                    json={"project_id": project_id, "export_type": "unknown", "payload": [{"text": "unused"}]},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "Unsupported export type")
                self.assertEqual(client.get("/api/v1/train-exports/history").json(), [])
                self.assertEqual(client.get("/api/v1/artifacts").json(), [])
                export_files = [path for path in (Path(tmp_dir) / "exports").rglob("*") if path.is_file()]
                self.assertEqual(export_files, [])
            finally:
                engine.dispose()

    def test_manual_export_rejects_empty_payload_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-empty-payload-test.db'}"
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
                project = create_project(db, ProjectCreate(name="empty payload project", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports",
                    json={"project_id": project_id, "export_type": "rag", "payload": []},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "No exportable materials provided")
                self.assertEqual(client.get("/api/v1/train-exports/history").json(), [])
                self.assertEqual(client.get("/api/v1/artifacts").json(), [])
                export_files = [path for path in (Path(tmp_dir) / "exports").rglob("*") if path.is_file()]
                self.assertEqual(export_files, [])
            finally:
                engine.dispose()

    def test_manual_export_rejects_payload_without_usable_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-empty-content-test.db'}"
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
            from app.models.artifact import Artifact
            from app.models.export_record import ExportRecord
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="empty content project", description="", domain_type="knowledge", status="active"))
                project_id = project.id
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/train-exports",
                    json={
                        "project_id": project_id,
                        "export_type": "rag",
                        "payload": [
                            {},
                            {"text": "   ", "metadata": {"source": "blank"}},
                            {"content": "", "labels": {"source": "blank"}},
                            {"input": "", "output": ""},
                        ],
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "No usable export content provided")
                db = SessionLocal()
                try:
                    self.assertEqual(db.query(Artifact).filter(Artifact.project_id == project_id).count(), 0)
                    self.assertEqual(db.query(ExportRecord).filter(ExportRecord.project_id == project_id).count(), 0)
                finally:
                    db.close()
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

    def test_manual_export_ignores_untrusted_semantic_unit_trace_sources(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-export-trace-pollution-test.db'}"
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
            from app.models.trace_link import TraceLink
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="trusted project", description="", domain_type="knowledge", status="active"))
                other_project = create_project(db, ProjectCreate(name="other project", description="", domain_type="knowledge", status="active"))
                db.add(
                    SemanticUnit(
                        id="semantic-other-project",
                        project_id=other_project.id,
                        asset_id="asset-other",
                        segment_id=None,
                        unit_type="text",
                        title="Other project unit",
                        content="This unit must not be linked.",
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
                response = client.post(
                    "/api/v1/train-exports",
                    json={
                        "project_id": project_id,
                        "export_type": "rag",
                        "payload": [
                            {"text": "manual row", "metadata": {"semantic_unit_id": "semantic-other-project"}},
                            {"text": "manual row 2", "metadata": {"semantic_unit_id": "semantic-missing"}},
                        ],
                    },
                )

                self.assertEqual(response.status_code, 200)

                db = SessionLocal()
                try:
                    links = db.query(TraceLink).filter(TraceLink.target_type == "artifact").all()
                    self.assertEqual(links, [])
                finally:
                    db.close()
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

                artifacts = client.get("/api/v1/artifacts").json()
                skill_artifact = next(item for item in artifacts if item["artifact_type"] == "skill" and Path(item["file_path"]).name == "skill.md")
                trace_response = client.get(f"/api/v1/trace/artifact/{skill_artifact['id']}")
                self.assertEqual(trace_response.status_code, 200)
                trace_links = trace_response.json()
                self.assertTrue(
                    any(
                        link["source_type"] == "generated_sample"
                        and link["source_id"] == "target-sample"
                        and link["target_type"] == "artifact"
                        and link["target_id"] == skill_artifact["id"]
                        for link in trace_links
                    )
                )
                self.assertFalse(any(link["source_id"] == "other-unit" for link in trace_links))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
