from sqlalchemy import String, Text, DateTime, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.db.base import Base


class SemanticUnit(Base):
    __tablename__ = "semantic_units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    segment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    unit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
