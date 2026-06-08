from sqlalchemy import String, Text, DateTime, Float
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.db.base import Base


class GeneratedSample(Base):
    __tablename__ = "generated_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sample_type: Mapped[str] = mapped_column(String(50), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_text: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
