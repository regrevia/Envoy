from pydantic import BaseModel
from typing import Optional, Any


class TaskCreate(BaseModel):
    project_id: str
    asset_id: Optional[str] = None
    task_type: str
    input_config: Optional[dict[str, Any]] = None


class TaskRead(TaskCreate):
    id: str
    status: str
    progress: int
