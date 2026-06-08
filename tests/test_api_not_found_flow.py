import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiNotFoundFlowTests(unittest.TestCase):
    def test_detail_endpoints_return_404_for_missing_resources(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-api-404-test.db'}"
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")

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

                expectations = [
                    ("/api/v1/projects/missing-project", "Project not found"),
                    ("/api/v1/artifacts/missing-artifact", "Artifact not found"),
                    ("/api/v1/semantic-units/missing-unit", "Semantic unit not found"),
                    ("/api/v1/generated-samples/missing-sample", "Generated sample not found"),
                ]

                for path, detail in expectations:
                    with self.subTest(path=path):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.json()["detail"], detail)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
