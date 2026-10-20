from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.project import Project
from app.services import project_service
from app.services.storage import storage_service

router = APIRouter(tags=["export"])


@router.post("/projects/{project_id}/export")
def trigger_export(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in ("approved", "exported"):
        raise HTTPException(status_code=400, detail=f"Cannot export project in '{project.status}' state")

    # TODO: Trigger the export stage as a background task
    # For now, mark as exporting
    project.status = "exporting"
    db.commit()
    return {"ok": True, "status": "exporting"}


@router.get("/projects/{project_id}/export/download")
def download_export(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    export_path = storage_service.export_dir(project_id) / "final.mp4"
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="Export not ready")

    return FileResponse(
        path=str(export_path),
        media_type="video/mp4",
        filename=f"{project.name}_dubbed.mp4",
    )
