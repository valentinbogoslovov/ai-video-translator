import shutil

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.deps import get_db
from app.schemas.project import ProjectOut
from app.services import project_service
from app.services.storage import storage_service

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectOut)
def create_project(
    name: str = Form(...),
    source_lang: str = Form("en"),
    target_lang: str = Form("es"),
    script_text: str = Form(None),
    video: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    project = project_service.create_project(db, name, source_lang, target_lang, script_text)

    if video and video.filename:
        source_dir = storage_service.source_dir(project.id)
        video_path = source_dir / video.filename
        with open(video_path, "wb") as f:
            shutil.copyfileobj(video.file, f)
        project.source_video_path = str(video_path)
        db.commit()
        db.refresh(project)

    return project


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return project_service.list_projects(db)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    if not project_service.delete_project(db, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}
