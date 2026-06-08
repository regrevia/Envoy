from pydantic import BaseModel
from typing import Optional


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    domain_type: Optional[str] = None
    status: Optional[str] = "active"


class ProjectRead(ProjectCreate):
    id: str
    user_id: Optional[str] = None
