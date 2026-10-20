import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    # draft | running | awaiting_review | approved | exporting | exported | error
    source_video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_lang: Mapped[str] = mapped_column(String(10), default="en")
    target_lang: Mapped[str] = mapped_column(String(10), default="es")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    pipeline_runs: Mapped[list["PipelineRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")  # type: ignore[name-defined]  # noqa: F821
    speakers: Mapped[list["Speaker"]] = relationship(back_populates="project", cascade="all, delete-orphan")  # type: ignore[name-defined]  # noqa: F821
    segments: Mapped[list["Segment"]] = relationship(back_populates="project", cascade="all, delete-orphan")  # type: ignore[name-defined]  # noqa: F821
