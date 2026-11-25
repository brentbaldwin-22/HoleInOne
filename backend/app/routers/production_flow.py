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
    # COUNTED THE SAME WAY THE COLUMNS SHOW IT. The clip columns show
    # only what this flow produced; a header counting all thousand
    # clips in the database over a column holding two is a number that
    # contradicts the thing underneath it.
    q = db.query(VideoClip).filter(VideoClip.flow_run_at.isnot(None))
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
               flow_only: int = 1, upload_id: int = 0,
               db: Session = Depends(get_db)):
    """Clips in one stage. `broadcast` filters on the independent fact.

    `flow_only` (the default) shows ONLY what this flow produced.

    WHY THAT IS THE DEFAULT. There are a thousand clips in this
    database from every pipeline the console has ever had, and a
    column headed "Draft produced" that opens full of them says
    nothing about the flow and buries the two clips somebody is
    actually looking at. A clip produced through here carries the cut
    it came from; nothing else does.
    """
    q = db.query(VideoClip)
    if upload_id:
        q = q.filter(VideoClip.long_upload_id == upload_id)
    if flow_only:
        # THE FLOW STAMP, NOT THE SWING LINK. Filtering on the cut id
        # hid every clip whose swing could not be identified -- which
        # is exactly the clip an operator most wants to look at.
        q = q.filter(VideoClip.flow_run_at.isnot(None))
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
    # ── WHICH FRAMES EACH DRAFT WAS CUT FROM ───────────────────────
    #
    # The clip's own record of the window it covers and the impact it
    # is a record of. Put on the card because "did the produce cut the
    # right seconds?" was otherwise answerable only by opening the
    # edit wizard and reading a scrubber against a panel -- which is
    # how a clip cut around another swing's ascent went unnoticed
    # through several runs.
    _ups = {r.long_upload_id for r in rows if r.long_upload_id}
    _sw_by_clip: dict = {}
    if _ups:
        for _u in (db.query(LongVideoUpload)
                   .filter(LongVideoUpload.id.in_(_ups)).all()):
            for _sw in ((_u.edit_metrics or {}).get("swings") or []):
                _cid = _sw.get("clip_id")
                if _cid is not None:
                    _sw_by_clip[int(_cid)] = _sw
    out = []
    for c in rows:
        _r = _clip_row(c, names.get(c.course_id), idxs.get(c.audio_cut_id))
        _sw = _sw_by_clip.get(c.id) or {}
        _r["cut_frames"] = ([_sw.get("start_frame"), _sw.get("end_frame")]
                            if _sw.get("start_frame") is not None else None)
        _r["impact_frame"] = _sw.get("impact_frame")
        # A DRAFT WITH NO LINE ON IT, and why. The clip is cut from
        # the audio impact whether or not a ball was found in the sky;
        # saying which is which is what stops a tracerless draft
        # reading as a tracer that failed.
        _r["no_tracer"] = _sw.get("no_tracer")
        # THE CONTRADICTION, ON THE CARD. Worked out here rather than
        # read off the record: a flag the produce stamped only appears
        # on clips produced since it existed, and the clip somebody is
        # staring at is usually an older one.
        _i, _lo, _hi = (_sw.get("impact_frame"), _sw.get("start_frame"),
                        _sw.get("end_frame"))
        _r["frames_disagree"] = None
        if _i is not None and _lo is not None and _hi is not None:
            if not (int(_lo) <= int(_i) <= int(_hi)):
                _r["frames_disagree"] = (
                    "cut f%d–f%d, which does not contain its own impact "
                    "at f%d" % (int(_lo), int(_hi), int(_i)))
        out.append(_r)
    # The day and group each clip belongs to, worked out over the page
    # that is being shown. Said once, on the server, because a second
    # implementation of "which group is this" in the browser is a
    # second answer to it.
    assign_groups(out)
    return {"stage": stage, "offset": offset, "limit": limit,
            "flow_only": bool(flow_only), "clips": out}


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


# ── COLUMN 1: THE RAW RECORDINGS ───────────────────────────────────

def _raw_row(db, row, info_of) -> dict:
    """One recording, with what the operator needs to judge it.

    Capture time, how long it runs, which tee box fired it, and both
    angles — the two files are the thing being looked at, so they are
    URLs the console's own viewer can open rather than a download.
    """
    from .admin import _clip_url_for, _trigger_zone

    _tee, _green = None, None
    try:
        _tee = _clip_url_for(row.tee_filename)
        _green = _clip_url_for(row.green_filename)
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: no url for upload %s: %s", row.id, exc)
    _zone = None
    try:
        _zone = _trigger_zone(db, row)
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": row.id,
        "course_id": row.course_id,
        "hole_number": getattr(row, "hole_number", None),
        "captured_at": (row.base_captured_at.isoformat()
                        if row.base_captured_at else None),
        "duration_sec": info_of(row),
        "trigger_zone": _zone,
        "tee_url": _tee,
        "green_url": _green,
        "has_green": bool(row.green_filename),
        "processing_status": row.processing_status,
    }


@router.get("/raw")
def flow_raw(course_id: int = 0, limit: int = 40, offset: int = 0,
             db: Session = Depends(get_db)):
    """The recordings this flow can be started on, newest first."""
    from .admin import _local_tee

    q = db.query(LongVideoUpload)
    if course_id:
        q = q.filter(LongVideoUpload.course_id == course_id)
    rows = (q.order_by(LongVideoUpload.base_captured_at.desc())
            .offset(max(0, offset)).limit(max(1, min(200, limit))).all())

    def _dur(r):
        # PROBED, AND NEVER AT THE COST OF THE LIST. The duration is
        # not on the row; it comes from the file. A probe that fails
        # or a file that is no longer on disk must leave a gap in one
        # card, not an error where the column should be.
        try:
            from ..services.video import probe_video_info
            _p = _local_tee(r)
            if not _p or not _p.exists():
                return None
            _d = (probe_video_info(_p) or {}).get("duration")
            return round(float(_d), 1) if _d else None
        except Exception:  # noqa: BLE001
            return None

    names = {c.id: c.name for c in db.query(Course).all()}
    out = []
    for r in rows:
        _row = _raw_row(db, r, _dur)
        _row["course_name"] = names.get(r.course_id)
        _row["cuts"] = (db.query(func.count(AudioCut.id))
                        .filter(AudioCut.long_upload_id == r.id).scalar() or 0)
        out.append(_row)
    return {"offset": offset, "limit": limit, "raw": out}


# ── THE RUN: ONE RECORDING, ALL THE WAY TO DRAFTS ──────────────────
#
# TWO PHASES, AND THE SECOND IS NOT NEW CODE. Producing from an audio
# impact -- find the strike, take its impact frame, sweep the frames
# just after it for the ball leaving, look for it coming down, render
# the tracer and the graphics -- is what `_produce_run` already does:
# its step 1 asks the cutter where the swings are and takes an impact
# frame from each, and steps 2 to 4 run once per swing. Writing a
# second pipeline shaped like it would mean two answers to "how did
# this clip get made", and the one the operator was reading would
# drift from the one that ran.
#
# What this adds is the part the console never had: the swing-sized
# cuts kept as artifacts in their own right, so the operator can see
# WHAT WAS HEARD before and regardless of what was produced from it --
# and a clock on each phase.

# Four seconds before the strike and eight after: long enough to see
# the swing and the ball away, short enough that two angles per swing
# of a round is not the whole disk.
FLOW_CUT_PRE_SEC = 4.0
FLOW_CUT_POST_SEC = 8.0

# ITS OWN FOLDER, NOT THE TEST CUTTER'S. `_testcuts` is scratch -- every
# test-cut run wipes the previous one's files -- and a flow cut has to
# outlive the next time somebody presses Test Cut.
FLOW_CUTS_SUBDIR = "_flowcuts"


def _phased(progress, phase):
    """Tag every progress line with the column it belongs to.

    The runner reports one `stage` string and the page has four
    columns. Without a tag the operator gets "Cutting strike 2 of 5"
    floating over a card in the first column while the work it
    describes belongs in the second, and once the produce starts its
    own lines arrive with no way to tell them from the cutter's.

    A prefix rather than a new field because the background runner
    carries exactly one string; adding a field would mean changing the
    runner every other job in the console shares.
    """
    if progress is None:
        return None

    def _p(stage, done=0, total=0):
        progress("%s|%s" % (phase, stage), done, total)
    return _p


def _flow_phase(rep, name, title, secs, detail="", count=None, units=None):
    rep["phases"].append({"name": name, "title": title,
                          "secs": round(float(secs), 2), "detail": detail,
                          "count": count, "units": units})


def flow_run(row, src_path, db, progress=None) -> dict:
    """Cut this recording on audio, then produce a draft from each cut.

    Never raises: it is handed to the console's background runner, and
    a traceback there is a spinner that never stops.
    """
    import time as _time
    from pathlib import Path

    from ..services.video import cut_segment
    from .admin import (
        CLIPS_DIR, _clip_url_for, _cutter_hits, _local_green, _trigger_zone,
        probe_fps,
    )

    rep: dict = {"ok": False, "upload_id": row.id, "phases": [],
                 "cuts": [], "n_cuts": 0, "n_clips": 0, "reason": None}
    _t_run = _time.monotonic()

    # ══ PHASE 1. WHAT THE MICROPHONE HEARD ════════════════════════
    _t = _time.monotonic()
    _say = _phased(progress, "audio")
    if _say:
        _say("Listening for strikes", 0, 0)
    dbg: dict = {}
    fps = float(probe_fps(src_path) or 0.0) or 30.0
    try:
        # THE HITS, NOT THE TIMES. `_cutter_times` flattens them to bare
        # floats, and the cuts column has to show WHAT WAS HEARD -- the
        # ratio and the rise time that got each one through this tee
        # box's gates. Calling the flattened version and then reading
        # `.get("ratio")` off a float is exactly what this did.
        hits = _cutter_hits("audio", db, row, src_path, fps, dbg) or []
    except Exception as exc:  # noqa: BLE001
        log.exception("flow: the cutter failed on %s: %s", row.id, exc)
        rep["reason"] = "the audio cutter failed: %s" % exc
        _flow_phase(rep, "audio", "Listening for strikes",
                    _time.monotonic() - _t, detail=str(exc))
        return rep
    _zone = None
    try:
        _zone = _trigger_zone(db, row)
    except Exception:  # noqa: BLE001
        pass
    _t_audio = _time.monotonic() - _t

    if not hits:
        _flow_phase(rep, "audio", "Listening for strikes", _t_audio,
                    detail="%s — %s" % (
                        _gates_sentence(dbg),
                        dbg.get("reason")
                        or "nothing cleared this tee box's gates"),
                    count=0, units="strikes")
        rep["reason"] = (dbg.get("reason")
                         or "no strike cleared this tee box's audio gates")
        rep["ok"] = True
        rep["debug"] = _json_small(dbg)
        return rep

    # ══ PHASE 2. CUT BOTH ANGLES AROUND EACH STRIKE ═══════════════
    #
    # A FRESH START, NOT AN ADDITION. Re-running the flow on a
    # recording means "do this again", and the cuts are keyed on
    # (upload, idx): a second run that found three strikes where the
    # first found five left the last two sitting there, pointing at
    # files this run is about to overwrite or has already replaced.
    # The column then showed five cuts for a run that made three, with
    # no way to tell which two were ghosts.
    #
    # The produce clears the upload's clips the same way, for the same
    # reason, which is why the two together are a re-run rather than a
    # second pile on top of the first.
    _t = _time.monotonic()
    rep["cleared"] = _clear_previous(db, row)
    _green_src = None
    try:
        _g = _local_green(row)
        _green_src = _g if (_g and _g.exists()) else None
    except Exception:  # noqa: BLE001
        _green_src = None
    _sub = CLIPS_DIR / FLOW_CUTS_SUBDIR
    _sub.mkdir(parents=True, exist_ok=True)
    _dur = None
    try:
        from ..services.video import probe_video_info
        _dur = (probe_video_info(Path(src_path)) or {}).get("duration")
    except Exception:  # noqa: BLE001
        pass

    _say = _phased(progress, "cut")
    for idx, hit in enumerate(hits):
        if _say:
            _say("Cutting strike %d of %d" % (idx + 1, len(hits)),
                 idx, len(hits))
        _peak = _hit_peak_sec(hit)
        _of = hit.get if isinstance(hit, dict) else (lambda _k: None)
        if _peak is None:
            continue
        _a = max(0.0, _peak - FLOW_CUT_PRE_SEC)
        _b = _peak + FLOW_CUT_POST_SEC
        if _dur:
            _b = min(_b, float(_dur))
        _tok = "%d-%02d" % (row.id, idx)
        _tee_name = "flowcut-%s-tee.mp4" % _tok
        _green_name = "flowcut-%s-green.mp4" % _tok
        _tee_ok = _green_ok = False
        try:
            _tee_ok = bool(cut_segment(src_path, _sub / _tee_name,
                                       _a, _b, fast=True))
        except Exception as exc:  # noqa: BLE001
            log.warning("flow: tee cut %s of %s failed: %s", idx, row.id, exc)
        if _green_src is not None:
            try:
                _green_ok = bool(cut_segment(_green_src,
                                             _sub / _green_name,
                                             _a, _b, fast=True))
            except Exception as exc:  # noqa: BLE001
                log.warning("flow: green cut %s of %s failed: %s",
                            idx, row.id, exc)
        _size = None
        try:
            _p = _sub / _tee_name
            _size = round(_p.stat().st_size / 1e6, 2) if _p.exists() else None
        except OSError:
            pass
        k = record_cut(
            db, upload=row, idx=idx, peak_time_sec=round(float(_peak), 2),
            start_sec=round(_a, 2), end_sec=round(_b, 2), detector="audio",
            zone=_zone, ratio=_of("ratio"), attack_ms=_of("attack_ms"),
            tee_filename=("%s/%s" % (FLOW_CUTS_SUBDIR, _tee_name)
                          if _tee_ok else None),
            green_filename=("%s/%s" % (FLOW_CUTS_SUBDIR, _green_name)
                            if _green_ok else None),
            duration_sec=round(_b - _a, 2), size_mb=_size)
        rep["cuts"].append({
            "idx": idx, "peak_time_sec": round(float(_peak), 2),
            "start_sec": round(_a, 2), "end_sec": round(_b, 2),
            "ratio": _of("ratio"), "attack_ms": _of("attack_ms"),
            "tee_url": _clip_url_for("%s/%s" % (FLOW_CUTS_SUBDIR, _tee_name))
            if _tee_ok else None,
            "green_url": _clip_url_for("%s/%s" % (FLOW_CUTS_SUBDIR,
                                                  _green_name))
            if _green_ok else None,
            "id": getattr(k, "id", None),
        })
    rep["n_cuts"] = len(rep["cuts"])
    _flow_phase(rep, "cut", "Cutting both angles around each strike",
                _time.monotonic() - _t, count=rep["n_cuts"], units="cuts")
    _flow_phase(rep, "audio", "Listening for strikes", _t_audio,
                detail=_gates_sentence(dbg),
                count=len(hits), units="strikes")
    rep["phases"].sort(key=lambda p: 0 if p["name"] == "audio" else 1)

    # ══ PHASE 3. PRODUCE A DRAFT FROM EACH ════════════════════════
    _t = _time.monotonic()
    _say = _phased(progress, "produce")
    if _say:
        _say("Producing", 0, 0)
    try:
        from .admin import _produce_run
        # The produce's own lines come through tagged too, so they land
        # in the draft column rather than under the cutter.
        _pr = _produce_run(row, src_path, db, progress=_say)
    except Exception as exc:  # noqa: BLE001
        log.exception("flow: produce failed on %s: %s", row.id, exc)
        _pr = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    rep["produce"] = _json_small(_pr)
    rep["n_clips"] = int(_pr.get("n_produced") or 0)
    _flow_phase(rep, "produce", "Producing a draft from each cut",
                _time.monotonic() - _t, count=rep["n_clips"], units="clips",
                detail=str(_pr.get("reason") or ""))
    # THE PRODUCE'S OWN STAGES, carried through rather than summarised.
    # "Production took 4m 29s" is not actionable; "the descent crawl
    # took 3m of it" is.
    #
    # `seconds` AND `counts`, which is what the produce writes. This
    # read `secs` and `units` -- the names THIS file uses -- and every
    # stage was dropped by the `is None` guard below, so the operator
    # got one four-minute row labelled "producing" and no way to see
    # which part of it was slow. Nothing failed; the breakdown simply
    # was not there.
    for st in (_pr.get("stages") or []):
        _sec = st.get("seconds")
        if _sec is None:
            continue
        _flow_phase(rep, "produce.%s" % st.get("n"),
                    str(st.get("title") or st.get("name") or "")[:120],
                    _sec, count=st.get("count"), units=st.get("counts"))

    # ══ PHASE 4. CLAIM WHAT THE RUN PRODUCED ═════════════════════
    #
    # DONE HERE, NOT DEEP IN THE PRODUCE. The produce path creates a
    # clip, and then more than one later step can replace or supersede
    # it; a link stamped at the moment of creation did not survive to
    # the page. This runs after the produce has finished and settled,
    # looks at what is actually there, and makes two separate claims
    # about each clip:
    #
    #   flow_run_at   the flow produced this. Certain, and what the
    #                 columns filter on.
    #   audio_cut_id  this is swing N. Best effort, by time, and left
    #                 unset when no cut window contains the clip --
    #                 an empty swing number is a visible gap, a wrong
    #                 one is not.
    rep["n_claimed"], rep["n_matched"] = _claim_clips(db, row, rep["cuts"])
    rep["total_sec"] = round(_time.monotonic() - _t_run, 2)
    rep["ok"] = True
    return rep


def _clear_previous(db, row) -> dict:
    """Throw away what an earlier run of this flow left on this upload.

    Rows AND files: a cut row removed while its mp4 stays behind is a
    file nothing points at, in a folder no retention sweep walks.

    ANY CLIP STILL POINTING AT ONE IS UNPOINTED FIRST. The produce
    that follows clears the upload's clips anyway, but it runs after
    this and can fail; a dangling `audio_cut_id` left in between would
    make a clip claim to be a swing that no longer exists, which reads
    as provenance and is not.
    """
    from .admin import CLIPS_DIR

    out = {"cuts": 0, "files": 0}
    try:
        old = (db.query(AudioCut)
               .filter(AudioCut.long_upload_id == row.id).all())
        if not old:
            return out
        ids = {k.id for k in old}
        for c in (db.query(VideoClip)
                  .filter(VideoClip.audio_cut_id.in_(ids)).all()):
            c.audio_cut_id = None
        for k in old:
            for field in ("tee_filename", "green_filename"):
                name = getattr(k, field, None)
                if not name:
                    continue
                try:
                    path = (CLIPS_DIR / name).resolve()
                    if CLIPS_DIR.resolve() in path.parents and path.is_file():
                        path.unlink()
                        out["files"] += 1
                except OSError as exc:
                    log.warning("flow: could not delete %s: %s", name, exc)
            db.delete(k)
            out["cuts"] += 1
        db.commit()
        log.info("flow: upload %s -- cleared %d cut(s) and %d file(s) "
                 "from an earlier run", row.id, out["cuts"], out["files"])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("flow: could not clear upload %s: %s", row.id, exc)
    return out


def _claim_clips(db, row, cuts) -> tuple[int, int]:
    """Stamp this run's clips, and match each to its cut where it can."""
    from datetime import datetime as _dt
    try:
        clips = (db.query(VideoClip)
                 .filter(VideoClip.long_upload_id == row.id).all())
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: could not claim clips on %s: %s", row.id, exc)
        return (0, 0)
    base = row.base_captured_at
    claimed = matched = 0
    for c in clips:
        c.flow_run_at = _dt.utcnow()
        claimed += 1
        if c.audio_cut_id or base is None or c.captured_at is None:
            continue
        # The clip's capture time is the recording's start plus where
        # in it the clip begins, so the difference is its offset on the
        # same clock the cuts were measured on.
        try:
            off = (c.captured_at - base).total_seconds()
        except TypeError:
            continue
        # `cut`, not `hit`: a hit is what the DETECTOR reported and a
        # cut is what was written from it. They are one line apart in
        # this file and carry different keys.
        cut = next((q for q in cuts
                    if q.get("start_sec") is not None
                    and float(q["start_sec"]) - 1.0 <= off
                    <= float(q["end_sec"]) + 1.0), None)
        if cut is None or cut.get("id") is None:
            continue
        c.audio_cut_id = cut["id"]
        k = db.get(AudioCut, cut["id"])
        if k is not None:
            k.produced = True
        matched += 1
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("flow: could not save the claim on %s: %s", row.id, exc)
        return (0, 0)
    log.info("flow: upload %s -- claimed %d clip(s), matched %d to a cut",
             row.id, claimed, matched)
    return (claimed, matched)


def _gates_sentence(dbg) -> str:
    """The gates this cut actually ran at, in words.

    SO THE QUESTION DOES NOT HAVE TO BE ASKED. "Are we cutting with
    the back-tee preset?" could only be answered by noticing a 22 ms
    attack surviving a gate that is supposed to refuse anything over
    14. The run now says what it used, and says plainly when those are
    the module defaults -- which is every hole nobody has applied a
    preset to.
    """
    g = (dbg or {}).get("gates_used") or {}
    if not g:
        return str((dbg or {}).get("gates_from") or "")
    bits = ["×%g ratio" % g.get("audio_min_ratio", 0),
            "≤%gms attack" % g.get("audio_max_attack_ms", 0),
            "%gs apart" % g.get("audio_min_separation_sec", 0)]
    on = (dbg or {}).get("audio1_on") or []
    if not on:
        bits.append("no audio1 gate is set — these are the defaults, "
                    "not a tee-box preset")
    elif not (dbg or {}).get("audio1_armed"):
        bits.append("%d audio1 gate%s set but NOT armed, so they "
                    "reported and refused nothing"
                    % (len(on), "" if len(on) == 1 else "s"))
    else:
        bits.append("%d audio1 gate%s armed: %s"
                    % (len(on), "" if len(on) == 1 else "s",
                       ", ".join(k.replace("audio_", "") for k in on)))
    return " · ".join(bits)


def _hit_peak_sec(hit):
    """The strike's time, whatever shape the cutter reported it in."""
    if hit is None:
        return None
    if isinstance(hit, (int, float)):
        return float(hit)
    for k in ("peak_time_sec", "peak_sec", "time_sec", "t"):
        v = hit.get(k) if isinstance(hit, dict) else None
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _json_small(d, cap=20000):
    """Enough of a debug blob to read, never enough to wedge a page."""
    try:
        import json as _json
        s = _json.dumps(d, default=str)
        return d if len(s) <= cap else {"truncated": True, "head": s[:cap]}
    except Exception:  # noqa: BLE001
        return {"unserialisable": True}


@router.post("/raw/{upload_id}/start")
def flow_start(upload_id: int, db: Session = Depends(get_db)):
    """Start the flow on one recording: cut on audio, then produce.

    Runs on a thread through the console's own background runner, so
    the answer here is "started", not the result. A produce is minutes
    of work and an HTTP request that waits for one is a request that
    times out somewhere the operator cannot see.
    """
    from .admin import _debugx_start

    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "long upload not found")
    return _debugx_start("flow", upload_id, _flow_runner)


def _flow_runner(row, src_path, db, progress=None):
    return flow_run(row, src_path, db, progress=progress)


@router.get("/raw/{upload_id}/status")
def flow_status(upload_id: int):
    """How the run is going, or how it went."""
    from .admin import _debugx_status, _json_safe

    return _json_safe(_debugx_status("flow", upload_id))


# ── GROUPS: A ROUND'S SWINGS, AS THE DAY WENT ──────────────────────
#
# "Tuesday, November 24th — Group 1". A group is the swings inside
# seven minutes of the FIRST swing in it, numbered from 1 as the day
# goes on.
#
# SEVEN MINUTES FROM THE GROUP'S START, not from the previous swing.
# A rolling gap would let a group run for hours whenever swings
# happened to arrive every six minutes -- the window would never
# close, and "Group 1" would mean the morning. From the start, a group
# is at most seven minutes long, which is what makes the number worth
# printing beside a clip.
#
# IN THE COURSE'S OWN DAY, not UTC. The columns are naive UTC; a swing
# at 19:30 Central is the 25th in UTC and the 24th to everybody who
# was standing there. Grouping on the UTC date would split one evening
# across two days and number both from 1.
GROUP_WINDOW_SEC = 7 * 60
GROUP_TZ = "America/Chicago"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "%dth" % n
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def _local_dt(dt):
    """A naive-UTC column as the time it was where the camera stands."""
    if dt is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        _d = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return _d.astimezone(ZoneInfo(GROUP_TZ))
    except Exception:  # noqa: BLE001
        return dt


def assign_groups(rows):
    """Number each row's group, in place, oldest first.

    `rows` are dicts carrying `captured_at`; each gets `group_number`
    and `group_label`. A row with no capture time gets neither — it
    cannot be placed in a day, and putting it in Group 1 would make it
    look like the first swing of the morning.
    """
    from datetime import datetime as _dt

    def _parse(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(str(s).replace("Z", "+00:00"))
        except ValueError:
            return None

    dated = sorted(
        ((r, _local_dt(_parse(r.get("captured_at")))) for r in rows),
        key=lambda p: (p[1] is None, p[1] or 0))
    by_day: dict = {}
    for r, when in dated:
        if when is None:
            continue
        day = when.date()
        st = by_day.get(day)
        if st is None or (when - st["start"]).total_seconds() > GROUP_WINDOW_SEC:
            st = {"n": (st["n"] + 1) if st else 1, "start": when}
            by_day[day] = st
        r["group_number"] = st["n"]
        r["group_label"] = "%s, %s %s — Group %d" % (
            when.strftime("%A"), when.strftime("%B"),
            _ordinal(when.day), st["n"])
    return rows


@router.delete("/clips/{clip_id}")
def delete_flow_clip(clip_id: int, db: Session = Depends(get_db)):
    """Throw a draft away.

    DELEGATES TO THE CONSOLE'S OWN CLIP DELETE, which is the only one
    that knows what deleting a clip entails. This had its own copy
    that removed the row and its obvious files, and four real foreign
    keys still pointed at it -- camera_events.produced_clip_id is
    stamped on every clip the moment it is produced -- so every delete
    raised an IntegrityError and 500'd with the clip still there.

    Rewriting that list here would have been a second answer to "what
    does deleting a clip mean", and the second answer is always the
    one that is missing a table. The flow adds only what is its own:
    a clip that has gone out is not thrown away by a stray click, and
    the cut it came from goes back to unproduced -- the swing still
    happened, and it is now a swing nothing was made from, which is
    what the cuts column is for.

    AND THE SWING STAYS (`keep_swing`). On the Broadcast page the tile
    an operator deletes IS the swing; here it is a DRAFT of one, and
    the swing is the audio cut beside it. Removing the swing from the
    recording's edit list would mean a deleted draft could only be
    replaced by re-running the whole flow, audio detection included --
    so the entry stays, minus its `clip_id`, and the draft can be made
    again from what is already measured.
    """
    from .admin import delete_clip

    c = db.get(VideoClip, clip_id)
    if not c:
        raise HTTPException(404, "clip not found")
    if c.broadcast_at is not None:
        raise HTTPException(
            409, "this clip has been broadcast — un-broadcast it first if "
                 "you really mean to delete it")
    # BEFORE THE DELETE, because afterwards there is no row to read the
    # cut id off.
    _cut_id = c.audio_cut_id
    out = delete_clip(clip_id, keep_swing=True, db=db)
    if _cut_id:
        k = db.get(AudioCut, _cut_id)
        if k is not None:
            k.produced = False
            db.commit()
    return dict(out or {}, ok=True, deleted=clip_id, freed_cut=_cut_id)