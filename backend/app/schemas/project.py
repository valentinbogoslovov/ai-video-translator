from datetime import datetime

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    source_lang: str = "en"
    target_lang: str = "es"
    script_text: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    script_text: str | None = None
    source_lang: str | None = None
    target_lang: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    status: str
    source_video_path: str | None
    script_text: str | None
    source_lang: str
    target_lang: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
