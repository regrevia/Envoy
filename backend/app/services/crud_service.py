from uuid import uuid4
from sqlalchemy.orm import Session
from app.models.project import Project
from app.models.asset import Asset
from app.models.processing_task import ProcessingTask
from app.models.artifact import Artifact
from app.models.export_record import ExportRecord
from app.schemas.project import ProjectCreate
from app.schemas.asset import AssetCreate
from app.schemas.task import TaskCreate
from app.schemas.artifact import ArtifactCreate


def create_project(db: Session, payload: ProjectCreate):
    obj = Project(id=str(uuid4()), name=payload.name, description=payload.description, domain_type=payload.domain_type, status=payload.status or "active")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_projects(db: Session):
    return db.query(Project).order_by(Project.created_at.desc()).all()


def get_project(db: Session, project_id: str):
    return db.query(Project).filter(Project.id == project_id).first()


def create_asset(db: Session, payload: AssetCreate):
    obj = Asset(id=str(uuid4()), project_id=payload.project_id, asset_type=payload.asset_type, file_name=payload.file_name, file_path=payload.file_path, mime_type=payload.mime_type, duration_sec=payload.duration_sec, language=payload.language, extra_metadata=payload.metadata)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_assets(db: Session):
    return db.query(Asset).order_by(Asset.created_at.desc()).all()


def get_asset(db: Session, asset_id: str):
    return db.query(Asset).filter(Asset.id == asset_id).first()


def create_task(db: Session, payload: TaskCreate):
    obj = ProcessingTask(id=str(uuid4()), project_id=payload.project_id, asset_id=payload.asset_id, task_type=payload.task_type, input_config=payload.input_config, status="pending", progress=0)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_tasks(db: Session):
    return db.query(ProcessingTask).order_by(ProcessingTask.created_at.desc()).all()


def get_task(db: Session, task_id: str):
    return db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()


def update_task_status(db: Session, task_id: str, status: str, progress: int | None = None, result_summary: str | None = None, error_message: str | None = None):
    task = get_task(db, task_id)
    if task is None:
        return None
    task.status = status
    if progress is not None:
        task.progress = progress
    if result_summary is not None:
        task.result_summary = result_summary
    if error_message is not None:
        task.error_message = error_message
    db.commit()
    db.refresh(task)
    return task


def create_artifact(db: Session, payload: ArtifactCreate):
    obj = Artifact(id=str(uuid4()), project_id=payload.project_id, artifact_type=payload.artifact_type, name=payload.name, file_path=payload.file_path, version=payload.version)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_artifacts(db: Session):
    return db.query(Artifact).order_by(Artifact.created_at.desc()).all()


def create_export_record(db: Session, project_id: str, task_id: str, export_type: str, task_name: str | None, root_dir: str, file_list: str):
    obj = ExportRecord(id=str(uuid4()), project_id=project_id, task_id=task_id, export_type=export_type, task_name=task_name, root_dir=root_dir, file_list=file_list)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_export_records(db: Session):
    return db.query(ExportRecord).order_by(ExportRecord.created_at.desc()).all()
