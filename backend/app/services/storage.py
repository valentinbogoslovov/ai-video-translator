import shutil
from pathlib import Path

from app.config import settings


class StorageService:
    """Local filesystem storage for project artifacts.

    Layout:
        storage/{project_id}/source/video.mp4
        storage/{project_id}/runs/{run_id}/{stage}/...
        storage/{project_id}/export/final.mp4
    """

    def __init__(self) -> None:
        self.root = settings.storage_path

    def project_dir(self, project_id: str) -> Path:
        p = self.root / project_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def source_dir(self, project_id: str) -> Path:
        p = self.project_dir(project_id) / "source"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def run_stage_dir(self, project_id: str, run_id: str, stage: str) -> Path:
        p = self.project_dir(project_id) / "runs" / run_id / stage
        p.mkdir(parents=True, exist_ok=True)
        return p

    def export_dir(self, project_id: str) -> Path:
        p = self.project_dir(project_id) / "export"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def delete_project(self, project_id: str) -> None:
        p = self.project_dir(project_id)
        if p.exists():
            shutil.rmtree(p)


storage_service = StorageService()
