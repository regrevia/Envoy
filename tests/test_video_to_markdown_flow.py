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
        assert body["input_config"]["auth"]["mode"] == "auto"
        assert body["project_id"] == project.id
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


def test_video_preprocess_config_carries_selected_ocr_profile():
    from app.workers.jobs import _video_preprocess_config

    assert _video_preprocess_config(
        {
            "ocr_profile": "pro",
            "video_frame_interval_seconds": 3,
            "video_max_frame_count": 20,
        },
        "/tmp/video.mp4",
    ) == {
        "source_path": "/tmp/video.mp4",
        "video_frame_interval_seconds": 3,
        "video_target_frame_rate": 0,
        "video_max_frame_count": 20,
        "preserve_media_temp_dirs": True,
        "ocr_profile": "pro",
    }


def test_local_task_recovery_requeues_interrupted_video_tasks(tmp_path, monkeypatch):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-recovery.db'}"
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_project, create_task
    from app.services.task_dispatcher import recover_interrupted_local_tasks

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="recovery", description="", domain_type="video", status="active"))
        task = create_task(db, TaskCreate(
            project_id=project.id,
            task_type="video_to_markdown",
            input_config={
                "source_url": "https://example.com/interrupted",
                "checkpoints": {"download": {"status": "success", "path": "/tmp/cached.mp4"}},
            },
        ))
        task.status = "running"
        task.progress = 68
        db.commit()

        dispatched = []
        monkeypatch.setattr(
            "app.services.task_dispatcher.dispatch_task",
            lambda db, task, worker, payload: dispatched.append((task.id, worker, payload)) or {"id": task.id},
        )

        recovered = recover_interrupted_local_tasks(db)

        assert recovered == 1
        assert len(dispatched) == 1
        assert dispatched[0][0] == task.id
        assert dispatched[0][2]["source_url"] == "https://example.com/interrupted"
        db.refresh(task)
        assert task.status == "pending"
        assert task.progress == 68
        assert "服务重启" in (task.result_summary or "")
    finally:
        db.close()
        engine.dispose()


def test_video_batch_and_append_api_create_independent_library_tasks(tmp_path):
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'envoy-video-batch-api.db'}"
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
    from app.schemas.asset import AssetCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="batch api", description="", domain_type="video", status="active"))
        local_asset = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="video",
            file_name="local-demo.webm",
            file_path=str(tmp_path / "local-demo.webm"),
            mime_type="video/webm",
            metadata={"storage": "local"},
        ))
        completed_single = create_task(db, TaskCreate(
            project_id=project.id,
            task_type="video_to_markdown",
            input_config={"source_url": "https://example.test/first", "title": "旧单视频"},
        ))
        completed_single.status = "success"
        db.commit()
        single_id = completed_single.id
        project_id = project.id

        with patch("app.api.v1.tasks.video_batch_to_markdown_job.delay") as delay:
            batch_response = TestClient(app).post(
                "/api/v1/tasks/video-batch-to-markdown",
                json={
                    "project_id": project_id,
                    "title": "游戏攻略",
                    "items": [
                        {"source_url": "https://example.test/a", "title": "攻略 A"},
                        {"asset_id": local_asset.id, "title": "本地攻略 B"},
                    ],
                },
            )
            assert batch_response.status_code == 200
            batch_body = batch_response.json()
            assert batch_body["task_type"] == "video_batch_to_markdown"
            assert len(batch_body["input_config"]["video_items"]) == 2
            assert batch_body["input_config"]["video_items"][1]["asset_id"] == local_asset.id
            library_id = batch_body["input_config"]["library_id"]
            delay.assert_called_once()

        with patch("app.api.v1.tasks.video_batch_to_markdown_job.delay") as append_delay:
            append_response = TestClient(app).post(
                f"/api/v1/tasks/{single_id}/append-videos",
                json={"items": [{"source_url": "https://example.test/c", "title": "追加 C"}]},
            )
        assert append_response.status_code == 200
        append_body = append_response.json()
        assert append_body["task_type"] == "video_batch_to_markdown"
        assert append_body["input_config"]["seed_task_ids"] == [single_id]
        assert append_body["input_config"]["video_items"][0]["source_url"] == "https://example.test/c"
        assert append_body["input_config"]["library_id"] != library_id
        append_delay.assert_called_once()
    finally:
        db.close()
        engine.dispose()


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


def test_video_export_rows_keeps_successful_vision_unit_and_drops_failed_retry_duplicates():
    from types import SimpleNamespace
    from app.workers.jobs import _video_export_rows

    rows = _video_export_rows([
        SimpleNamespace(
            id="vision-failed-old",
            project_id="p",
            asset_id="a",
            segment_id=None,
            unit_type="scene",
            title="连续窗口 1",
            content='{"summary":"","error":"provider error","status":"failed"}',
            labels={"stage": "understand", "source": "video_understanding", "window_index": 0, "status": "failed"},
            confidence=0.35,
        ),
        SimpleNamespace(
            id="vision-success-retry-old",
            project_id="p",
            asset_id="a",
            segment_id=None,
            unit_type="scene",
            title="连续窗口 1",
            content='{"summary":"旧版画面结果","status":"success"}',
            labels={"stage": "understand", "source": "video_understanding", "window_index": 0, "status": "success"},
            confidence=0.78,
        ),
        SimpleNamespace(
            id="vision-success-retry-latest",
            project_id="p",
            asset_id="a",
            segment_id=None,
            unit_type="scene",
            title="连续窗口 1",
            content='{"summary":"最新画面结果","status":"success"}',
            labels={"stage": "understand", "source": "video_understanding", "window_index": 0, "status": "success"},
            confidence=0.78,
        ),
    ])

    assert [row["id"] for row in rows] == ["vision-success-retry-latest"]


def test_long_video_frame_windows_cover_every_frame_with_overlap_and_asr_alignment():
    from app.workers.jobs import _plan_video_frame_windows, _transcript_for_video_window

    frames = [
        {"path": f"frame-{index}", "start_sec": index * 5, "end_sec": (index + 1) * 5}
        for index in range(13)
    ]
    windows = _plan_video_frame_windows(frames, window_size=6, overlap=1)

    assert [len(window) for window in windows] == [6, 6, 3]
    assert windows[0][-1]["path"] == windows[1][0]["path"]
    covered = {frame["path"] for window in windows for frame in window}
    assert covered == {frame["path"] for frame in frames}

    transcript = _transcript_for_video_window(
        [
            {"start_sec": 4, "end_sec": 9, "text": "窗口一口播"},
            {"start_sec": 29, "end_sec": 34, "text": "窗口二口播"},
        ],
        "全文兜底",
        5,
        10,
    )
    assert transcript == "窗口一口播"


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
