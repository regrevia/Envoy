import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiGeneratedSamplesFlowTests(unittest.TestCase):
    def test_generated_samples_list_detail_and_missing_resource(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-generated-samples-api-test.db'}"
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
            from app.models.generated_sample import GeneratedSample
            from app.schemas.project import ProjectCreate
            from app.services.crud_service import create_project

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                project = create_project(db, ProjectCreate(name="sample review project", description="", domain_type="knowledge", status="active"))
                db.add(
                    GeneratedSample(
                        id="sample-api-001",
                        project_id=project.id,
                        source_unit_id="semantic-api-001",
                        sample_type="qa",
                        input_text="What should reviewers inspect?",
                        output_text="They should inspect generated samples before export.",
                        quality_score=0.91,
                    )
                )
                db.commit()
            finally:
                db.close()

            try:
                client = TestClient(app)

                list_response = client.get("/api/v1/generated-samples")
                self.assertEqual(list_response.status_code, 200)
                samples = list_response.json()
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0]["id"], "sample-api-001")
                self.assertEqual(samples[0]["input_text"], "What should reviewers inspect?")
                self.assertEqual(samples[0]["output_text"], "They should inspect generated samples before export.")
                self.assertEqual(samples[0]["quality_score"], 0.91)

                detail_response = client.get("/api/v1/generated-samples/sample-api-001")
                self.assertEqual(detail_response.status_code, 200)
                detail = detail_response.json()
                self.assertEqual(detail["id"], "sample-api-001")
                self.assertEqual(detail["source_unit_id"], "semantic-api-001")
                self.assertEqual(detail["sample_type"], "qa")

                missing_response = client.get("/api/v1/generated-samples/missing-sample")
                self.assertEqual(missing_response.status_code, 404)
                self.assertEqual(missing_response.json()["detail"], "Generated sample not found")
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
