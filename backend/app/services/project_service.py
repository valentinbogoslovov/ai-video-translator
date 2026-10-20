from sqlalchemy.orm import Session

from app.models.project import Project
from app.services.storage import storage_service


def create_project(db: Session, name: str, source_lang: str, target_lang: str, script_text: str | None = None) -> Project:
    project = Project(name=name, source_lang=source_lang, target_lang=target_lang, script_text=script_text)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get_project(db: Session, project_id: str) -> Project | None:
    return db.query(Project).filter(Project.id == project_id).first()


def list_projects(db: Session) -> list[Project]:
    return db.query(Project).order_by(Project.created_at.desc()).all()


def delete_project(db: Session, project_id: str) -> bool:
    project = get_project(db, project_id)
    if not project:
        return False
    storage_service.delete_project(project_id)
    db.delete(project)
    db.commit()
    return True
