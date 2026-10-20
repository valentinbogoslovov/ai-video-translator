import json
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.services.storage import storage_service


class TranscribeStage(PipelineStage):
    """Generate word-level timestamps from vocals track."""

    name = "transcribe"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        vocals_path = previous_artifacts.get("vocals_path")
        if not vocals_path:
            raise ValueError("No vocals_path from separate stage")

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)

        # TODO: Use whisper / faster-whisper for transcription
        transcript = {
            "segments": [
                # Example structure:
                # {
                #     "id": 0,
                #     "start": 0.0,
                #     "end": 3.5,
                #     "text": "Hello, welcome.",
                #     "words": [
                #         {"word": "Hello,", "start": 0.0, "end": 0.8},
                #         {"word": "welcome.", "start": 1.0, "end": 3.5},
                #     ]
                # }
            ]
        }
        transcript_path = stage_dir / "transcript.json"
        transcript_path.write_text(json.dumps(transcript, indent=2))

        return {"transcript_path": str(transcript_path)}
