from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db
from app.schemas.pipeline import PipelineRestart, PipelineRunOut, PipelineStatusOut
from app.services import pipeline_service, project_service
from app.worker.runner import submit_task, is_task_running
from app.worker.tasks import run_pipeline

router = APIRouter(tags=["pipeline"])


@router.post("/projects/{project_id}/pipeline/start", response_model=PipelineRunOut)
def start_pipeline(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    task_id = f"pipeline-{project_id}"
    if is_task_running(task_id):
        raise HTTPException(status_code=409, detail="Pipeline already running")

    run = pipeline_service.start_pipeline(db, project_id)
    submit_task(task_id, run_pipeline, project_id, run.id)
    return run


@router.post("/projects/{project_id}/pipeline/restart", response_model=PipelineRunOut)
def restart_pipeline(project_id: str, body: PipelineRestart, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    task_id = f"pipeline-{project_id}"
    if is_task_running(task_id):
        raise HTTPException(status_code=409, detail="Pipeline already running")

    try:
        run = pipeline_service.restart_pipeline(db, project_id, body.from_stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    submit_task(task_id, run_pipeline, project_id, run.id)
    return run


@router.get("/projects/{project_id}/pipeline/status", response_model=PipelineStatusOut)
def pipeline_status(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    status = pipeline_service.get_pipeline_status(db, project_id)
    return status
