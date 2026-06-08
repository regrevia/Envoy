from app.schemas.project import ProjectCreate


def create_project_service(payload: ProjectCreate):
    return payload.model_dump()


def list_projects_service():
    return []
