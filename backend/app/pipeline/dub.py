from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.base import PipelineStage
from app.models.segment import Segment
from app.services.storage import storage_service


class DubStage(PipelineStage):
    """Generate dubbed voice audio for each segment using TTS."""

    name = "dub"

    def execute(self, db: Session, project_id: str, run_id: str, previous_artifacts: dict[str, Any]) -> dict[str, Any]:
        segments = (
            db.query(Segment)
            .filter(Segment.project_id == project_id, Segment.locked == False)  # noqa: E712
            .order_by(Segment.index)
            .all()
        )

        stage_dir = storage_service.run_stage_dir(project_id, run_id, self.name)
        dub_files = []

        for seg in segments:
            if not seg.translated_text:
                continue

            # TODO: Use TTS engine (see services/providers.py)
            # - Select voice based on seg.speaker.voice_id
            # - Generate speech for seg.translated_text
            # - Ensure audio fits within seg.start_ms to seg.end_ms timing
            dub_path = stage_dir / f"segment_{seg.index:04d}.wav"
            dub_path.touch()

            seg.dub_audio_path = str(dub_path)
            dub_files.append(str(dub_path))

        db.commit()
        return {"dub_files": dub_files}
