"""The production flow: one recording, its cuts, and what became of them.

FIVE VIEWS OF ONE PIPELINE, which the operator console has never had
in one place:

    raw        a three-minute recording off a camera pair
    cuts       the swing-sized pieces the cutter heard in it, both
               angles, nothing drawn on them
    draft      produced automatically; nobody has looked
    final      an administrator looked and accepted it
    broadcast  it went out

TWO DIFFERENT UNITS, AND THE TABS SAY SO. `raw` and `cuts` count
recordings and cuts; `draft`, `final` and `broadcast` count CLIPS. One
recording can hold four swings and produce four clips, so the numbers
across the strip are not meant to add up and a reader who tries should
be told why rather than left to work it out.

BROADCAST IS NOT A STAGE. A clip can go out from draft or from final,
so it is an independent timestamp. Were it a stage, "final" would stop
meaning "a person approved this" and start meaning "approved or went
out", and neither question could be answered afterwards.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import AudioCut, Course, LongVideoUpload, VideoClip

log = logging.getLogger("golfreelz.flow")

router = APIRouter(prefix="/api/admin/flow", tags=["admin"],
                   dependencies=[Depends(require_admin)])

# How long a cut is kept before retention may sweep it. Two camera
# angles per swing of every round is bulky and most are never used;
# the clips produced FROM them are not touched by this.
CUT_RETENTION_DAYS = 21

STAGES = ("draft", "final")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clip_row(c: VideoClip, course_name=None, cut_idx=None) -> dict:
    return {
        "id": c.id,
        "hole_number": c.hole_number,
        "course_id": c.course_id,
        "course_name": course_name,
        "captured_at": c.captured_at.isoformat() if c.captured_at else None,
        "url": c.source_url,
        "thumbnail_url": c.thumbnail_url,
        "tracer_url": c.tracer_url,
        "vertical_url": c.vertical_url,
        "long_upload_id": c.long_upload_id,
        "audio_cut_id": c.audio_cut_id,
        # WHICH SWING, not which cut row. The id is a database key and
        # means nothing to the operator; "swing 3 of recording 118" is
        # the fact they are after, and it is the whole reason the link
        # exists. None where the produce path never recorded one --
        # every clip produced before this existed, and any whose index
        # join was refused as unsound.
        "cut_idx": cut_idx,
        "stage": c.stage or "draft",
        "accepted_at": c.accepted_at.isoformat() if c.accepted_at else None,
        "accepted_by": c.accepted_by,
        "broadcast_at": (c.broadcast_at.isoformat()
                         if c.broadcast_at else None),
        "participant_id": c.participant_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _cut_row(k: AudioCut, base: str) -> dict:
    def _u(fn):
        return f"{base}/uploads/clips/{fn}" if fn else None
    return {
        "id": k.id, "long_upload_id": k.long_upload_id,
        "idx": k.idx, "hole_number": k.hole_number,
        "peak_time_sec": k.peak_time_sec,
        "start_sec": k.start_sec, "end_sec": k.end_sec,
        "detector": k.detector, "trigger_zone": k.trigger_zone,
        "ratio": k.ratio, "attack_ms": k.attack_ms,
        "tee_url": _u(k.tee_filename), "green_url": _u(k.green_filename),
        "duration_sec": k.duration_sec, "size_mb": k.size_mb,
        "produced": bool(k.produced), "produce_error": k.produce_error,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
    }


@router.get("/counts")
def flow_counts(db: Session = Depends(get_db)):
    """What is in each tab, and in what unit.

    `unit` travels with every number because the strip mixes two of
    them: a recording holding four swings is one row in `raw` and four
    in `draft`. Without it the tabs look like a funnel that loses
    things.
    """
    n_raw = db.query(func.count(LongVideoUpload.id)).scalar() or 0
    n_cuts = db.query(func.count(AudioCut.id)).scalar() or 0
    q = db.query(VideoClip)
    n_draft = q.filter((VideoClip.stage == "draft")
                       | (VideoClip.stage.is_(None))).count()
    n_final = q.filter(VideoClip.stage == "final").count()
    n_bcast = q.filter(VideoClip.broadcast_at.isnot(None)).count()
    return {
        "tabs": [
            {"key": "raw", "label": "Raw recordings", "unit": "recordings",
             "count": n_raw,
             "detail": "what a camera pair uploaded, before anything was cut"},
            {"key": "cuts", "label": "Audio cuts", "unit": "cuts",
             "count": n_cuts,
             "detail": "swing-sized pieces, both angles, nothing drawn on "
                       "them"},
            {"key": "draft", "label": "Draft produced", "unit": "clips",
             "count": n_draft,
             "detail": "produced automatically — nobody has looked yet"},
            {"key": "final", "label": "Final produced", "unit": "clips",
             "count": n_final,
             "detail": "an administrator looked and accepted it"},
            {"key": "broadcast", "label": "Broadcast", "unit": "clips",
             "count": n_bcast,
             "detail": "went out, from draft or from final"},
        ],
        # SAID ONCE, HERE, rather than left for somebody to notice the
        # numbers do not subtract.
        "note": "Recordings and cuts are not clips: one recording can "
                "hold four swings and become four clips, so these "
                "counts are not a funnel and are not meant to add up.",
    }


@router.get("/clips")
def flow_clips(stage: str = "draft", broadcast: str = "",
               course_id: int = 0, limit: int = 60, offset: int = 0,
               db: Session = Depends(get_db)):
    """Clips in one stage. `broadcast` filters on the independent fact."""
    q = db.query(VideoClip)
    if stage == "broadcast":
        q = q.filter(VideoClip.broadcast_at.isnot(None))
    elif stage == "draft":
        # NULL IS DRAFT. Every clip produced before this column existed
        # has one, and treating those as "no stage" would empty the tab
        # that is supposed to hold everything nobody has looked at.
        q = q.filter((VideoClip.stage == "draft")
                     | (VideoClip.stage.is_(None)))
    elif stage in STAGES:
        q = q.filter(VideoClip.stage == stage)
    else:
        raise HTTPException(400, "stage must be draft, final or broadcast")
    if broadcast == "1":
        q = q.filter(VideoClip.broadcast_at.isnot(None))
    elif broadcast == "0":
        q = q.filter(VideoClip.broadcast_at.is_(None))
    if course_id:
        q = q.filter(VideoClip.course_id == course_id)
    rows = (q.order_by(VideoClip.captured_at.desc())
            .offset(max(0, offset)).limit(max(1, min(200, limit))).all())
    names = {c.id: c.name for c in db.query(Course).all()}
    # One query for the page, not one per clip.
    cut_ids = [c.audio_cut_id for c in rows if c.audio_cut_id]
    idxs = (dict(db.query(AudioCut.id, AudioCut.idx)
                 .filter(AudioCut.id.in_(cut_ids)).all())
            if cut_ids else {})
    return {"stage": stage, "offset": offset, "limit": limit,
            "clips": [_clip_row(c, names.get(c.course_id),
                                idxs.get(c.audio_cut_id)) for c in rows]}


@router.get("/cuts")
def flow_cuts(upload_id: int = 0, produced: str = "", limit: int = 80,
              offset: int = 0, db: Session = Depends(get_db)):
    """The swing-sized cuts, newest first.

    `produced=0` is the interesting filter: a cut nothing was made
    from is either a swing the producer failed on or a sound that was
    never a swing, and neither is visible in a list of produced clips.
    """
    from ..config import settings
    q = db.query(AudioCut)
    if upload_id:
        q = q.filter(AudioCut.long_upload_id == upload_id)
    if produced == "1":
        q = q.filter(AudioCut.produced.is_(True))
    elif produced == "0":
        q = q.filter(AudioCut.produced.is_(False))
    rows = (q.order_by(AudioCut.created_at.desc(), AudioCut.idx.asc())
            .offset(max(0, offset)).limit(max(1, min(200, limit))).all())
    base = settings.app_base_url
    return {"offset": offset, "limit": limit,
            "cuts": [_cut_row(k, base) for k in rows]}


@router.post("/clips/{clip_id}/accept")
def accept_clip(clip_id: int, by: str = Form(""),
                db: Session = Depends(get_db)):
    """Move a draft to final: a person looked at it and says it is good."""
    c = db.get(VideoClip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    c.stage = "final"
    c.accepted_at = _utcnow()
    c.accepted_by = (by or "").strip()[:120] or None
    db.commit()
    log.info("flow: clip %s accepted%s", clip_id,
             " by %s" % c.accepted_by if c.accepted_by else "")
    return _clip_row(c)


@router.post("/clips/{clip_id}/unaccept")
def unaccept_clip(clip_id: int, db: Session = Depends(get_db)):
    """Back to draft. Accepting is a judgement and judgements change.

    The broadcast timestamp is NOT cleared: whether it went out is a
    fact about the past and un-accepting cannot unsend it.
    """
    c = db.get(VideoClip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    c.stage = "draft"
    c.accepted_at = None
    c.accepted_by = None
    db.commit()
    return _clip_row(c)


@router.post("/clips/{clip_id}/broadcast")
def broadcast_clip(clip_id: int, on: bool = Form(True),
                   db: Session = Depends(get_db)):
    """Mark a clip as broadcast, from whatever stage it is in.

    DELIBERATELY NOT A STAGE CHANGE. A draft that goes out is still a
    draft nobody has approved, and recording it as "final" would lose
    that distinction for good.
    """
    c = db.get(VideoClip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    c.broadcast_at = _utcnow() if on else None
    db.commit()
    return _clip_row(c)


def record_cut(db, *, upload, idx, peak_time_sec=None, start_sec=None,
               end_sec=None, detector=None, zone=None, ratio=None,
               attack_ms=None, tee_filename=None, green_filename=None,
               duration_sec=None, size_mb=None) -> AudioCut | None:
    """Keep one swing-sized cut. Never raises.

    IDEMPOTENT ON (upload, idx), because a recording gets re-cut
    whenever somebody changes a gate and a second row per re-run would
    turn the tab into a history of experiments rather than a list of
    swings.

    A failure here must not cost the cut itself: the MP4 is already on
    disk and the produce that follows does not depend on this row.
    """
    try:
        k = (db.query(AudioCut)
             .filter(AudioCut.long_upload_id == upload.id,
                     AudioCut.idx == int(idx))
             .one_or_none())
        if k is None:
            k = AudioCut(long_upload_id=upload.id, idx=int(idx))
            db.add(k)
        # ── SUPPLIED WINS; ABSENT LEAVES WHAT WAS THERE ─────────────
        #
        # PATCH SEMANTICS, because this is an upsert from partial
        # information. Assigning every field unconditionally meant a
        # caller that merely did not know the tee box ERASED the one
        # already recorded — the same shape as the bug in
        # `_d3_save_swing`, where an absent value and a deliberate
        # clearance were the same statement and a run that knew less
        # than the last one silently destroyed what it knew.
        #
        # A value that genuinely changes is still supplied, so it
        # still wins. Only silence is treated as silence.
        k.course_id = getattr(upload, "course_id", None) or k.course_id
        for _f, _v in (("peak_time_sec", peak_time_sec),
                       ("start_sec", start_sec), ("end_sec", end_sec),
                       ("trigger_zone", zone), ("ratio", ratio),
                       ("attack_ms", attack_ms),
                       ("duration_sec", duration_sec),
                       ("size_mb", size_mb),
                       ("tee_filename", tee_filename),
                       ("green_filename", green_filename)):
            if _v is not None:
                setattr(k, _f, _v)
        if detector:
            k.detector = str(detector)[:24]
        k.expires_at = _utcnow() + timedelta(days=CUT_RETENTION_DAYS)
        db.commit()
        return k
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("flow: could not record the cut for upload %s idx %s: %s",
                    getattr(upload, "id", None), idx, exc)
        return None


def link_produced_cut(db, *, upload, idx, clip, start_sec=None, end_sec=None,
                      detector=None, zone=None) -> AudioCut | None:
    """Tie a produced clip back to the swing it was cut from. Never raises.

    Called from the produce path, which is the only place that knows
    both halves of the fact. Two things follow from it: the cuts tab
    can answer "what did nothing get made from?", which is the whole
    reason that tab exists, and a produced clip can say which swing of
    a three-minute recording it is rather than only which recording.

    THE INDEX JOIN IS CHECKED, NOT ASSUMED. `idx` is the swing's slot
    in edit_metrics; the cuts were keyed on the audio window that found
    the swing. Those are the same number because the swings are built
    from the windows -- but if they ever drift apart, linking anyway
    would hand the operator a clip labelled with another swing's tee
    box and time, which reads exactly like a correct answer. So when a
    stored cut's peak sits outside the window this clip was produced
    from, the mismatch is logged and NOTHING is linked. An empty cuts
    column is a visible gap; a wrong one is not.
    """
    try:
        k = (db.query(AudioCut)
             .filter(AudioCut.long_upload_id == upload.id,
                     AudioCut.idx == int(idx))
             .one_or_none())
        if (k is not None and k.peak_time_sec is not None
                and start_sec is not None and end_sec is not None):
            # A one-second margin, because the produce window is
            # derived from the launch frame and the cut window from the
            # audio peak: they are built differently and are not
            # required to agree to the frame.
            lo, hi = float(start_sec) - 1.0, float(end_sec) + 1.0
            if not (lo <= float(k.peak_time_sec) <= hi):
                log.warning(
                    "flow: NOT linking upload %s swing %s to cut %s -- the "
                    "cut's peak is at %.2fs but the clip was produced from "
                    "%.2fs..%.2fs. The swing slots and the audio windows "
                    "have drifted apart; the cuts tab will show this swing "
                    "as unproduced rather than name the wrong one.",
                    upload.id, idx, k.id, float(k.peak_time_sec),
                    float(start_sec), float(end_sec))
                return None
        k = record_cut(db, upload=upload, idx=idx, start_sec=start_sec,
                       end_sec=end_sec, detector=detector, zone=zone,
                       duration_sec=(round(float(end_sec) - float(start_sec), 2)
                                     if start_sec is not None
                                     and end_sec is not None else None))
        if k is None:
            return None
        k.produced = True
        k.produce_error = None
        if clip is not None and getattr(clip, "id", None) is not None:
            clip.audio_cut_id = k.id
        db.commit()
        return k
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("flow: could not link upload %s swing %s to its cut: %s",
                    getattr(upload, "id", None), idx, exc)
        return None
