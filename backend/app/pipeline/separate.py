from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.services.storage import storage_service


class SeparateStage(PipelineStage):
    """Separate dialogue from background audio (vocals vs accompaniment)."""

    name = "separate"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        audio_path = previous_artifacts.get("audio_path")
        if not audio_path:
            raise ValueError("No audio_path from ingest stage")

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)

        # TODO: Use demucs or similar for source separation
        vocals_path = stage_dir / "vocals.wav"
        vocals_path.touch()
        accompaniment_path = stage_dir / "accompaniment.wav"
        accompaniment_path.touch()

        return {
            "vocals_path": str(vocals_path),
            "accompaniment_path": str(accompaniment_path),
        }
