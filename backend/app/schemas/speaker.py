from pydantic import BaseModel


class SpeakerOut(BaseModel):
    id: str
    project_id: str
    label: str
    voice_id: str | None
    color: str

    model_config = {"from_attributes": True}


class SpeakerUpdate(BaseModel):
    label: str | None = None
    voice_id: str | None = None
    color: str | None = None
