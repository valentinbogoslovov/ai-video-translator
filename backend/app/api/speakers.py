from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.speaker import Speaker
from app.schemas.speaker import SpeakerOut, SpeakerUpdate

router = APIRouter(tags=["speakers"])


@router.get("/projects/{project_id}/speakers", response_model=list[SpeakerOut])
def list_speakers(project_id: str, db: Session = Depends(get_db)):
    return db.query(Speaker).filter(Speaker.project_id == project_id).all()


@router.patch("/projects/{project_id}/speakers/{speaker_id}", response_model=SpeakerOut)
def update_speaker(project_id: str, speaker_id: str, body: SpeakerUpdate, db: Session = Depends(get_db)):
    speaker = db.query(Speaker).filter(Speaker.id == speaker_id, Speaker.project_id == project_id).first()
    if not speaker:
        raise HTTPException(status_code=404, detail="Speaker not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(speaker, key, value)

    db.commit()
    db.refresh(speaker)
    return speaker
