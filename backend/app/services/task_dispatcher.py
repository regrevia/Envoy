from threading import Thread
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import TASK_EXECUTION_MODE
from app.db.session import SessionLocal
from app.services.crud_service import update_task_status


def _task_payload(task):
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "project_id": task.project_id,
        "asset_id": task.asset_id,
        "input_config": task.input_config,
        "result_summary": task.result_summary,
        "error_message": task.error_message,
    }


def _run_local_task(celery_task: Any, payload: dict):
    try:
        celery_task.run(payload)
    except Exception as exc:
        db = SessionLocal()
        try:
            try:
                update_task_status(db, payload.get("task_id"), "failed", 100, error_message=f"Local task failed: {exc}")
            except Exception:
                pass
        finally:
            db.close()


def dispatch_task(db: Session, task, celery_task: Any, payload: dict):
    job_payload = {
        **payload,
        "task_id": task.id,
        "project_id": task.project_id,
        "asset_id": task.asset_id,
    }

    if TASK_EXECUTION_MODE == "celery":
        try:
            celery_task.delay(job_payload)
        except Exception as exc:
            task = update_task_status(db, task.id, "dispatch_failed", 0, error_message=f"Task dispatch failed: {exc}")
        return _task_payload(task)

    task = update_task_status(db, task.id, "running", 1, result_summary="Local task started")
    worker = Thread(target=_run_local_task, args=(celery_task, job_payload), daemon=True)
    worker.start()
    return _task_payload(task)
