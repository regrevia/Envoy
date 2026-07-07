from __future__ import annotations

from typing import Optional
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.db.base import Base


class ExportRecord(Base):
    __tablename__ = "export_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    export_type: Mapped[str] = mapped_column(String(50), nullable=False)
    task_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    root_dir: Mapped[str] = mapped_column(Text, nullable=False)
    file_list: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
