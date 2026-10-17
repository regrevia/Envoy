import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_mixed_library_api_accepts_multiple_asset_types_and_creates_one_parent_task(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mixed-library.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
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
        project = create_project(db, ProjectCreate(
            name="mixed library",
            description="",
            domain_type="knowledge",
            status="active",
        ))
        image = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="image",
            file_name="map.png",
            file_path="map.png",
            mime_type="image/png",
        ))
        document = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="document",
            file_name="guide.pdf",
            file_path="guide.pdf",
            mime_type="application/pdf",
        ))

        with patch("app.api.v1.tasks.mixed_assets_to_markdown_job.delay") as delay:
            response = TestClient(app).post(
                "/api/v1/tasks/mixed-library-to-markdown",
                json={
                    "project_id": project.id,
                    "title": "综合攻略库",
                    "asset_ids": [image.id, document.id],
                    "max_parallel": 3,
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["task_type"] == "mixed_assets_to_markdown"
        assert body["input_config"]["asset_ids"] == [image.id, document.id]
        assert body["input_config"]["max_parallel"] == 3
        delay.assert_called_once()
    finally:
        db.close()
        engine.dispose()


def test_mixed_library_append_requires_completed_mixed_parent(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mixed-library-append.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
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
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(
            name="mixed append",
            description="",
            domain_type="knowledge",
            status="active",
        ))
        first = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="text",
            file_name="first.txt",
            file_path="first.txt",
            mime_type="text/plain",
        ))
        second = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="image",
            file_name="second.png",
            file_path="second.png",
            mime_type="image/png",
        ))
        parent = create_task(db, TaskCreate(
            project_id=project.id,
            task_type="mixed_assets_to_markdown",
            input_config={
                "library_id": "mixed-library-1",
                "library_title": "综合攻略库",
                "asset_ids": [first.id],
            },
        ))
        parent.status = "success"
        db.commit()

        with patch("app.api.v1.tasks.mixed_assets_to_markdown_job.delay") as delay:
            response = TestClient(app).post(
                f"/api/v1/tasks/{parent.id}/append-assets",
                json={"asset_ids": [second.id]},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["input_config"]["library_id"] == "mixed-library-1"
        assert body["input_config"]["asset_ids"] == [second.id]
        assert body["input_config"]["seed_task_ids"] == [parent.id]
        delay.assert_called_once()
    finally:
        db.close()
        engine.dispose()
