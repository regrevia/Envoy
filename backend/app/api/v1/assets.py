from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.asset import AssetCreate
from app.services.crud_service import create_asset, get_project, list_assets
from app.services.minio_service import upload_to_storage

router = APIRouter()


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
    mime_type: str | None = Form(None),
    duration_sec: int | None = Form(None),
    language: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    file_bytes = await file.read()
    storage_info = upload_to_storage(file.filename, file_bytes, file.content_type or "application/octet-stream")
    payload = AssetCreate(
        project_id=project_id,
        asset_type=asset_type,
        file_name=file.filename,
        file_path=storage_info["object_name"],
        mime_type=mime_type or file.content_type,
        duration_sec=duration_sec,
        language=language,
        metadata={"original_name": file.filename, "storage": storage_info.get("storage"), "storage_reason": storage_info.get("reason")},
    )
    asset = create_asset(db, payload)
    return {"storage": storage_info, "asset": _asset_payload(asset)}
