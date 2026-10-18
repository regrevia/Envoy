from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import LOCAL_TASK_MAX_CONCURRENCY, TASK_EXECUTION_MODE
from app.db.session import SessionLocal
from app.models.processing_task import ProcessingTask
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


_local_executor = ThreadPoolExecutor(
    max_workers=LOCAL_TASK_MAX_CONCURRENCY,
    thread_name_prefix="envoy-local-task",
)


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
    _local_executor.submit(_run_local_task, celery_task, job_payload)
    return _task_payload(task)


def recover_interrupted_local_tasks(db: Session) -> int:
    """Requeue local tasks left active when the API process was restarted."""
    if TASK_EXECUTION_MODE != "local":
        return 0

    # Import lazily to keep the dispatcher usable during app startup and tests.
    from app.workers.jobs import (
        analyze_job,
        export_job,
        export_markdown_job,
        export_skill_job,
        mixed_assets_to_markdown_job,
        ping_job,
        preprocess_job,
        sample_job,
        video_batch_to_markdown_job,
        video_to_markdown_job,
    )

    workers = {
        "ping": ping_job,
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
    tasks = (
        db.query(ProcessingTask)
        .filter(ProcessingTask.status.in_(["pending", "running"]))
        .order_by(ProcessingTask.created_at.asc())
        .all()
    )
    recovered = 0
    for task in tasks:
        worker = workers.get(task.task_type)
        if worker is None:
            continue
        task.status = "pending"
        task.result_summary = "服务重启后从检查点恢复，等待重新执行"
        task.error_message = None
        db.commit()
        dispatch_task(db, task, worker, dict(task.input_config or {}))
        recovered += 1
    return recovered
