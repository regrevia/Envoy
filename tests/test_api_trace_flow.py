import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiTraceFlowTests(unittest.TestCase):
    def test_trace_query_for_artifact_includes_reverse_source_links(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-trace-api-test.db'}"
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
            from app.models.trace_link import TraceLink

            Base.metadata.create_all(bind=engine)
            db = SessionLocal()
            try:
                db.add_all(
                    [
                        TraceLink(
                            id="trace-asset-unit",
                            source_type="asset",
                            source_id="asset-trace-001",
                            target_type="semantic_unit",
                            target_id="semantic-trace-001",
                            relation_type="derived_from",
                        ),
                        TraceLink(
                            id="trace-unit-sample",
                            source_type="semantic_unit",
                            source_id="semantic-trace-001",
                            target_type="generated_sample",
                            target_id="sample-trace-001",
                            relation_type="derived_from",
                        ),
                        TraceLink(
                            id="trace-sample-artifact",
                            source_type="generated_sample",
                            source_id="sample-trace-001",
                            target_type="artifact",
                            target_id="artifact-trace-001",
                            relation_type="exported_to",
                        ),
                    ]
                )
                db.commit()
            finally:
                db.close()

            try:
                client = TestClient(app)
                response = client.get("/api/v1/trace/artifact/artifact-trace-001")

                self.assertEqual(response.status_code, 200)
                links = response.json()
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]["direction"], "incoming")
                self.assertEqual(links[0]["source_type"], "generated_sample")
                self.assertEqual(links[0]["source_id"], "sample-trace-001")
                self.assertEqual(links[0]["target_type"], "artifact")
                self.assertEqual(links[0]["target_id"], "artifact-trace-001")
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
