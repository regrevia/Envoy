from pydantic import BaseModel, Field, HttpUrl


class VideoBrowserSessionCreate(BaseModel):
    source_url: HttpUrl
    limit: int = Field(default=100, ge=1, le=100)
