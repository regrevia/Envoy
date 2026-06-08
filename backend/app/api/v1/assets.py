from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.asset import AssetCreate
from app.services.crud_service import create_asset, list_assets
from app.services.minio_service import upload_to_storage

router = APIRouter()


@router.get("/assets")
def list_assets_api(db: Session = Depends(get_db)):
    return [{"id": a.id, "project_id": a.project_id, "asset_type": a.asset_type, "file_name": a.file_name, "file_path": a.file_path} for a in list_assets(db)]


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
    return {"storage": storage_info, "asset": {"id": asset.id, "project_id": asset.project_id, "asset_type": asset.asset_type, "file_name": asset.file_name, "file_path": asset.file_path}}
