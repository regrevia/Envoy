from pydantic import BaseModel
from typing import Optional


class ArtifactCreate(BaseModel):
    project_id: str
    artifact_type: str
    name: str
    file_path: str
    version: Optional[str] = None


class ArtifactRead(ArtifactCreate):
    id: str
