import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class CoreMaterialFlowTests(unittest.TestCase):
    def test_long_text_upload_preprocess_and_auto_material_exports(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-core-material-flow-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(tmp_path / "exports")
            os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
            os.environ["TEXT_PREPROCESS_CHUNK_CHARS"] = "160"
            os.environ["TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS"] = "20"
            os.environ["OLLAMA_MODEL"] = ""
            os.environ["OLLAMA_RUNTIME_CONFIG_PATH"] = str(tmp_path / "ollama_runtime.json")

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
                project_response = client.post(
                    "/api/v1/projects",
                    json={
                        "name": "长文本到 AI 素材",
                        "description": "验证上传、预处理和自动导出闭环",
                        "domain_type": "knowledge",
                        "status": "active",
                    },
                )
                self.assertEqual(project_response.status_code, 200)
                project_id = project_response.json()["id"]

                source_text = "\n\n".join(
                    [
                        "第一章：Envoy 需要把长文本稳定切分成可追溯语义单元。" + "核心链路需要可恢复、可观察、可重试。" * 4,
                        "第二章：生成 Markdown 时要保留清晰标题、核心内容和素材来源。" + "每个片段都应该能回到原始素材。" * 4,
                        "第三章：Skill 产物应沉淀任务背景、执行步骤和可复用检查清单。" + "操作说明要能直接交给使用者执行。" * 4,
                        "第四章：RAG 语料要包含项目、素材、语义单元和置信度等元数据。" + "检索结果必须能够解释来源。" * 4,
                        "第五章：长任务不能卡死，失败后不能留下污染的中间状态。" + "重试前要清理本轮半成品。" * 4,
                    ]
                )
                upload_response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": project_id, "asset_type": "text", "language": "zh"},
                    files={"file": ("core-material.txt", source_text.encode("utf-8"), "text/plain")},
                )
                self.assertEqual(upload_response.status_code, 200)
                uploaded = upload_response.json()
                asset_id = uploaded["asset"]["id"]
                self.assertEqual(uploaded["asset"]["project_id"], project_id)
                self.assertEqual(uploaded["asset"]["asset_type"], "text")
                self.assertTrue(Path(uploaded["storage"]["file_path"]).exists())

                preprocess_response = client.post(
                    "/api/v1/tasks/preprocess",
                    json={
                        "project_id": project_id,
                        "asset_id": asset_id,
                        "task_type": "preprocess",
                        "input_config": {"requested_outputs": ["markdown", "skill", "rag"]},
                    },
                )
                self.assertEqual(preprocess_response.status_code, 200)
                preprocess_task_id = preprocess_response.json()["id"]

                task = None
                for _ in range(80):
                    task_response = client.get(f"/api/v1/tasks/{preprocess_task_id}")
                    self.assertEqual(task_response.status_code, 200)
                    task = task_response.json()
                    if task["status"] in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertIsNotNone(task)
                self.assertEqual(task["status"], "success", task.get("error_message"))
                self.assertEqual(task["progress"], 100)
                self.assertIn("text preprocess done", task["result_summary"])
                self.assertEqual(task["input_config"]["requested_outputs"], ["markdown", "skill", "rag"])

                semantic_response = client.get(f"/api/v1/semantic-units?project_id={project_id}")
                self.assertEqual(semantic_response.status_code, 200)
                semantic_units = semantic_response.json()
                self.assertGreaterEqual(len(semantic_units), 2)
                self.assertTrue(all(unit["project_id"] == project_id for unit in semantic_units))
                self.assertTrue(all(unit["asset_id"] == asset_id for unit in semantic_units))
                self.assertTrue(all(unit["labels"]["stage"] == "preprocess" for unit in semantic_units))
                self.assertTrue(any("Envoy 需要把长文本" in unit["content"] for unit in semantic_units))

                results = {}
                for export_type in ("markdown", "skill", "rag"):
                    export_response = client.post(
                        "/api/v1/train-exports/auto",
                        json={"project_id": project_id, "export_type": export_type, "title": f"{export_type} core material"},
                    )
                    self.assertEqual(export_response.status_code, 200, export_response.text)
                    body = export_response.json()
                    self.assertNotIn("error", body)
                    self.assertTrue(Path(body["path"]).exists())
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
                self.assertIn("Envoy 需要把长文本", markdown_text)
                self.assertIn("来源：文本素材", markdown_text)
                self.assertEqual(markdown_manifest["project_id"], project_id)
                self.assertEqual(markdown_manifest["row_count"], len(semantic_units))
                self.assertEqual(markdown_manifest["source_counts"], {"text_asset": len(semantic_units)})

                self.assertIn("## 输入摘要", skill_text)
                self.assertIn("## 执行步骤", skill_text)
                self.assertIn("Envoy 需要把长文本", skill_text)
                self.assertEqual(skill_manifest["project_id"], project_id)
                self.assertEqual(skill_manifest["source_counts"], {"text_asset": len(semantic_units)})
                self.assertEqual(len(skill_manifest["materials"]), len(semantic_units))

                self.assertEqual(len(rag_rows), len(semantic_units))
                self.assertEqual(rag_manifest["project_id"], project_id)
                self.assertEqual(rag_manifest["row_count"], len(semantic_units))
                self.assertEqual(rag_manifest["source_counts"], {"text_asset": len(semantic_units)})
                self.assertTrue(all(row["metadata"]["project_id"] == project_id for row in rag_rows))
                self.assertTrue(all(row["metadata"]["asset_id"] == asset_id for row in rag_rows))
                self.assertTrue(any("RAG 语料要包含项目" in row["text"] for row in rag_rows))

                history_response = client.get("/api/v1/train-exports/history")
                self.assertEqual(history_response.status_code, 200)
                history = history_response.json()
                self.assertEqual({row["export_type"] for row in history}, {"markdown", "skill", "rag"})
                self.assertTrue(all(row["project_id"] == project_id for row in history))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
