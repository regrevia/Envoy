from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MixedLibraryCreate(BaseModel):
    project_id: str
    asset_ids: list[str] = Field(min_length=1, max_length=50)
    title: str = "混合素材知识库"
    library_id: Optional[str] = None
    max_parallel: int = Field(default=2, ge=1, le=8)
    markdown_enhance_with_ollama: bool = False
    input_config: dict[str, Any] = Field(default_factory=dict)


class MixedLibraryAppend(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=50)
    title: Optional[str] = None
    max_parallel: int = Field(default=2, ge=1, le=8)
    markdown_enhance_with_ollama: bool = False
