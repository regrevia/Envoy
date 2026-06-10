import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class ApiAssetFlowTests(unittest.TestCase):
    def test_asset_upload_rejects_missing_project_without_creating_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-missing-project-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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

                response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": "missing-project", "asset_type": "text", "language": "zh"},
                    files={"file": ("source.txt", b"orphan content", "text/plain")},
                )

                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"], "Project not found")

                assets_response = client.get("/api/v1/assets")
                self.assertEqual(assets_response.status_code, 200)
                self.assertEqual(assets_response.json(), [])
                storage_dir = Path(tmp_dir) / "storage"
                stored_files = [path for path in storage_dir.rglob("*") if path.is_file()] if storage_dir.exists() else []
                self.assertEqual(stored_files, [])
            finally:
                engine.dispose()

    def test_asset_upload_rejects_empty_file_without_creating_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-empty-upload-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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
                    json={"name": "空文件上传项目", "description": "", "domain_type": "knowledge", "status": "active"},
                )
                project_id = project_response.json()["id"]

                response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": project_id, "asset_type": "text", "language": "zh"},
                    files={"file": ("empty.txt", b"", "text/plain")},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "Uploaded file is empty")
                self.assertEqual(client.get("/api/v1/assets").json(), [])
                storage_dir = Path(tmp_dir) / "storage"
                stored_files = [path for path in storage_dir.rglob("*") if path.is_file()] if storage_dir.exists() else []
                self.assertEqual(stored_files, [])
            finally:
                engine.dispose()

    def test_asset_upload_rejects_unknown_asset_type_without_creating_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-unknown-asset-type-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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
                    json={"name": "未知素材类型项目", "description": "", "domain_type": "knowledge", "status": "active"},
                )
                project_id = project_response.json()["id"]

                response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": project_id, "asset_type": "unknown", "language": "zh"},
                    files={"file": ("source.bin", b"raw bytes", "application/octet-stream")},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "Unsupported asset type")
                self.assertEqual(client.get("/api/v1/assets").json(), [])
                storage_dir = Path(tmp_dir) / "storage"
                stored_files = [path for path in storage_dir.rglob("*") if path.is_file()] if storage_dir.exists() else []
                self.assertEqual(stored_files, [])
            finally:
                engine.dispose()

    def test_project_and_text_asset_upload_flow(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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
                    json={"name": "真实链路项目", "description": "集成测试", "domain_type": "knowledge", "status": "active"},
                )
                self.assertEqual(project_response.status_code, 200)
                project_id = project_response.json()["id"]

                upload_response = client.post(
                    "/api/v1/assets/upload",
                    data={"project_id": project_id, "asset_type": "text", "language": "zh"},
                    files={"file": ("source.txt", b"long text content", "text/plain")},
                )
                self.assertEqual(upload_response.status_code, 200)
                uploaded = upload_response.json()
                self.assertEqual(uploaded["asset"]["project_id"], project_id)
                self.assertEqual(uploaded["asset"]["asset_type"], "text")
                self.assertEqual(uploaded["asset"]["mime_type"], "text/plain")
                self.assertEqual(uploaded["asset"]["language"], "zh")
                self.assertEqual(uploaded["asset"]["metadata"]["original_name"], "source.txt")
                self.assertEqual(uploaded["asset"]["metadata"]["storage"], "local")
                self.assertIsNotNone(uploaded["asset"]["created_at"])
                self.assertEqual(uploaded["storage"]["storage"], "local")
                self.assertTrue(Path(uploaded["storage"]["file_path"]).exists())

                assets_response = client.get("/api/v1/assets")
                self.assertEqual(assets_response.status_code, 200)
                assets = assets_response.json()
                self.assertEqual(len(assets), 1)
                self.assertEqual(assets[0]["file_name"], "source.txt")
                self.assertEqual(assets[0]["mime_type"], "text/plain")
                self.assertEqual(assets[0]["language"], "zh")
                self.assertEqual(assets[0]["metadata"]["original_name"], "source.txt")
                self.assertEqual(assets[0]["metadata"]["storage"], "local")
                self.assertIsNotNone(assets[0]["created_at"])
            finally:
                engine.dispose()

    def test_asset_upload_streams_file_without_reading_entire_body(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-stream-upload-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

            for name in list(sys.modules):
                if name.startswith("app."):
                    sys.modules.pop(name, None)

            from fastapi.testclient import TestClient
            from starlette.datastructures import UploadFile as StarletteUploadFile
            from app.db.base import Base
            from app.db.session import engine
            import app.models  # noqa: F401
            from app.main import app

            Base.metadata.create_all(bind=engine)
            try:
                client = TestClient(app)
                project_response = client.post(
                    "/api/v1/projects",
                    json={"name": "流式上传项目", "description": "", "domain_type": "video", "status": "active"},
                )
                project_id = project_response.json()["id"]

                with patch.object(StarletteUploadFile, "read", side_effect=AssertionError("upload endpoint must stream file object instead of reading it into memory")):
                    response = client.post(
                        "/api/v1/assets/upload",
                        data={"project_id": project_id, "asset_type": "video", "language": "zh"},
                        files={"file": ("long-video.mp4", b"video-bytes" * 1024, "video/mp4")},
                    )

                self.assertEqual(response.status_code, 200)
                uploaded = response.json()
                self.assertEqual(uploaded["asset"]["asset_type"], "video")
                self.assertEqual(uploaded["storage"]["storage"], "local")
                self.assertTrue(Path(uploaded["storage"]["file_path"]).exists())
                self.assertGreater(Path(uploaded["storage"]["file_path"]).stat().st_size, 0)
            finally:
                engine.dispose()

    def test_asset_upload_rejects_storage_placeholder_without_creating_asset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp_dir) / 'envoy-placeholder-upload-test.db'}"
            os.environ["ENVOY_EXPORT_DIR"] = str(Path(tmp_dir) / "exports")
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "storage")

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
                    json={"name": "存储占位失败项目", "description": "", "domain_type": "video", "status": "active"},
                )
                project_id = project_response.json()["id"]

                with patch(
                    "app.api.v1.assets.upload_fileobj_to_storage",
                    return_value={"bucket": "envoy", "object_name": "long-video.mp4", "storage": "local-placeholder", "reason": "minio-sdk-missing"},
                ):
                    response = client.post(
                        "/api/v1/assets/upload",
                        data={"project_id": project_id, "asset_type": "video", "language": "zh"},
                        files={"file": ("long-video.mp4", b"video bytes", "video/mp4")},
                    )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "Storage backend unavailable: minio-sdk-missing")
                self.assertEqual(client.get("/api/v1/assets").json(), [])
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
