import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiSemanticUnitsFlowTests(unittest.TestCase):
    def test_semantic_units_list_and_detail_include_traceable_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-semantic-units-api-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")

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
                project = create_project(db, ProjectCreate(name="semantic api project", description="", domain_type="knowledge", status="active"))
                unit = SemanticUnit(
                    id="semantic-api-001",
                    project_id=project.id,
                    asset_id="asset-api-001",
                    segment_id="segment-api-001",
                    unit_type="text",
                    title="可追溯片段",
                    content="用于验证语义单元 API 字段完整性。",
                    labels={"stage": "preprocess", "chunk_index": 0},
                    confidence=0.93,
                )
                db.add(unit)
                db.commit()
            finally:
                db.close()

            try:
                client = TestClient(app)

                list_response = client.get("/api/v1/semantic-units")
                detail_response = client.get("/api/v1/semantic-units/semantic-api-001")

                self.assertEqual(list_response.status_code, 200)
                self.assertEqual(detail_response.status_code, 200)
                listed = list_response.json()[0]
                detail = detail_response.json()

                for payload in (listed, detail):
                    self.assertEqual(payload["id"], "semantic-api-001")
                    self.assertEqual(payload["asset_id"], "asset-api-001")
                    self.assertEqual(payload["segment_id"], "segment-api-001")
                    self.assertEqual(payload["labels"]["chunk_index"], 0)
                    self.assertEqual(payload["confidence"], 0.93)
                    self.assertIsNotNone(payload["created_at"])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
