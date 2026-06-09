from pathlib import Path
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.artifact import ArtifactCreate
from app.services.crud_service import create_artifact, list_artifacts
from app.models.artifact import Artifact

router = APIRouter()


MANIFEST_KEYS = {
    "schema_version",
    "source_task_id",
    "task_name",
    "project_id",
    "row_count",
    "ollama_status",
    "ollama_error",
    "llm_summary",
    "llm_usage",
    "llm_keywords",
    "llm_risks",
}


def _manifest_path_for_artifact(artifact: Artifact):
    path = Path(artifact.file_path)
    if path.suffix.lower() == ".json" and path.is_file():
        return path
    candidates = [
        path.with_name("skill.json"),
        path.with_name("skill_manifest.json"),
        path.with_name("markdown_manifest.json"),
        path.with_name("rag_manifest.json"),
        path.with_name(f"{path.stem}_manifest.json"),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _artifact_manifest_payload(artifact: Artifact):
    manifest_path = _manifest_path_for_artifact(artifact)
    if manifest_path is None:
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {
            "manifest_path": str(manifest_path),
            "read_status": "failed",
        }
    if not isinstance(data, dict):
        return {
            "manifest_path": str(manifest_path),
            "read_status": "invalid",
        }
    payload = {key: data[key] for key in MANIFEST_KEYS if key in data}
    payload["manifest_path"] = str(manifest_path)
    payload["read_status"] = "success"
    return payload


def _artifact_payload(artifact: Artifact, include_manifest: bool = False):
    payload = {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "artifact_type": artifact.artifact_type,
        "name": artifact.name,
        "file_path": artifact.file_path,
        "version": artifact.version,
        "created_at": artifact.created_at,
    }
    if include_manifest:
        payload["manifest"] = _artifact_manifest_payload(artifact)
    return payload


@router.get("/artifacts")
def list_artifacts_api(db: Session = Depends(get_db)):
    return [_artifact_payload(artifact) for artifact in list_artifacts(db)]


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _artifact_payload(artifact, include_manifest=True)


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
    return _artifact_payload(artifact)
