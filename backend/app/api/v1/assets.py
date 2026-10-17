from __future__ import annotations

from pathlib import Path
from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.asset import AssetCreate
from app.services.crud_service import create_asset, get_project, list_assets
from app.services.minio_service import upload_fileobj_to_storage

router = APIRouter()


SUPPORTED_ASSET_TYPES = {"text", "markdown", "document", "image", "audio", "video"}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".mxf", ".ogv", ".ts", ".webm", ".wmv",
}


def _is_supported_video_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS


def _asset_payload(asset):
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "asset_type": asset.asset_type,
        "file_name": asset.file_name,
        "file_path": asset.file_path,
        "mime_type": asset.mime_type,
        "duration_sec": asset.duration_sec,
        "language": asset.language,
        "metadata": asset.extra_metadata or {},
        "created_at": asset.created_at,
    }


@router.get("/assets")
def list_assets_api(db: Session = Depends(get_db)):
    return [_asset_payload(asset) for asset in list_assets(db)]


@router.post("/assets/upload")
async def upload_asset_api(
    project_id: str = Form(...),
    asset_type: str = Form(...),
    file: UploadFile = File(...),
    mime_type: Optional[str] = Form(None),
    duration_sec: Optional[int] = Form(None),
    language: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    normalized_asset_type = asset_type.strip().lower()
    if normalized_asset_type not in SUPPORTED_ASSET_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported asset type")
    if not file.filename or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Uploaded file name is empty")
    if normalized_asset_type == "video" and not _is_supported_video_filename(file.filename):
        supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Unsupported video format; supported extensions: {supported}")

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    storage_name = file.filename
    if normalized_asset_type == "video":
        storage_name = f"{uuid4().hex}{Path(file.filename).suffix.lower()}"
    storage_info = upload_fileobj_to_storage(storage_name, file.file, file_size, file.content_type or "application/octet-stream")
    if storage_info.get("storage") not in {"local", "minio"}:
        raise HTTPException(status_code=503, detail=f"Storage backend unavailable: {storage_info.get('reason') or 'unknown'}")
    payload = AssetCreate(
        project_id=project_id,
        asset_type=normalized_asset_type,
        file_name=file.filename,
        file_path=storage_info["object_name"],
        mime_type=mime_type or file.content_type,
        duration_sec=duration_sec,
        language=language,
        metadata={"original_name": file.filename, "storage": storage_info.get("storage"), "storage_reason": storage_info.get("reason")},
    )
    asset = create_asset(db, payload)
    return {"storage": storage_info, "asset": _asset_payload(asset)}
