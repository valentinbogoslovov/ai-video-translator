from pydantic import BaseModel


class SegmentOut(BaseModel):
    id: str
    project_id: str
    index: int
    start_ms: int
    end_ms: int
    speaker_id: str | None
    original_text: str | None
    translated_text: str | None
    dub_audio_path: str | None
    locked: bool

    model_config = {"from_attributes": True}


class SegmentUpdate(BaseModel):
    start_ms: int | None = None
    end_ms: int | None = None
    speaker_id: str | None = None
    original_text: str | None = None
    translated_text: str | None = None
    locked: bool | None = None
