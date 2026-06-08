from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.task import TaskCreate
from app.services.crud_service import create_task, list_tasks, get_task, update_task_status
from app.workers.jobs import ping_job, preprocess_job, analyze_job, sample_job, export_job, export_skill_job, export_markdown_job

router = APIRouter()


@router.get("/tasks")
def list_tasks_api(db: Session = Depends(get_db)):
    return [{"id": t.id, "task_type": t.task_type, "status": t.status, "progress": t.progress} for t in list_tasks(db)]


@router.get("/tasks/{task_id}")
def get_task_api(task_id: str, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task is None:
        return None
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress, "result_summary": task.result_summary, "error_message": task.error_message}


@router.post("/tasks/{task_id}/status")
def set_task_status(task_id: str, payload: dict, db: Session = Depends(get_db)):
    task = update_task_status(db, task_id, payload.get("status", "pending"), payload.get("progress"), payload.get("result_summary"), payload.get("error_message"))
    return None if task is None else {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/preprocess")
def create_preprocess_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    preprocess_job.delay({"task_id": task.id, **payload.model_dump()})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/analyze")
def create_analyze_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    analyze_job.delay({"task_id": task.id, **payload.model_dump()})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/generate-samples")
def create_sample_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    sample_job.delay({"task_id": task.id, **payload.model_dump()})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/export")
def create_export_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = create_task(db, payload)
    export_job.delay({"task_id": task.id, **payload.model_dump()})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/export-skill")
def create_export_skill_task(payload: dict, db: Session = Depends(get_db)):
    task = create_task(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_skill", input_config=payload))
    export_skill_job.delay({"task_id": task.id, **payload})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}


@router.post("/tasks/export-markdown")
def create_export_markdown_task(payload: dict, db: Session = Depends(get_db)):
    task = create_task(db, TaskCreate(project_id=payload.get("project_id"), asset_id=payload.get("asset_id"), task_type="export_markdown", input_config=payload))
    export_markdown_job.delay({"task_id": task.id, **payload})
    return {"id": task.id, "task_type": task.task_type, "status": task.status, "progress": task.progress}
