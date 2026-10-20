from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.models.project import Project
from app.services.storage import storage_service


class ExportStage(PipelineStage):
    """Mux final dubbed audio with original video to produce output file."""

    name = "export"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        dubbed_audio_path = previous_artifacts.get("dubbed_audio_path")
        project = db.query(Project).filter(Project.id == project_id).first()

        if not dubbed_audio_path or not project or not project.source_video_path:
            raise ValueError("Missing dubbed audio or source video")

        export_dir = storage_service.export_dir(project_id)

        # TODO: Use ffmpeg to:
        # 1. Take original video (for video stream)
        # 2. Replace audio with dubbed_audio.wav
        # 3. Export as final.mp4
        final_path = export_dir / "final.mp4"
        final_path.touch()

        project.status = "exported"
        db.commit()

        return {"final_video_path": str(final_path)}
