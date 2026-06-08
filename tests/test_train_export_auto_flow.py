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
                skill_text = Path(results["skill"]["path"]).read_text(encoding="utf-8")
                rag_rows = [
                    json.loads(line)
                    for line in Path(results["rag"]["path"]).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

                self.assertIn("## 视频转化摘要", markdown_text)
                self.assertIn("第一段核心知识", markdown_text)
                self.assertIn("## 输入摘要", skill_text)
                self.assertIn("样本 1", skill_text)
                self.assertIn("第一段核心知识", skill_text)
                self.assertEqual(rag_rows[0]["text"], "第一段核心知识。第二段可训练内容。")
                self.assertEqual(rag_rows[0]["metadata"]["source"], "text_asset")

                artifacts = client.get("/api/v1/artifacts").json()
                artifact_types = {item["artifact_type"] for item in artifacts}
                self.assertIn("markdown", artifact_types)
                self.assertIn("skill", artifact_types)
                self.assertIn("rag", artifact_types)

                markdown_artifact = next(item for item in artifacts if item["artifact_type"] == "markdown")
                download = client.get(f"/api/v1/artifacts/{markdown_artifact['id']}/download")
                self.assertEqual(download.status_code, 200)
                self.assertIn("第一段核心知识", download.text)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
