from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.services.storage import storage_service


class MixStage(PipelineStage):
    """Mix dubbed segments with background audio stem."""

    name = "mix"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        accompaniment_path = previous_artifacts.get("accompaniment_path")
        dub_files = previous_artifacts.get("dub_files", [])

        if not accompaniment_path:
            raise ValueError("No accompaniment_path from separate stage")

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)

        # TODO: Use pydub/soundfile to:
        # 1. Load accompaniment track
        # 2. Place each dub segment at its correct timeline position
        # 3. Mix together with level adjustments
        # 4. Export final mixed audio
        dubbed_audio_path = stage_dir / "dubbed_audio.wav"
        dubbed_audio_path.touch()

        return {"dubbed_audio_path": str(dubbed_audio_path)}
