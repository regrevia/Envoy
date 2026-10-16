from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


DEFAULT_VIDEO_PATH = ROOT / "tmp" / "e2e-video-rag" / "commercial-long-video.mp4"


def _configured_binary(tool: str):
    env_name = {
        "ffmpeg": "FFMPEG_BIN",
        "ffprobe": "FFPROBE_BIN",
    }.get(tool)
    return os.getenv(env_name or "") or tool


def _binary_available(command: str):
    if os.path.sep in command:
        return Path(command).is_file() and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def _missing_media_dependencies():
    return [tool for tool in ("ffmpeg", "ffprobe") if not _binary_available(_configured_binary(tool))]


def _missing_python_media_dependencies():
    has_whisper = importlib.util.find_spec("whisper") is not None
    has_paddleocr = importlib.util.find_spec("paddleocr") is not None
    has_paddle_backend = importlib.util.find_spec("paddle") is not None
    has_text_extractor = has_whisper or (has_paddleocr and has_paddle_backend)
    return [] if has_text_extractor else ["whisper_or_paddleocr"]


def _video_duration_seconds(video_path: Path):
    command = _configured_binary("ffprobe")
    try:
        completed = subprocess.run(
            [
                command,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if completed.returncode != 0:
            return 0.0
        payload = json.loads(completed.stdout or "{}")
        return float((payload.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _semantic_rows(units):
    return [
        {
            "record_type": "semantic_unit",
            "id": unit.id,
            "project_id": unit.project_id,
            "asset_id": unit.asset_id,
            "unit_type": unit.unit_type,
            "title": unit.title,
            "content": unit.content,
            "labels": unit.labels,
            "confidence": unit.confidence,
        }
        for unit in units
    ]


def _preprocess_failure_payload(stored_task, result: dict):
    semantic = result.get("semantic") if isinstance(result, dict) else None
    semantic_error = {}
    if isinstance(semantic, dict):
        for key in ("audio_extraction_error", "frame_extraction_error", "transcription_failed_segments"):
            value = semantic.get(key)
            if value:
                semantic_error[key] = value
    return {
        "status": "failed",
        "reason": "preprocess_failed",
        "task_status": getattr(stored_task, "status", None),
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "task_error": getattr(stored_task, "error_message", None),
        "result_error": result.get("error") if isinstance(result, dict) else None,
        "semantic_error": semantic_error,
    }


def _media_probe_failure_payload(probe: dict):
    return {
        "status": "failed",
        "reason": "media_probe_failed",
        "probe": probe,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run local long video -> delivery package acceptance flow.")
    parser.add_argument("--video", default=os.getenv("VIDEO_PATH", str(DEFAULT_VIDEO_PATH)), help="Video path to preprocess and export.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "large-video-delivery"), help="Directory for generated delivery package.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'tmp' / 'large-video-delivery-e2e.db'}"))
    parser.add_argument("--min-duration-seconds", type=float, default=float(os.getenv("VIDEO_MIN_DURATION_SECONDS", "60")), help="Minimum video duration accepted as long-video evidence.")
    args = parser.parse_args(argv)

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"missing video: {video_path}", file=sys.stderr)
        return 2
    missing_dependencies = _missing_media_dependencies()
    if missing_dependencies:
        print(json.dumps({
            "status": "failed",
            "reason": "missing_media_dependency",
            "dependency": missing_dependencies[0],
            "missing_dependencies": missing_dependencies,
        }, ensure_ascii=False, indent=2))
        return 3
    missing_python_dependencies = _missing_python_media_dependencies()
    if missing_python_dependencies:
        print(json.dumps({
            "status": "failed",
            "reason": "missing_python_media_dependency",
            "dependency": missing_python_dependencies[0],
            "missing_dependencies": missing_python_dependencies,
        }, ensure_ascii=False, indent=2))
        return 3
    duration_seconds = _video_duration_seconds(video_path)
    if duration_seconds < args.min_duration_seconds:
        print(json.dumps({
            "status": "failed",
            "reason": "video_too_short",
            "duration_seconds": duration_seconds,
            "minimum_duration_seconds": args.min_duration_seconds,
        }, ensure_ascii=False, indent=2))
        return 4

    from probe_media_file import probe_media_file

    media_probe = probe_media_file(video_path)
    if media_probe.get("status") != "ok":
        print(json.dumps(_media_probe_failure_payload(media_probe), ensure_ascii=False, indent=2))
        return 5

    os.environ.setdefault("ENVOY_STORAGE_MODE", "local")
    os.environ.setdefault("ENVOY_LOCAL_STORAGE_DIR", str(ROOT / "tmp" / "storage"))
    os.environ.setdefault("ENVOY_EXPORT_DIR", str(ROOT / "tmp" / "exports"))
    os.environ.setdefault("DATABASE_URL", args.database_url)
    os.environ.setdefault("OLLAMA_MODEL", "")

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.core.storage import storage_client
    from app.models.semantic_unit import SemanticUnit
    from app.schemas.asset import AssetCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task, get_task
    from app.services.train_export_service import export_delivery_package
    from app.workers.jobs import preprocess_job
    from validate_delivery_package import validate_delivery_package

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="large video delivery e2e", description="", domain_type="knowledge", status="active"))
        storage_info = storage_client.upload_bytes(video_path.name, video_path.read_bytes(), "video/mp4")
        asset = create_asset(
            db,
            AssetCreate(
                project_id=project.id,
                asset_type="video",
                file_name=video_path.name,
                file_path=storage_info["object_name"],
                mime_type="video/mp4",
                metadata={"storage": storage_info["storage"]},
            ),
        )
        task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
        project_id = project.id
        asset_id = asset.id
        task_id = task.id
    finally:
        db.close()

    try:
        result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})
    except FileNotFoundError as exc:
        dependency = Path(str(exc.filename or "")).name or str(exc.filename or "unknown")
        print(json.dumps({
            "status": "failed",
            "reason": "missing_media_dependency",
            "dependency": dependency,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 3

    db = SessionLocal()
    try:
        stored_task = get_task(db, task_id)
        units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()
        rows = _semantic_rows(units)
        export_result = export_delivery_package(rows, args.out_dir, task_id, title=video_path.stem, task_name=video_path.stem, project_id=project_id)
        validator_result = validate_delivery_package(Path(export_result["root"]))
        if stored_task.status != "success" or result.get("status") != "preprocessed":
            print(json.dumps(_preprocess_failure_payload(stored_task, result), ensure_ascii=False, indent=2))
            return 1
        if len(units) < 1 or validator_result.get("chunk_count", 0) < 1:
            print(json.dumps({"status": "failed", "reason": "insufficient_video_content", "unit_count": len(units), "chunk_count": validator_result.get("chunk_count", 0)}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        if validator_result["status"] != "ok":
            print(json.dumps({"status": "failed", "reason": "delivery_validation_failed", "validator": validator_result}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        print(json.dumps({
            "status": "ok",
            "task_status": stored_task.status,
            "result_status": result.get("status"),
            "unit_count": len(units),
            "package_root": export_result["root"],
            "manifest": export_result["paths"]["manifest"],
            "import_manifest": export_result["paths"]["import_manifest"],
            "validator": validator_result,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
