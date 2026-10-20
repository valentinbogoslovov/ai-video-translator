from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.segment import Segment
from app.schemas.segment import SegmentOut, SegmentUpdate

router = APIRouter(tags=["segments"])


@router.get("/projects/{project_id}/segments", response_model=list[SegmentOut])
def list_segments(project_id: str, db: Session = Depends(get_db)):
    return db.query(Segment).filter(Segment.project_id == project_id).order_by(Segment.index).all()


@router.patch("/projects/{project_id}/segments/{segment_id}", response_model=SegmentOut)
def update_segment(project_id: str, segment_id: str, body: SegmentUpdate, db: Session = Depends(get_db)):
    seg = db.query(Segment).filter(Segment.id == segment_id, Segment.project_id == project_id).first()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(seg, key, value)

    db.commit()
    db.refresh(seg)
    return seg


@router.post("/projects/{project_id}/segments/{segment_id}/regenerate", response_model=SegmentOut)
def regenerate_segment(project_id: str, segment_id: str, db: Session = Depends(get_db)):
    seg = db.query(Segment).filter(Segment.id == segment_id, Segment.project_id == project_id).first()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    # TODO: Re-run TTS for this single segment
    # For now, just clear the dub path to indicate it needs regeneration
    seg.dub_audio_path = None
    db.commit()
    db.refresh(seg)
    return seg
