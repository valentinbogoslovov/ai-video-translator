from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite needs this
)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


# Import all models so they register with Base.metadata
from app.models.project import Project  # noqa: E402, F401
from app.models.pipeline_run import PipelineRun  # noqa: E402, F401
from app.models.stage_result import StageResult  # noqa: E402, F401
from app.models.speaker import Speaker  # noqa: E402, F401
from app.models.segment import Segment  # noqa: E402, F401


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
