from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ClipProcessingStatus, Course, Participant, VideoClip
from ..schemas import ClipOut, GalleryOut, IssueFlag, ParticipantOut
from ..services import clip_picker

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


def _get_participant(db: Session, token: str) -> Participant:
    p = db.query(Participant).filter(Participant.gallery_token == token).first()
    if not p:
        raise HTTPException(404, "gallery not found")
    return p


@router.get("/{gallery_token}", response_model=GalleryOut)
def get_gallery(gallery_token: str, db: Session = Depends(get_db)):
    participant = _get_participant(db, gallery_token)
    course = db.get(Course, participant.tee_time.course_id)
    clips = (
        db.query(VideoClip)
        .filter(
            VideoClip.participant_id == participant.id,
            VideoClip.processing_status == ClipProcessingStatus.assigned.value,
        )
        .order_by(VideoClip.hole_number.asc(), VideoClip.captured_at.asc())
        .all()
    )
    return GalleryOut(
        participant=ParticipantOut.model_validate(participant),
        course_name=course.name if course else "",
        livestream_url=course.livestream_url if course else None,
        hole_yardages=(course.hole_yardages or {}) if course else {},
        clips=[ClipOut.model_validate(c) for c in clips],
    )


@router.post("/{gallery_token}/clips/{clip_id}/flag", response_model=ClipOut)
def flag_clip(gallery_token: str, clip_id: int, payload: IssueFlag, db: Session = Depends(get_db)):
    participant = _get_participant(db, gallery_token)
    clip = db.get(VideoClip, clip_id)
    if not clip or clip.participant_id != participant.id:
        raise HTTPException(404, "clip not found for this gallery")
    clip.processing_status = ClipProcessingStatus.flagged.value
    clip.issue_note = f"[golfer flag] {payload.note.strip()}"
    db.commit()
    db.refresh(clip)
    return ClipOut.model_validate(clip)


# ---------------------------------------------------------------------------
# Picking your own shots
# ---------------------------------------------------------------------------
# Nothing assigns a swing to a person automatically, so the golfer does
# it. See services/clip_picker.py for why the candidate list is cut on
# the gaps between groups rather than by a window around the tee time.


def _candidate_json(clip: VideoClip) -> dict:
    return {
        "id": clip.id,
        "hole_number": clip.hole_number,
        "captured_at": clip.captured_at.isoformat() if clip.captured_at else None,
        "preview_url": clip_picker.preview_url(clip),
        "thumbnail_url": clip.thumbnail_url,
        "vertical": bool(clip.vertical_url),
        "distance_from_pin_feet": clip.distance_from_pin_feet,
    }


@router.get("/{gallery_token}/candidates")
def list_candidates(gallery_token: str, offset: int = 0,
                    db: Session = Depends(get_db)):
    """The group of shots this golfer most likely played in.

    `offset` steps through the ranking when the first group is not
    theirs -- the pace-of-play problem solved by a tap instead of by a
    better guess at when they reached the hole.
    """
    participant = _get_participant(db, gallery_token)
    found = clip_picker.candidates(db, participant, offset=offset)
    return {
        "clips": [_candidate_json(c) for c in found["clips"]],
        "group_index": found["group_index"],
        "group_count": found["group_count"],
        "has_more": found["has_more"],
        "total_unclaimed": found["total_unclaimed"],
    }


@router.post("/{gallery_token}/claim")
def claim_clips(gallery_token: str, payload: dict = Body(...),
                db: Session = Depends(get_db)):
    """Take these shots as mine.

    ONLY UNCLAIMED CLIPS, re-checked here rather than trusted from the
    list the browser was holding. Two golfers in the same group can have
    the same clip on screen at once, and the second tap must lose
    cleanly instead of moving the clip out of the first one's gallery.
    """
    participant = _get_participant(db, gallery_token)
    ids = payload.get("clip_ids") or []
    try:
        ids = [int(i) for i in ids][:20]
    except (TypeError, ValueError):
        raise HTTPException(400, "clip_ids must be numbers")
    if not ids:
        raise HTTPException(400, "no clips given")

    return clip_picker.claim(db, participant, ids)


@router.post("/{gallery_token}/unclaim")
def unclaim_clip(gallery_token: str, payload: dict = Body(...),
                 db: Session = Depends(get_db)):
    """Not me after all -- put it back for whoever did hit it.

    Worth having: a wrong tap that cannot be undone is a clip nobody can
    ever claim, and the golfer who did hit it has no way to ask for it.
    """
    participant = _get_participant(db, gallery_token)
    try:
        clip_id = int(payload.get("clip_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "clip_id must be a number")
    if not clip_picker.unclaim(db, participant, clip_id):
        raise HTTPException(404, "clip not found in this gallery")
    return {"unclaimed": clip_id}
