from pydantic import BaseModel
from typing import Optional, Any


class TrainExportRequest(BaseModel):
    project_id: Optional[str] = None
    export_type: str
    payload: Optional[list[dict[str, Any]]] = None
    out_dir: Optional[str] = None
    title: Optional[str] = None
    source_task_id: Optional[str] = None
    source_asset_id: Optional[str] = None
    task_name: Optional[str] = None
