import uuid

from sqlalchemy import String, Integer, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("speakers.id"), nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dub_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_run: Mapped[str | None] = mapped_column(String(36), ForeignKey("pipeline_runs.id"), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="segments")  # type: ignore[name-defined]  # noqa: F821
    speaker: Mapped["Speaker | None"] = relationship(back_populates="segments")  # type: ignore[name-defined]  # noqa: F821
