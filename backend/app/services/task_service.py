from sqlalchemy.orm import Session
from app.services.crud_service import list_tasks


def get_task_by_id(db: Session, task_id: str):
    return next((task for task in list_tasks(db) if task.id == task_id), None)
