from pydantic import BaseModel
from typing import Optional, Any


class AssetCreate(BaseModel):
    project_id: str
    asset_type: str
    file_name: str
    file_path: str
    mime_type: Optional[str] = None
    duration_sec: Optional[int] = None
    language: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class AssetRead(AssetCreate):
    id: str
