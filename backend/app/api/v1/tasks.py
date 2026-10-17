from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from app.core.config import TASK_STALE_AFTER_SECONDS
from app.core.storage import storage_client
from app.db.session import get_db
from app.models.semantic_unit import SemanticUnit
from app.schemas.task import TaskCreate
from app.schemas.video_task import VideoToMarkdownCreate
from app.schemas.video_batch import VideoBatchAppend, VideoBatchCreate, VideoPlaylistDiscover
from app.schemas.mixed_library import MixedLibraryAppend, MixedLibraryCreate
from app.schemas.video_browser import VideoBrowserSessionCreate
from app.services.crud_service import create_task, get_asset, get_project, list_tasks, get_task, update_task_status
from app.services.task_dispatcher import dispatch_task
from app.services.video_source_service import (
    VideoSourceError,
    classify_discovery_error,
    classify_video_source,
    discover_video_items,
    normalize_video_auth,
    validate_video_url,
)
from app.services.browser_session_service import BrowserSessionError, browser_session_manager
from app.workers.jobs import (
    ping_job,
    preprocess_job,
    analyze_job,
    sample_job,
    export_job,
    export_skill_job,
    export_markdown_job,
    video_to_markdown_job,
    video_batch_to_markdown_job,
    mixed_assets_to_markdown_job,
)

router = APIRouter()

RETRYABLE_TASKS = {
    "preprocess": preprocess_job,
    "analyze": analyze_job,
    "generate_samples": sample_job,
    "sample": sample_job,
    "export": export_job,
    "export_skill": export_skill_job,
    "export_markdown": export_markdown_job,
    "video_to_markdown": video_to_markdown_job,
    "video_batch_to_markdown": video_batch_to_markdown_job,
    "mixed_assets_to_markdown": mixed_assets_to_markdown_job,
}


def _is_remote_source_path(source_path: str):
    return urlparse(source_path).scheme.lower() in {"http", "https", "s3"}


def _asset_storage_file_exists(asset):
    if asset is None or not asset.file_path:
        return False
    if storage_client.mode == "local":
        return storage_client._local_path(asset.file_path).is_file() or Path(asset.file_path).is_file()
    metadata = asset.extra_metadata if isinstance(asset.extra_metadata, dict) else {}
    return metadata.get("storage") in {"minio", "local"}


def _retry_input_config(db: Session, original):
    input_config = dict(original.input_config or {})
    source_path = str(input_config.get("source_path") or "").strip()
    if original.task_type == "preprocess" and source_path and not _is_remote_source_path(source_path):
        asset = get_asset(db, original.asset_id) if original.asset_id else None
        if not Path(source_path).is_file() and _asset_storage_file_exists(asset):
            input_config.pop("source_path", None)
    input_config["retry_of_task_id"] = original.id
    return input_config


def _task_staleness(task):
    stale_seconds = 0
    stale_candidate = task.status in {"pending", "running"}
    if stale_candidate and task.updated_at is not None:
        stale_seconds = max(int((datetime.utcnow() - task.updated_at).total_seconds()), 0)
    is_stale = stale_candidate and stale_seconds >= TASK_STALE_AFTER_SECONDS
    return {
        "is_stale": is_stale,
        "stale_seconds": stale_seconds,
        "stale_after_seconds": TASK_STALE_AFTER_SECONDS,
    }


def _can_cancel(task):
    return task.status in {"pending", "running", "dispatch_failed"}


def _can_retry(task):
    return task.status in {"failed", "dispatch_failed", "canceled"}


def _task_diagnostics_payload(task):
    staleness = _task_staleness(task)
    can_cancel = _can_cancel(task)
    can_retry = _can_retry(task)
    severity = "healthy"
    recommended_action = "monitor"
    summary = "任务状态正常，继续等待刷新。"
    next_actions = ["继续观察任务进度"]

    if staleness["is_stale"]:
        severity = "stale"
        recommended_action = "cancel_then_retry"
        if task.status == "pending":
            summary = "任务排队后长时间未开始，可能卡在任务队列、Celery/Redis 或本地执行器启动阶段。"
            next_actions = ["取消任务", "重试任务", "检查任务队列和执行器"]
        else:
            summary = "任务长时间未更新，可能卡在媒体处理、转写、OCR 或外部服务调用。"
            next_actions = ["取消任务", "重试任务", "检查媒体处理配置和服务日志"]
    elif task.status == "dispatch_failed":
        severity = "failed"
        recommended_action = "retry"
        summary = "任务投递失败，通常与 Redis/Celery 或本地执行器启动失败有关。"
        next_actions = ["重试任务", "检查任务执行服务"]
    elif task.status == "failed":
        severity = "failed"
        recommended_action = "retry"
        summary = "任务执行失败，请查看错误信息后重试。"
        next_actions = ["查看错误信息", "重试任务"]
    elif task.status == "canceled":
        severity = "canceled"
        recommended_action = "retry"
        summary = "任务已取消，原任务不会继续执行，可从原任务上下文重试。"
        next_actions = ["重试任务", "检查媒体处理配置和服务日志"]
    elif task.status == "success":
        severity = "complete"
        recommended_action = "review_outputs"
        summary = "任务已完成，可以继续审查语义单元、样本或导出产物。"
        next_actions = ["查看产物", "审查追溯链路"]

    return {
        "task_id": task.id,
        "status": task.status,
        "task_type": task.task_type,
        "progress": task.progress,
        "updated_at": task.updated_at,
        "result_summary": task.result_summary,
        "error_message": task.error_message,
        **staleness,
        "severity": severity,
        "summary": summary,
        "recommended_action": recommended_action,
        "next_actions": next_actions,
        "can_cancel": can_cancel,
        "can_retry": can_retry,
    }


def _task_payload(task):
    staleness = _task_staleness(task)
    return {
        "id": task.id,
        "project_id": task.project_id,
        "asset_id": task.asset_id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "input_config": task.input_config,
        "result_summary": task.result_summary,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        **staleness,
    }


def _validated_task_payload(db: Session, payload: TaskCreate):
    if get_project(db, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    source_unit_id = (payload.input_config or {}).get("source_unit_id")
    if payload.task_type == "generate_samples" and source_unit_id:
        unit = db.query(SemanticUnit).filter(SemanticUnit.id == source_unit_id).first()
        if unit is None:
            raise HTTPException(status_code=404, detail="Source semantic unit not found")
        if unit.project_id != payload.project_id:
            raise HTTPException(status_code=400, detail="Source semantic unit does not belong to project")
        if payload.asset_id and unit.asset_id and payload.asset_id != unit.asset_id:
            raise HTTPException(status_code=400, detail="Asset does not match source semantic unit")
        return payload.model_copy(update={"asset_id": payload.asset_id or unit.asset_id})

    if payload.asset_id:
        asset = get_asset(db, payload.asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.project_id != payload.project_id:
            raise HTTPException(status_code=400, detail="Asset does not belong to project")
    return payload


@router.get("/tasks")
def list_tasks_api(db: Session = Depends(get_db)):
    return [_task_payload(t) for t in list_tasks(db)]


@router.get("/tasks/{task_id}")
def get_task_api(task_id: str, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_payload(task)


@router.get("/tasks/{task_id}/diagnostics")
def get_task_diagnostics_api(task_id: str, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_diagnostics_payload(task)


@router.post("/tasks/{task_id}/status")
def set_task_status(task_id: str, payload: dict, db: Session = Depends(get_db)):
    task = update_task_status(db, task_id, payload.get("status", "pending"), payload.get("progress"), payload.get("result_summary"), payload.get("error_message"))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_payload(task)


@router.post("/tasks/{task_id}/cancel")
def cancel_task_api(task_id: str, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in {"success", "failed", "canceled"}:
        raise HTTPException(status_code=409, detail="Task is already finished")
    task = update_task_status(db, task_id, "canceled", task.progress, result_summary="Task canceled by user")
    return _task_payload(task)


@router.post("/tasks/{task_id}/retry")
def retry_task_api(task_id: str, db: Session = Depends(get_db)):
    original = get_task(db, task_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if original.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="Task is still active")
    if original.status not in {"failed", "dispatch_failed", "canceled"}:
        raise HTTPException(status_code=400, detail="Only failed or canceled tasks can be retried")
    worker = RETRYABLE_TASKS.get(original.task_type)
    if worker is None:
        raise HTTPException(status_code=400, detail="Task type is not retryable")
    input_config = _retry_input_config(db, original)
    payload = TaskCreate(
        project_id=original.project_id,
        asset_id=original.asset_id,
        task_type=original.task_type,
        input_config=input_config,
    )
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    dispatch_task(db, task, worker, payload.model_dump())
    refreshed = get_task(db, task.id)
    return _task_payload(refreshed or task)


@router.post("/tasks/preprocess")
def create_preprocess_task(payload: TaskCreate, db: Session = Depends(get_db)):
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, preprocess_job, payload.model_dump())


def _video_task_payload(
    project_id: str,
    source_url: str,
    title: str = "",
    auth: Optional[dict] = None,
    input_config: Optional[dict] = None,
):
    try:
        source_url = validate_video_url(source_url)
        normalized_auth = normalize_video_auth(auth)
    except VideoSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized_config = dict(input_config or {})
    normalized_config.update({
        "source_url": source_url,
        "title": title or "",
    })
    normalized_config["auth"] = normalized_auth or {"mode": "auto"}
    return TaskCreate(
        project_id=project_id,
        task_type="video_to_markdown",
        input_config=normalized_config,
    )


def _create_video_task(db: Session, task_payload: TaskCreate):
    task_payload = _validated_task_payload(db, task_payload)
    task = create_task(db, task_payload)
    return dispatch_task(db, task, video_to_markdown_job, task_payload.model_dump())


@router.post("/tasks/video-to-markdown")
def create_video_to_markdown_task(payload: VideoToMarkdownCreate, db: Session = Depends(get_db)):
    project_id = payload.project_id.strip()
    auth = payload.auth if isinstance(payload.auth, dict) else None
    task_payload = _video_task_payload(
        project_id,
        payload.source_url,
        payload.title,
        auth,
        payload.input_config,
    )
    return _create_video_task(db, task_payload)


@router.post("/video-playlists/discover")
def discover_video_playlist(payload: VideoPlaylistDiscover):
    try:
        return discover_video_items(str(payload.source_url), limit=payload.limit, auth={"mode": "auto"})
    except VideoSourceError as exc:
        error = classify_discovery_error(exc)
        source = classify_video_source(str(payload.source_url))
        detail = {
            **error,
            "source_url": str(payload.source_url),
            "platform": source["platform"],
            "source_kind": source["source_kind"],
        }
        raise HTTPException(status_code=409 if error["requires_user_action"] else 400, detail=detail) from exc


@router.post("/video-playlists/browser-sessions")
def create_browser_discovery_session(payload: VideoBrowserSessionCreate):
    try:
        return browser_session_manager.create(str(payload.source_url), payload.limit)
    except BrowserSessionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/video-playlists/browser-sessions/{session_id}")
def browser_discovery_session_status(session_id: str):
    try:
        return browser_session_manager.status(session_id)
    except BrowserSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/video-playlists/browser-sessions/{session_id}/discover")
def discover_from_browser_session(session_id: str):
    try:
        return browser_session_manager.discover(session_id)
    except BrowserSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/video-playlists/browser-sessions/{session_id}")
def close_browser_discovery_session(session_id: str):
    return browser_session_manager.close(session_id)


def _create_video_batch(
    db: Session,
    project_id: str,
    title: str,
    items: list,
    library_id: Optional[str] = None,
    input_config: Optional[dict] = None,
):
    normalized_items = []
    for item in items:
        if item.asset_id:
            asset = get_asset(db, item.asset_id)
            if asset is None:
                raise HTTPException(status_code=404, detail=f"Video asset not found: {item.asset_id}")
            if asset.project_id != project_id or asset.asset_type != "video":
                raise HTTPException(status_code=400, detail=f"Video asset does not belong to project: {item.asset_id}")
            normalized_items.append({
                "asset_id": item.asset_id,
                "title": str(item.title or asset.file_name or "").strip(),
            })
        else:
            normalized_items.append({
                "source_url": validate_video_url(str(item.source_url)),
                "title": str(item.title or "").strip(),
            })
    config = dict(input_config or {})
    config.update({
        "library_id": library_id or str(uuid4()),
        "library_title": title or "视频知识库",
        "video_items": normalized_items,
        "max_parallel": config.get("max_parallel", 2),
        "auth": {"mode": "auto"},
    })
    payload = TaskCreate(
        project_id=project_id,
        task_type="video_batch_to_markdown",
        input_config=config,
    )
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, video_batch_to_markdown_job, payload.model_dump())


@router.post("/tasks/video-batch-to-markdown")
def create_video_batch_task(payload: VideoBatchCreate, db: Session = Depends(get_db)):
    try:
        return _create_video_batch(
            db,
            payload.project_id.strip(),
            payload.title,
            payload.items,
            library_id=payload.library_id,
            input_config=payload.input_config,
        )
    except VideoSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/append-videos")
def append_videos_to_library(task_id: str, payload: VideoBatchAppend, db: Session = Depends(get_db)):
    source_task = get_task(db, task_id)
    if source_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if source_task.task_type not in {"video_batch_to_markdown", "video_to_markdown"} or source_task.status != "success":
        raise HTTPException(status_code=409, detail="Videos can only be appended to a completed video task or library")
    source_config = source_task.input_config if isinstance(source_task.input_config, dict) else {}
    library_id = str(source_config.get("library_id") or uuid4())
    seed_task_ids = list(source_config.get("seed_task_ids") or [])
    if source_task.task_type == "video_to_markdown":
        seed_task_ids.append(source_task.id)
    try:
        return _create_video_batch(
            db,
            source_task.project_id,
            payload.title or str(source_config.get("library_title") or "视频知识库"),
            payload.items,
            library_id=library_id,
            input_config={
                "append_to_task_id": source_task.id,
                "seed_task_ids": list(dict.fromkeys(seed_task_ids)),
            },
        )
    except VideoSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _create_mixed_library(
    db: Session,
    project_id: str,
    title: str,
    asset_ids: list[str],
    library_id: Optional[str] = None,
    input_config: Optional[dict] = None,
):
    normalized_asset_ids = []
    for asset_id in asset_ids:
        asset = get_asset(db, str(asset_id))
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
        if asset.project_id != project_id:
            raise HTTPException(status_code=400, detail=f"Asset does not belong to project: {asset_id}")
        normalized_asset_ids.append(asset.id)
    config = dict(input_config or {})
    config.update({
        "library_id": library_id or str(uuid4()),
        "library_title": title or "混合素材知识库",
        "asset_ids": list(dict.fromkeys(normalized_asset_ids)),
        "max_parallel": config.get("max_parallel", 2),
        "markdown_enhance_with_ollama": bool(config.get("markdown_enhance_with_ollama", False)),
    })
    payload = TaskCreate(
        project_id=project_id,
        task_type="mixed_assets_to_markdown",
        input_config=config,
    )
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, mixed_assets_to_markdown_job, payload.model_dump())


@router.post("/tasks/mixed-library-to-markdown")
def create_mixed_library_task(payload: MixedLibraryCreate, db: Session = Depends(get_db)):
    return _create_mixed_library(
        db,
        payload.project_id.strip(),
        payload.title,
        payload.asset_ids,
        library_id=payload.library_id,
        input_config={
            **payload.input_config,
            "max_parallel": payload.max_parallel,
            "markdown_enhance_with_ollama": payload.markdown_enhance_with_ollama,
        },
    )


@router.post("/tasks/{task_id}/append-assets")
def append_assets_to_mixed_library(task_id: str, payload: MixedLibraryAppend, db: Session = Depends(get_db)):
    source_task = get_task(db, task_id)
    if source_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if source_task.task_type not in {"mixed_assets_to_markdown", "video_to_markdown", "video_batch_to_markdown"} or source_task.status != "success":
        raise HTTPException(status_code=409, detail="Assets can only be appended to a completed video or mixed library")
    source_config = source_task.input_config if isinstance(source_task.input_config, dict) else {}
    seed_task_ids = list(source_config.get("seed_task_ids") or [])
    seed_task_ids.append(source_task.id)
    return _create_mixed_library(
        db,
        source_task.project_id,
        payload.title or str(source_config.get("library_title") or "混合素材知识库"),
        payload.asset_ids,
        library_id=str(source_config.get("library_id") or uuid4()),
        input_config={
            "append_to_task_id": source_task.id,
            "seed_task_ids": list(dict.fromkeys(seed_task_ids)),
            "max_parallel": payload.max_parallel,
            "markdown_enhance_with_ollama": payload.markdown_enhance_with_ollama,
        },
    )


@router.post("/tasks/analyze")
def create_analyze_task(payload: TaskCreate, db: Session = Depends(get_db)):
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, analyze_job, payload.model_dump())


@router.post("/tasks/generate-samples")
def create_sample_task(payload: TaskCreate, db: Session = Depends(get_db)):
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, sample_job, payload.model_dump())


@router.post("/tasks/export")
def create_export_task(payload: TaskCreate, db: Session = Depends(get_db)):
    payload = _validated_task_payload(db, payload)
    task = create_task(db, payload)
    return dispatch_task(db, task, export_job, payload.model_dump())


@router.post("/tasks/export-skill")
def create_export_skill_task(payload: dict, db: Session = Depends(get_db)):
    task_payload = _validated_task_payload(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_skill", input_config=payload))
    task = create_task(db, task_payload)
    return dispatch_task(db, task, export_skill_job, payload)


@router.post("/tasks/export-markdown")
def create_export_markdown_task(payload: dict, db: Session = Depends(get_db)):
    task_payload = _validated_task_payload(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_markdown", input_config=payload))
    task = create_task(db, task_payload)
    return dispatch_task(db, task, export_markdown_job, payload)
