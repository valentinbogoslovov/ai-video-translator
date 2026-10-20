from datetime import datetime

from pydantic import BaseModel


class PipelineStart(BaseModel):
    pass  # no params needed for initial run


class PipelineRestart(BaseModel):
    from_stage: str  # e.g. "dub", "transcribe"


class StageResultOut(BaseModel):
    id: str
    stage: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_detail: str | None

    model_config = {"from_attributes": True}


class PipelineRunOut(BaseModel):
    id: str
    project_id: str
    run_number: int
    trigger: str
    start_stage: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stage_results: list[StageResultOut] = []

    model_config = {"from_attributes": True}


class PipelineStatusOut(BaseModel):
    current_run: PipelineRunOut | None
    runs: list[PipelineRunOut]
