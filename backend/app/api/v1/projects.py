from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.project import ProjectCreate
from app.services.crud_service import create_project, list_projects, get_project

router = APIRouter()


def _project_payload(project):
    return {
        "id": project.id,
        "user_id": project.user_id,
        "name": project.name,
        "description": project.description,
        "domain_type": project.domain_type,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


@router.get("/projects")
def list_projects_api(db: Session = Depends(get_db)):
    return [_project_payload(project) for project in list_projects(db)]


@router.post("/projects")
def create_project_api(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = create_project(db, payload)
    return _project_payload(project)


@router.get("/projects/{project_id}")
def get_project_api(project_id: str, db: Session = Depends(get_db)):
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_payload(project)
