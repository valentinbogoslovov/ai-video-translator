import json
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.models.project import Project
from app.services.storage import storage_service


class IngestStage(PipelineStage):
    """Extract audio from video, detect metadata, normalize audio format."""

    name = "ingest"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not project.source_video_path:
            raise ValueError("Project has no source video")

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)

        # TODO: Use ffmpeg to extract audio and probe metadata
        # For now, create stub outputs
        audio_path = stage_dir / "full_audio.wav"
        audio_path.touch()

        metadata = {
            "duration_ms": 0,
            "sample_rate": 44100,
            "channels": 2,
            "source_video": project.source_video_path,
        }
        metadata_path = stage_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata))

        return {
            "audio_path": str(audio_path),
            "metadata_path": str(metadata_path),
        }
