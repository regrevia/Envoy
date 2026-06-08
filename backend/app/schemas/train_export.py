from pydantic import BaseModel
from typing import Optional, Literal, Any


class TrainExportRequest(BaseModel):
    export_type: Literal['sft', 'instruction', 'preference', 'rag', 'skill', 'markdown']
    payload: Optional[list[dict[str, Any]]] = None
    out_dir: Optional[str] = None
    title: Optional[str] = None
    source_task_id: Optional[str] = None
    source_asset_id: Optional[str] = None
    task_name: Optional[str] = None
