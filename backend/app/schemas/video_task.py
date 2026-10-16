from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class VideoToMarkdownCreate(BaseModel):
    project_id: str
    source_url: str
    title: str = ""
    auth: Optional[dict[str, Any]] = None
    input_config: dict[str, Any] = Field(default_factory=dict)
