from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.task import TaskCreate
from app.services.crud_service import create_task, list_tasks, get_task, update_task_status
from app.services.task_dispatcher import dispatch_task
from app.workers.jobs import ping_job, preprocess_job, analyze_job, sample_job, export_job, export_skill_job, export_markdown_job

router = APIRouter()


def _task_payload(task):
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result_summary": task.result_summary,
        "error_message": task.error_message,
    }


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


@router.post("/tasks/preprocess")
def create_preprocess_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    return dispatch_task(db, task, preprocess_job, payload.model_dump())


@router.post("/tasks/analyze")
def create_analyze_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    return dispatch_task(db, task, analyze_job, payload.model_dump())


@router.post("/tasks/generate-samples")
def create_sample_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    return dispatch_task(db, task, sample_job, payload.model_dump())


@router.post("/tasks/export")
def create_export_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    return dispatch_task(db, task, export_job, payload.model_dump())


@router.post("/tasks/export-skill")
def create_export_skill_task(payload: dict, db: Session = Depends(get_db)):
    task = create_task(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_skill", input_config=payload))
    return dispatch_task(db, task, export_skill_job, payload)


@router.post("/tasks/export-markdown")
def create_export_markdown_task(payload: dict, db: Session = Depends(get_db)):
    task = create_task(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_markdown", input_config=payload))
    return dispatch_task(db, task, export_markdown_job, payload)
