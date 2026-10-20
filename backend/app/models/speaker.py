import uuid

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(100), default="Speaker 1")
    voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    color: Mapped[str] = mapped_column(String(7), default="#3B82F6")

    project: Mapped["Project"] = relationship(back_populates="speakers")  # type: ignore[name-defined]  # noqa: F821
    segments: Mapped[list["Segment"]] = relationship(back_populates="speaker")  # type: ignore[name-defined]  # noqa: F821
