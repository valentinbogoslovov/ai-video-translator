from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.project import Project
from app.schemas.pipeline import PipelineRestart, PipelineRunOut
from app.services import pipeline_service, project_service
from app.worker.runner import submit_task
from app.worker.tasks import run_pipeline

router = APIRouter(tags=["review"])


@router.post("/projects/{project_id}/review/approve")
def approve_review(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "awaiting_review":
        raise HTTPException(status_code=400, detail=f"Cannot approve project in '{project.status}' state")

    project.status = "approved"
    db.commit()
    return {"ok": True, "status": "approved"}


@router.post("/projects/{project_id}/review/reject", response_model=PipelineRunOut)
def reject_review(project_id: str, body: PipelineRestart, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "awaiting_review":
        raise HTTPException(status_code=400, detail=f"Cannot reject project in '{project.status}' state")

    try:
        run = pipeline_service.restart_pipeline(db, project_id, body.from_stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    submit_task(f"pipeline-{project_id}", run_pipeline, project_id, run.id)
    return run
