import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_video_to_markdown_task_api_creates_one_resumable_task(tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-video-url-api.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_VIDEO_DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from fastapi.testclient import TestClient
    from app.db.base import Base
    from app.db.session import engine, SessionLocal
    import app.models  # noqa: F401
    from app.main import app
    from app.schemas.project import ProjectCreate
    from app.services.crud_service import create_project

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="video url", description="", domain_type="video", status="active"))
        with patch("app.api.v1.tasks.video_to_markdown_job.delay") as delay:
            response = TestClient(app).post(
                "/api/v1/tasks/video-to-markdown",
                json={"project_id": project.id, "source_url": "https://www.bilibili.com/video/BV1xx411c7mD"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["task_type"] == "video_to_markdown"
        assert body["status"] == "pending"
        assert body["input_config"]["source_url"].startswith("https://")
        assert body["project_id"] == project.id
        delay.assert_called_once()
    finally:
        db.close()
        engine.dispose()


def test_video_to_markdown_upload_api_stores_only_cookie_file_reference(tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-video-cookie-api.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_VIDEO_DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "celery"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from fastapi.testclient import TestClient
    from app.db.base import Base
    from app.db.session import engine, SessionLocal
    import app.models  # noqa: F401
    from app.main import app
    from app.schemas.project import ProjectCreate
    from app.services.crud_service import create_project, get_task

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="cookie video", description="", domain_type="video", status="active"))
        with patch("app.api.v1.tasks.video_to_markdown_job.delay") as delay:
            response = TestClient(app).post(
                "/api/v1/tasks/video-to-markdown/upload",
                data={"project_id": project.id, "source_url": "https://www.douyin.com/video/123"},
                files={"cookies_file": ("cookies.txt", b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n", "text/plain")},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["input_config"]["auth"]["mode"] == "cookies_vault"
        assert "value" not in body["input_config"]
        assert len(body["input_config"]["auth"]["credential_id"]) == 32
        delay.assert_called_once()
    finally:
        db.close()
        engine.dispose()


def test_video_checkpoint_round_trip_is_json_safe():
    from app.workers.jobs import merge_video_task_checkpoint, read_video_task_checkpoint
    from app.api.v1.tasks import RETRYABLE_TASKS

    config = {"source_url": "https://example.com/v", "checkpoints": {}}
    merge_video_task_checkpoint(config, "download", {"status": "success", "path": "/tmp/video.mp4"})
    merge_video_task_checkpoint(config, "preprocess", {"status": "success", "semantic_task_id": "child-1"})

    assert read_video_task_checkpoint(config, "download")["path"] == "/tmp/video.mp4"
    assert read_video_task_checkpoint(config, "preprocess")["semantic_task_id"] == "child-1"
    assert "video_to_markdown" in RETRYABLE_TASKS


def test_video_download_checkpoint_is_marked_complete_for_progress_ui():
    from app.workers.jobs import merge_video_task_checkpoint, read_video_task_checkpoint

    config = {"checkpoints": {}}
    merge_video_task_checkpoint(config, "download", {
        "status": "success",
        "path": "/tmp/video.mp4",
        "asset_id": "asset-1",
    })

    assert read_video_task_checkpoint(config, "download")["status"] == "success"


def test_video_worker_preserves_raw_download_error(tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-video-worker-error.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_VIDEO_DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_project, create_task, get_task
    from app.services.video_source_service import VideoSourceError
    from app.workers.jobs import video_to_markdown_job

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="worker error", description="", domain_type="video", status="active"))
        task = create_task(
            db,
            TaskCreate(
                project_id=project.id,
                task_type="video_to_markdown",
                input_config={"source_url": "https://www.bilibili.com/video/BV1error"},
            ),
        )
        task_id = task.id
        project_id = project.id
    finally:
        db.close()

    raw_error = "ERROR: [BiliBili] BV1error: login required; use --cookies-from-browser"
    with patch("app.workers.jobs.download_video", side_effect=VideoSourceError(raw_error)):
        result = video_to_markdown_job.run({"task_id": task_id, "project_id": project_id})

    db = SessionLocal()
    try:
        stored = get_task(db, task_id)
        assert result["status"] == "failed"
        assert stored.status == "failed"
        assert stored.error_message == raw_error
    finally:
        db.close()
        engine.dispose()


def test_video_export_rows_drop_redundant_media_summary_when_transcript_exists():
    from types import SimpleNamespace
    from app.workers.jobs import _video_export_rows

    rows = _video_export_rows([
        SimpleNamespace(
            id="transcript",
            project_id="p",
            asset_id="a",
            segment_id=None,
            unit_type="transcript",
            title="transcript",
            content="spoken knowledge",
            labels={"stage": "preprocess", "source": "media_transcript"},
            confidence=0.9,
        ),
        SimpleNamespace(
            id="summary",
            project_id="p",
            asset_id="a",
            segment_id=None,
            unit_type="scene",
            title="preprocess summary",
            content='{"transcript": {"text": "spoken knowledge"}}',
            labels={"stage": "preprocess", "source": "media_asset"},
            confidence=0.8,
        ),
    ])

    assert [row["id"] for row in rows] == ["transcript"]


def test_video_worker_reuses_successful_download_and_preprocess_checkpoints(tmp_path):
    import json
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-video-worker-resume.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_VIDEO_DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    os.environ["ENVOY_EXPORT_DIR"] = str(tmp_path / "exports")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.models.semantic_unit import SemanticUnit
    from app.schemas.asset import AssetCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task, get_task
    from app.workers.jobs import video_to_markdown_job

    Base.metadata.create_all(bind=engine)
    video_path = tmp_path / "cached.mp4"
    video_path.write_bytes(b"already downloaded")
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="resume", description="", domain_type="video", status="active"))
        asset = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="video",
            file_name=video_path.name,
            file_path=str(video_path),
            mime_type="video/mp4",
            metadata={"source_url": "https://example.com/video"},
        ))
        child = create_task(db, TaskCreate(
            project_id=project.id,
            asset_id=asset.id,
            task_type="preprocess",
            input_config={},
        ))
        child.status = "success"
        db.commit()
        db.add(SemanticUnit(
            id="resume-transcript-unit",
            project_id=project.id,
            asset_id=asset.id,
            segment_id=None,
            unit_type="transcript",
            title="关键知识",
            content="恢复检查点后应保留完整的知识内容。",
            labels={"stage": "preprocess", "source": "media_transcript", "task_id": child.id},
            confidence=0.95,
        ))
        db.commit()
        parent = create_task(db, TaskCreate(
            project_id=project.id,
            asset_id=asset.id,
            task_type="video_to_markdown",
            input_config={
                "source_url": "https://example.com/video",
                "title": "恢复验收视频",
                "checkpoints": {
                    "download": {
                        "status": "success",
                        "path": str(video_path),
                        "asset_id": asset.id,
                        "video_id": "cached",
                        "title": "恢复验收视频",
                    },
                    "preprocess": {
                        "status": "success",
                        "semantic_task_id": child.id,
                        "asset_id": asset.id,
                    },
                },
            },
        ))
        parent_id = parent.id
        project_id = project.id
    finally:
        db.close()

    with patch("app.workers.jobs.download_video", side_effect=AssertionError("download checkpoint should be reused")), \
         patch("app.workers.jobs.preprocess_job.run", side_effect=AssertionError("preprocess checkpoint should be reused")):
        result = video_to_markdown_job.run({"task_id": parent_id, "project_id": project_id})

    db = SessionLocal()
    try:
        stored = get_task(db, parent_id)
        exported = Path(stored.input_config["checkpoints"]["export"]["path"]).read_text(encoding="utf-8")
        assert result["status"] == "video_markdown_ready"
        assert stored.status == "success"
        assert "恢复检查点后应保留完整的知识内容。" in exported
        assert "视频时间线" not in exported
        assert stored.input_config["checkpoints"]["download"]["status"] == "success"
        assert stored.input_config["checkpoints"]["preprocess"]["status"] == "success"
        assert stored.input_config["checkpoints"]["export"]["status"] == "success"
    finally:
        db.close()
        engine.dispose()
