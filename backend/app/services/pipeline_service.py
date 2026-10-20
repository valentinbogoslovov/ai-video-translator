import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.pipeline_run import PipelineRun
from app.models.stage_result import StageResult
from app.models.project import Project

STAGE_ORDER = ["ingest", "separate", "transcribe", "diarize", "align", "dub", "mix"]


def start_pipeline(db: Session, project_id: str) -> PipelineRun:
    """Start a full pipeline run from ingest."""
    return _create_run(db, project_id, trigger="initial", from_stage="ingest")


def restart_pipeline(db: Session, project_id: str, from_stage: str) -> PipelineRun:
    """Restart pipeline from a specific stage (correction run)."""
    if from_stage not in STAGE_ORDER:
        raise ValueError(f"Invalid stage: {from_stage}. Must be one of {STAGE_ORDER}")
    return _create_run(db, project_id, trigger="correction", from_stage=from_stage)


def _create_run(db: Session, project_id: str, trigger: str, from_stage: str) -> PipelineRun:
    # Determine run number
    last_run = (
        db.query(PipelineRun)
        .filter(PipelineRun.project_id == project_id)
        .order_by(PipelineRun.run_number.desc())
        .first()
    )
    run_number = (last_run.run_number + 1) if last_run else 1

    # Get previous run's artifacts for skipped stages
    prev_artifacts: dict[str, str | None] = {}
    if last_run and trigger == "correction":
        for sr in last_run.stage_results:
            if sr.status == "completed":
                prev_artifacts[sr.stage] = sr.artifacts

    run = PipelineRun(
        project_id=project_id,
        run_number=run_number,
        trigger=trigger,
        start_stage=from_stage,
    )
    db.add(run)
    db.flush()

    start_idx = STAGE_ORDER.index(from_stage)
    for i, stage in enumerate(STAGE_ORDER):
        if i < start_idx:
            sr = StageResult(
                pipeline_run_id=run.id,
                stage=stage,
                status="skipped",
                artifacts=prev_artifacts.get(stage),
            )
        else:
            sr = StageResult(pipeline_run_id=run.id, stage=stage, status="pending")
        db.add(sr)

    # Update project status
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        project.status = "running"

    db.commit()
    db.refresh(run)
    return run


def update_stage_status(
    db: Session, run_id: str, stage: str, status: str, artifacts: dict | None = None, error: str | None = None
) -> StageResult | None:
    sr = (
        db.query(StageResult)
        .filter(StageResult.pipeline_run_id == run_id, StageResult.stage == stage)
        .first()
    )
    if not sr:
        return None

    sr.status = status
    now = datetime.now(timezone.utc)
    if status == "running":
        sr.started_at = now
    elif status in ("completed", "failed"):
        sr.finished_at = now
    if artifacts is not None:
        sr.artifacts = json.dumps(artifacts)
    if error:
        sr.error_detail = error

    db.commit()
    db.refresh(sr)
    return sr


def complete_run(db: Session, run_id: str, success: bool = True) -> None:
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        return
    run.status = "completed" if success else "failed"
    run.finished_at = datetime.now(timezone.utc)

    project = db.query(Project).filter(Project.id == run.project_id).first()
    if project:
        project.status = "awaiting_review" if success else "error"

    db.commit()


def get_pipeline_status(db: Session, project_id: str) -> dict:
    runs = (
        db.query(PipelineRun)
        .filter(PipelineRun.project_id == project_id)
        .order_by(PipelineRun.run_number.desc())
        .all()
    )
    current = runs[0] if runs and runs[0].status == "running" else None
    return {"current_run": current, "runs": runs}
