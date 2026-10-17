import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_mixed_library_worker_exports_selected_text_assets_to_one_markdown_bundle(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mixed-worker.db'}"
    os.environ["ENVOY_STORAGE_MODE"] = "local"
    os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["ENVOY_EXPORT_DIR"] = str(tmp_path / "exports")
    os.environ["ENVOY_TASK_EXECUTION_MODE"] = "local"
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.schemas.asset import AssetCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task, get_task
    from app.workers.jobs import mixed_assets_to_markdown_job

    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "one.txt").write_text("红色钥匙在村口石桥旁。", encoding="utf-8")
    (storage / "two.txt").write_text("进入洞窟后先点亮左侧火把。", encoding="utf-8")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(
            name="mixed worker",
            description="",
            domain_type="knowledge",
            status="active",
        ))
        first = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="text",
            file_name="one.txt",
            file_path="one.txt",
            mime_type="text/plain",
        ))
        second = create_asset(db, AssetCreate(
            project_id=project.id,
            asset_type="text",
            file_name="two.txt",
            file_path="two.txt",
            mime_type="text/plain",
        ))
        task = create_task(db, TaskCreate(
            project_id=project.id,
            task_type="mixed_assets_to_markdown",
            input_config={
                "library_id": "mixed-worker-library",
                "library_title": "综合攻略",
                "asset_ids": [first.id, second.id],
                "max_parallel": 2,
            },
        ))
        task_id = task.id
        project_id = project.id
    finally:
        db.close()

    result = mixed_assets_to_markdown_job.run({"task_id": task_id, "project_id": project_id})

    db = SessionLocal()
    try:
        stored = get_task(db, task_id)
        assert result["status"] == "mixed_library_ready"
        assert stored.status == "success"
        index_path = Path(stored.input_config["library_index_path"])
        assert index_path.is_file()
        root = index_path.parent
        index = index_path.read_text(encoding="utf-8")
        assert "one.txt" in index
        assert "two.txt" in index
        assert (root.parent / "manifest.json").is_file()
        assert stored.input_config["library_artifact_id"]
    finally:
        db.close()
        engine.dispose()
