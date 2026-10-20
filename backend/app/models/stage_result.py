import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class StageResult(Base):
    __tablename__ = "stage_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(20))
    # ingest | separate | transcribe | diarize | align | dub | mix
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | running | completed | failed | skipped
    artifacts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    pipeline_run: Mapped["PipelineRun"] = relationship(back_populates="stage_results")  # type: ignore[name-defined]  # noqa: F821
