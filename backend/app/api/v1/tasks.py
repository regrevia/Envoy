from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import TASK_STALE_AFTER_SECONDS
from app.db.session import get_db
from app.models.semantic_unit import SemanticUnit
from app.schemas.task import TaskCreate
from app.services.crud_service import create_task, get_asset, get_project, list_tasks, get_task, update_task_status
from app.services.task_dispatcher import dispatch_task
from app.workers.jobs import ping_job, preprocess_job, analyze_job, sample_job, export_job, export_skill_job, export_markdown_job

router = APIRouter()

RETRYABLE_TASKS = {
    "preprocess": preprocess_job,
    "analyze": analyze_job,
    "generate_samples": sample_job,
    "sample": sample_job,
    "export": export_job,
    "export_skill": export_skill_job,
    "export_markdown": export_markdown_job,
}


def _task_payload(task):
    stale_seconds = 0
    if task.status == "running" and task.updated_at is not None:
        stale_seconds = max(int((datetime.utcnow() - task.updated_at).total_seconds()), 0)
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
        "is_stale": task.status == "running" and stale_seconds >= TASK_STALE_AFTER_SECONDS,
        "stale_seconds": stale_seconds,
        "stale_after_seconds": TASK_STALE_AFTER_SECONDS,
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
    worker = RETRYABLE_TASKS.get(original.task_type)
    if worker is None:
        raise HTTPException(status_code=400, detail="Task type is not retryable")
    input_config = dict(original.input_config or {})
    input_config["retry_of_task_id"] = original.id
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
