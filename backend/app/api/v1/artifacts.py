from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.artifact import ArtifactCreate
from app.services.crud_service import create_artifact, list_artifacts
from app.models.artifact import Artifact

router = APIRouter()


@router.get("/artifacts")
def list_artifacts_api(db: Session = Depends(get_db)):
    return [{"id": a.id, "artifact_type": a.artifact_type, "name": a.name, "file_path": a.file_path, "version": a.version} for a in list_artifacts(db)]


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "artifact_type": artifact.artifact_type,
        "name": artifact.name,
        "file_path": artifact.file_path,
        "version": artifact.version,
        "created_at": artifact.created_at,
    }


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)):
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = Path(artifact.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(path=str(path), filename=artifact.name)


@router.post("/artifacts")
def create_artifact_api(payload: ArtifactCreate, db: Session = Depends(get_db)):
    artifact = create_artifact(db, payload)
    return {"id": artifact.id, "artifact_type": artifact.artifact_type, "name": artifact.name, "file_path": artifact.file_path, "version": artifact.version}
