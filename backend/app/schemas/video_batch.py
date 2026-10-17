from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator


class VideoPlaylistDiscover(BaseModel):
    source_url: HttpUrl
    limit: int = Field(default=100, ge=1, le=100)


class VideoBatchItem(BaseModel):
    source_url: Optional[HttpUrl] = None
    asset_id: Optional[str] = None
    title: str = ""

    @model_validator(mode="after")
    def require_one_source(self):
        if bool(self.source_url) == bool(self.asset_id):
            raise ValueError("Exactly one of source_url or asset_id is required")
        return self


class VideoBatchCreate(BaseModel):
    project_id: str
    items: list[VideoBatchItem] = Field(min_length=1, max_length=20)
    library_id: Optional[str] = None
    title: str = "视频知识库"
    input_config: dict[str, Any] = Field(default_factory=dict)


class VideoBatchAppend(VideoBatchCreate):
    project_id: Optional[str] = None
    library_id: Optional[str] = None
    title: Optional[str] = None
    source_task_id: Optional[str] = None
