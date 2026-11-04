"""Pairing a shot with the golfer who hit it, by hand.

NOTHING DOES THIS AUTOMATICALLY. services/appearance.py ships a stub
embedder -- embed_image_bytes hashes the image bytes rather than looking
at the picture, `embedding_provider` defaults to "stub" and is not set in
render.yaml -- so matcher.py falls through to _round_robin_pick and deals
clips out in rotation. The outfit photo collected at registration is
stored and read by nobody. Until a real embedder is wired in, the only
honest way to put a name on a swing is for a person to look at both.

This is the operator's half of that. The golfer's half is clip_picker.py,
which shows a golfer a short list and lets them tap their own; the two
are the same job approached from opposite ends and they share the same
notion of what a clip looks like, so the preview that plays here is
clip_picker.preview_url -- one definition, not two that can drift.

WHY PLAYER-FIRST. /flagged-clips already walks clips and offers candidate
golfers underneath each one. That is the wrong way round for someone
standing at the course: they have the day's roster in front of them, they
know who went out, and the question they can actually answer is "which of
these shots is Alex's", not "who is this". So the entry point here is the
roster, and the clips hang off a chosen player.

A DAY IS NOT A SERVER-SIDE IDEA. Every datetime in this database is naive
UTC (see frontend/src/time.js), and Course carries no timezone, so "that
day" cannot be worked out here without guessing -- a UTC calendar day
runs from 8pm the previous evening in Eastern time, which would put a
late tee time on the wrong page. The caller therefore sends an explicit
[start, end) in UTC, computed from the operator's own local midnight, the
same conversion Register.jsx already does on the way in. Wrong guesses
about the day cost the operator the clip they were looking for, so there
are none.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (AuditLog, ClipProcessingStatus, Course, Participant,
                      TeeTime, VideoClip)
from .clip_picker import preview_url


def parse_instant(iso: str, what: str = "instant") -> datetime:
    """An ISO 8601 instant as NAIVE UTC, the way everything here is stored.

    The same conversion as tee_times.parse_tee_time, minus its one-year
    guard: that guard is right for a tee time being booked and wrong for
    a window being looked at, since footage from two seasons ago is a
    perfectly ordinary thing to go back to.
    """
    raw = (iso or "").strip()
    if not raw:
        raise ValueError(f"a {what} is required")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"could not read the {what} {raw!r}")
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def unidentified_counts(db: Session, start: datetime, end: datetime) -> list[dict]:
    """Every course, with how many of ONE DAY's clips nobody owns.

    Scoped to the day rather than to all of history because the pile is
    worked off daily: an all-time total counts last month's footage,
    which nobody is going to go back and pair, and buries the number that
    says whether today is done. The window is the same [start, end) the
    roster uses, so the dropdown and the page under it cannot disagree.

    Counted on `participant_id IS NULL` rather than on a status, because
    the status is a processing state and ownership is the question being
    asked -- a clip can be `processed` and still belong to nobody.
    """
    if end <= start:
        raise ValueError("the end of the day must come after its start")
    counts = dict(
        db.query(VideoClip.course_id, func.count(VideoClip.id))
        .filter(
            VideoClip.participant_id.is_(None),
            VideoClip.captured_at >= start,
            VideoClip.captured_at < end,
        )
        .group_by(VideoClip.course_id)
        .all()
    )
    out = []
    for c in db.query(Course).order_by(Course.name).all():
        out.append({
            "id": c.id,
            "name": c.name,
            "location": c.location or "",
            "unidentified": int(counts.get(c.id, 0)),
        })
    return out


def _player_row(p: Participant, assigned: int) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        # The photo is the whole point of the roster view: it is what the
        # operator compares against the footage.
        "selfie_url": f"/uploads/{p.selfie_path}" if p.selfie_path else None,
        "mobile": p.mobile,
        "email": p.email,
        "tee_time": p.tee_time.starts_at if p.tee_time else None,
        "assigned": assigned,
    }


def _clip_row(c: VideoClip) -> dict:
    return {
        "id": c.id,
        "hole_number": c.hole_number,
        "camera_type": c.camera_type,
        "captured_at": c.captured_at,
        "preview_url": preview_url(c),
        "thumbnail_url": c.thumbnail_url,
        "distance_from_pin_feet": c.distance_from_pin_feet,
        "participant_id": c.participant_id,
        "participant_name": c.participant.name if c.participant else None,
    }


def day(db: Session, course_id: int, start: datetime, end: datetime) -> dict:
    """The roster and the footage for one course over one [start, end).

    Both halves in one call because the page needs both at once: the
    roster is useless without knowing how many shots are still loose, and
    the clip list is what every player row is counted against.

    ASSIGNED CLIPS COME BACK TOO, carrying the name they went to. An
    operator who mis-taps needs to see the mistake to undo it, and a list
    that silently drops a clip the moment it is paired gives them no way
    to find it again.
    """
    course = db.get(Course, course_id)
    if course is None:
        raise ValueError("no such course")
    if end <= start:
        raise ValueError("the end of the day must come after its start")

    clips = (
        db.query(VideoClip)
        .filter(
            VideoClip.course_id == course_id,
            VideoClip.captured_at >= start,
            VideoClip.captured_at < end,
        )
        .order_by(VideoClip.captured_at)
        .all()
    )

    # One pass over the day's clips for the per-player tallies, rather
    # than a count query per player.
    tally: dict[int, int] = {}
    for c in clips:
        if c.participant_id is not None:
            tally[c.participant_id] = tally.get(c.participant_id, 0) + 1

    tee_times = (
        db.query(TeeTime)
        .filter(
            TeeTime.course_id == course_id,
            TeeTime.starts_at >= start,
            TeeTime.starts_at < end,
        )
        .order_by(TeeTime.starts_at)
        .all()
    )
    players = []
    for tt in tee_times:
        for p in tt.participants:
            players.append(_player_row(p, tally.get(p.id, 0)))

    return {
        "course": {"id": course.id, "name": course.name},
        "players": players,
        "clips": [_clip_row(c) for c in clips],
        "unidentified": sum(1 for c in clips if c.participant_id is None),
    }


def pair(db: Session, clip_id: int, participant_id: int) -> dict:
    """Put a name on a shot.

    Re-pairing an already-paired clip is allowed and is not an error: it
    is the correction path, and refusing it would leave an operator who
    tapped the wrong row with no way back except the database.
    """
    clip = db.get(VideoClip, clip_id)
    if clip is None:
        raise ValueError("no such clip")
    participant = db.get(Participant, participant_id)
    if participant is None:
        raise ValueError("no such player")
    if clip.course_id != participant.tee_time.course_id:
        raise ValueError("that player did not play that course")

    was = clip.participant_id
    clip.participant_id = participant.id
    clip.processing_status = ClipProcessingStatus.assigned.value
    clip.issue_note = None
    db.add(AuditLog(
        actor="admin",
        action="identify_pair",
        target=f"clip:{clip.id}->p:{participant.id}",
        detail=f"was p:{was}" if was else "was unidentified",
    ))
    db.commit()
    return {"ok": True, "clip_id": clip.id, "participant_id": participant.id}


def unpair(db: Session, clip_id: int) -> dict:
    """Take the name back off, returning the clip to the unidentified pile."""
    clip = db.get(VideoClip, clip_id)
    if clip is None:
        raise ValueError("no such clip")
    was = clip.participant_id
    clip.participant_id = None
    clip.processing_status = ClipProcessingStatus.unassigned.value
    db.add(AuditLog(
        actor="admin",
        action="identify_unpair",
        target=f"clip:{clip.id}",
        detail=f"was p:{was}" if was else "was unidentified",
    ))
    db.commit()
    return {"ok": True, "clip_id": clip.id, "participant_id": None}
