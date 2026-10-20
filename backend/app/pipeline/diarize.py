import json
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.services.storage import storage_service


class DiarizeStage(PipelineStage):
    """Identify speakers and label segments with speaker IDs."""

    name = "diarize"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        vocals_path = previous_artifacts.get("vocals_path")
        if not vocals_path:
            raise ValueError("No vocals_path from separate stage")

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)

        # TODO: Use pyannote.audio for speaker diarization
        diarization = {
            "speakers": [],
            # Example:
            # [
            #     {"speaker_id": "spk_0", "start": 0.0, "end": 3.5},
            #     {"speaker_id": "spk_1", "start": 4.0, "end": 8.2},
            # ]
        }
        diarization_path = stage_dir / "diarization.json"
        diarization_path.write_text(json.dumps(diarization, indent=2))

        return {"diarization_path": str(diarization_path)}
