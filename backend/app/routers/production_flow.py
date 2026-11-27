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
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Form, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import (
    AudioCut, CameraEvent, Course, LongVideoUpload, VideoClip,
)

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


def _cut_frames(k: AudioCut) -> dict:
    """The cut's own seconds, said in frames.

    THE QUESTION THIS ANSWERS. "The produce used impact frame 2009 --
    what did the audio cut say?" There was no way to ask it: the cut
    is recorded in seconds (33.48s) and the produce reports frames
    (f2009), and converting between them needs the recording's frame
    rate and the hole's audio lag, neither of which was anywhere near
    the row. So the two numbers could not be compared, and a produce
    working off a frame the audio never pointed at looked exactly like
    one working off the right frame.

    `peak_frame` is the microphone's peak. `impact_frame` is that less
    the hole's audio lag, which is the frame the produce anchors on
    when the ball's own departure cannot be measured -- so when the
    two agree, the produce used the audio's answer, and when they
    disagree something measured a different one and should say so.

    Empty for a cut recorded before the frame rate was kept, rather
    than guessed at 30 or 60: a frame number off by a factor of two is
    worse than no frame number.
    """
    _fps = float(k.fps or 0.0)
    _pk = k.peak_time_sec
    if _fps <= 0 or _pk is None:
        return {"fps": k.fps, "lag_sec": k.lag_sec,
                "peak_frame": None, "impact_frame": None,
                "lag_frames": None,
                # WITHOUT A FRAME RATE THERE IS NO WINDOW EITHER, and
                # no window means the wizard opens on the whole
                # recording -- which is what it used to do for every
                # cut. Honest degradation for a cut recorded before
                # the frame rate was kept; a window guessed at 30 or
                # 60 would open on the wrong seconds.
                "start_frame": None, "end_frame": None}
    _lag = float(k.lag_sec or 0.0)
    out = {
        "fps": round(_fps, 2),
        "lag_sec": round(_lag, 3),
        "peak_frame": int(round(float(_pk) * _fps)),
        "lag_frames": int(round(_lag * _fps)),
        "impact_frame": int(round(max(0.0, float(_pk) - _lag) * _fps)),
        "start_frame": None, "end_frame": None,
    }
    # ── THE CUT'S OWN WINDOW, IN RECORDING FRAMES ───────────────────
    #
    # So that producing a clip BY HAND from this cut opens on the
    # twelve seconds the cut is of, rather than on the whole
    # recording. On #1915 that was "1–9239" in the frame box: the
    # operator was handed a three-minute video and asked to find a
    # strike in it that the microphone had already located.
    #
    # IN THE RECORDING'S OWN FRAME NUMBERS, not the cut file's. The
    # produce reads the full-resolution source and every frame number
    # on the swing record is an index into it; pointing the wizard at
    # the already-cut MP4 instead would re-encode a re-encode and
    # shift every number by the cut's own start.
    if k.start_sec is not None and k.end_sec is not None:
        out["start_frame"] = max(0, int(round(float(k.start_sec) * _fps)))
        out["end_frame"] = int(round(float(k.end_sec) * _fps))
    return out


def _cut_why_no_file(k: AudioCut, which: str, has_green=None) -> str | None:
    """Why this angle has no link, or None when it has one.

    A DISABLED BUTTON IS NOT AN ANSWER. There are four reasons a cut
    has no footage and the operator's next move is different for every
    one of them -- and the button looked identical for all four, so
    "why do some clips not have the tee angle enabled?" could only be
    answered by reading this file.

      SWEPT. The cuts are scratch -- two angles per swing at a few
      megabytes, every swing of every round -- and they expire after
      CUT_RETENTION_DAYS. The sweep deletes the MP4s and CLEARS the
      columns, deliberately, because a row still holding the name of
      a deleted file offers a link that plays nothing. The ROW is
      kept where a produced clip points at it: which swing of a
      three-minute recording a clip came from is worth a row even
      when the footage is gone.

      NO GREEN CAMERA. A tee-only pair has no green half to cut, which
      is an ordinary state and not a failure.

      THE CUT FAILED. ffmpeg returned nothing for this angle. Logged
      at the time and otherwise invisible.

      GONE FROM DISK. The row names a file that is not there -- a
      hand-cleared folder, a lost volume, a half-finished sweep. The
      worst of the four, because the name alone would have offered a
      link.
    """
    from .admin import CLIPS_DIR

    _fn = k.green_filename if which == "green" else k.tee_filename
    if _fn:
        if (CLIPS_DIR / _fn).exists():
            return None
        return ("the file is not on disk any more — the row still names "
                "%s, so this was cleared out from under it rather than "
                "swept" % _fn)
    _exp = k.expires_at
    if _exp is not None and _exp <= _utcnow():
        return ("swept — cuts are scratch and are deleted after %d days. "
                "The row is kept because it records which swing this was."
                % CUT_RETENTION_DAYS)
    if which == "green" and has_green is False:
        return "this recording has no green half — a tee-only pair"
    return ("the %s cut failed when this recording was run — re-run the "
            "flow on it to try again" % which)


def _cut_row(k: AudioCut, base: str, zone_label=None,
             has_green=None) -> dict:
    from .admin import CLIPS_DIR

    def _u(fn):
        # ONLY WHERE THE FILE IS ACTUALLY THERE. A URL built from a
        # name whose file is gone is a link that 404s, which is worse
        # than no link: it looks like footage.
        return (f"{base}/uploads/clips/{fn}"
                if fn and (CLIPS_DIR / fn).exists() else None)
    return {
        "id": k.id, "long_upload_id": k.long_upload_id,
        "idx": k.idx, "hole_number": k.hole_number,
        "peak_time_sec": k.peak_time_sec,
        "start_sec": k.start_sec, "end_sec": k.end_sec,
        "detector": k.detector, "trigger_zone": k.trigger_zone,
        # THE NAME, carried alongside the index for the same reason
        # the raw row carries it: "Zone 2" is the rig's count and
        # "middle" is what the operator drew. Passed in rather than
        # looked up: a cut has no camera of its own, and asking per
        # row would be a camera-event query per cut on the page.
        "trigger_zone_label": zone_label,
        "ratio": k.ratio, "attack_ms": k.attack_ms,
        **_cut_frames(k),
        "tee_url": _u(k.tee_filename), "green_url": _u(k.green_filename),
        # AND WHY, WHERE THERE IS NONE. See `_cut_why_no_file`.
        "tee_why": _cut_why_no_file(k, "tee", has_green),
        "green_why": _cut_why_no_file(k, "green", has_green),
        "duration_sec": k.duration_sec, "size_mb": k.size_mb,
        "produced": bool(k.produced), "produce_error": k.produce_error,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
    }


@router.get("/counts")
def flow_counts(day: str = "", zone: int = -1, course_id: int = 0,
                db: Session = Depends(get_db)):
    """What is in each tab, and in what unit.

    FILTERED THE SAME WAY THE COLUMNS ARE. A header reading "1560
    RECORDINGS" over a column showing one day's four is two numbers
    about different things, and the big one is the one that gets
    believed.

    `unit` travels with every number because the strip mixes two of
    them: a recording holding four swings is one row in `raw` and four
    in `draft`. Without it the tabs look like a funnel that loses
    things.
    """
    _ids = _flow_filter(db, course_id, day, zone)
    _raw_q = db.query(LongVideoUpload.id, LongVideoUpload.tee_filename)
    _cut_q = db.query(func.count(AudioCut.id))
    if _ids is not None:
        _raw_q = _raw_q.filter(LongVideoUpload.id.in_(_ids))
        _cut_q = _cut_q.filter(AudioCut.long_upload_id.in_(_ids))
    # COUNTED THE WAY THE COLUMN SHOWS IT, footage included. A header
    # reading 1560 over a list that hides every recording whose video
    # is gone is the same contradiction as one reading 1560 over a
    # single day -- and the big number is the one that gets believed.
    _l, _b = _source_index()
    n_raw = sum(1 for _, _fn in _raw_q.all() if _have_source(_fn, _l, _b))
    n_cuts = _cut_q.scalar() or 0
    # COUNTED THE SAME WAY THE COLUMNS SHOW IT. The clip columns show
    # only what this flow produced; a header counting all thousand
    # clips in the database over a column holding two is a number that
    # contradicts the thing underneath it.
    q = db.query(VideoClip).filter(VideoClip.flow_run_at.isnot(None))
    if _ids is not None:
        q = q.filter(VideoClip.long_upload_id.in_(_ids))
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
               day: str = "", zone: int = -1,
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
    # The same filter the raw column gets. See `_flow_filter`.
    _ids = _flow_filter(db, course_id, day, zone)
    if _ids is not None:
        q = q.filter(VideoClip.long_upload_id.in_(_ids))
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
    # THE WHOLE ROW, NOT JUST THE INDEX. The draft's card has to be
    # able to say what the AUDIO thought the impact frame was, beside
    # the frame the produce used -- "it identified 2009 instead of
    # 1920, what did the audio cut say?" was unanswerable from this
    # page, and the answer decides whether the produce is wrong or the
    # thing that measured a different frame is.
    _cuts = (db.query(AudioCut).filter(AudioCut.id.in_(cut_ids)).all()
             if cut_ids else [])
    idxs = {k.id: k.idx for k in _cuts}
    _cut_by_id = {k.id: k for k in _cuts}
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
        # WHERE THAT FRAME CAME FROM -- measured off the ball, or the
        # audio peak less the lag. Two different kinds of evidence
        # that looked identical on screen.
        _r["impact_from"] = _sw.get("impact_from")
        # ── AND WHAT THE AUDIO SAID, FOR COMPARISON ─────────────────
        _k = _cut_by_id.get(c.audio_cut_id)
        _kf = _cut_frames(_k) if _k is not None else {}
        _r["audio_impact_frame"] = _kf.get("impact_frame")
        _r["audio_peak_sec"] = getattr(_k, "peak_time_sec", None)
        _r["audio_lag_sec"] = _kf.get("lag_sec")
        _r["fps"] = _kf.get("fps")
        # ONE SENTENCE, ONLY WHEN THEY DIFFER. Agreement needs no
        # words; a gap of 89 frames is a second and a half of golf
        # swing and is the whole story of the clip.
        _r["impact_differs"] = None
        _ai, _pi = _r["audio_impact_frame"], _sw.get("impact_frame")
        if _ai is not None and _pi is not None and int(_ai) != int(_pi):
            _d = int(_pi) - int(_ai)
            _r["impact_differs"] = (
                "the audio put the strike at f%d; this was produced from "
                "f%d, %d frame%s %s (%.2fs) — %s"
                % (_ai, _pi, abs(_d), "" if abs(_d) == 1 else "s",
                   "later" if _d > 0 else "earlier",
                   abs(_d) / float(_kf.get("fps") or 30.0),
                   str(_sw.get("impact_from")
                       or "nothing on the swing says where f%d came from"
                       % _pi)))
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
              offset: int = 0, day: str = "", zone: int = -1,
              course_id: int = 0, db: Session = Depends(get_db)):
    """The swing-sized cuts, newest first.

    `produced=0` is the interesting filter: a cut nothing was made
    from is either a swing the producer failed on or a sound that was
    never a swing, and neither is visible in a list of produced clips.
    """
    from ..config import settings
    q = db.query(AudioCut)
    if upload_id:
        q = q.filter(AudioCut.long_upload_id == upload_id)
    # ── THE PAGE'S FILTER, NOT JUST THE RAW COLUMN'S ────────────────
    #
    # The four columns are four requests, and a day applied to only
    # the first of them is how a page says "No recordings" over three
    # columns full of other days' work.
    _ids = _flow_filter(db, course_id, day, zone)
    if _ids is not None:
        q = q.filter(AudioCut.long_upload_id.in_(_ids))
    if produced == "1":
        q = q.filter(AudioCut.produced.is_(True))
    elif produced == "0":
        q = q.filter(AudioCut.produced.is_(False))
    rows = (q.order_by(AudioCut.created_at.desc(), AudioCut.idx.asc())
            .offset(max(0, offset)).limit(max(1, min(200, limit))).all())
    base = settings.app_base_url
    # ── ONE ZONE LOOKUP PER RECORDING, NOT PER CUT ──────────────────
    #
    # A cut has no camera of its own; its zone name comes from the
    # recording's camera event. Four cuts of one recording asking
    # separately is four identical queries for one answer.
    from .admin import _trigger_zone_label
    _labels: dict = {}
    # WHETHER THE RECORDING HAD A GREEN HALF AT ALL. A tee-only pair
    # has no green to cut, which is an ordinary state -- and without
    # this the cut row cannot tell it from a green cut that failed.
    _greens: dict = {}
    for _uid in {k.long_upload_id for k in rows if k.long_upload_id}:
        _up = db.get(LongVideoUpload, _uid)
        if _up is None:
            continue
        _greens[_uid] = bool(getattr(_up, "green_filename", None))
        try:
            _labels[_uid] = _trigger_zone_label(db, _up)
        except Exception:  # noqa: BLE001
            _labels[_uid] = None
    return {"offset": offset, "limit": limit,
            "cuts": [_cut_row(k, base, _labels.get(k.long_upload_id),
                              _greens.get(k.long_upload_id))
                     for k in rows]}


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
               duration_sec=None, size_mb=None, fps=None,
               lag_sec=None) -> AudioCut | None:
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
                       # AS THEY WERE WHEN THE CUT WAS MADE. Kept on the
                       # row rather than resolved on read, so changing
                       # the hole's lag tomorrow does not rewrite what
                       # this cut was made with -- which is the only way
                       # "the lag was 0.15s then and is 0.30s now" can
                       # be told from "the lag has always been 0.30s".
                       ("fps", fps), ("lag_sec", lag_sec),
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
    from .admin import _clip_url_for, _trigger_zone, _trigger_zone_label

    _tee, _green = None, None
    try:
        _tee = _clip_url_for(row.tee_filename)
        _green = _clip_url_for(row.green_filename)
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: no url for upload %s: %s", row.id, exc)
    _zone = None
    _zone_label = None
    try:
        _zone = _trigger_zone(db, row)
        # THE NAME, NOT JUST THE INDEX. "Zone 2" is the rig's count;
        # the operator drew the box and typed "middle" on it.
        _zone_label = _trigger_zone_label(db, row, _zone)
    except Exception:  # noqa: BLE001
        pass
    return {
        "id": row.id,
        "trigger_zone_label": _zone_label,
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
        # WHETHER THERE IS WORK TO SHOW, and when it was done. The
        # report itself is not sent with the list -- it is the biggest
        # thing on the row and nobody is reading it until they ask --
        # so the button appears on this and fetches on click.
        "produce_report_at": (row.produce_report_at.isoformat()
                              if row.produce_report_at else None),
    }


def _source_index():
    """Every file name that exists, locally or in the bucket.

    TWO CHEAP CALLS FOR THE WHOLE TABLE, and that is the whole point.
    `_local_tee` rehydrates from object storage before answering, which
    is right for one recording and ruinous for fifteen hundred: asking
    it per row to find out whether a file is there would try to
    DOWNLOAD every clip on the course.

    One directory scan and one bucket listing answer the same question
    for every row at once. A name in either place is a recording that
    can still be worked on -- the disk is ephemeral here and a clip
    that is only in the bucket is pulled back the moment anything
    needs it.

    Returns (local names, bucket names). Either may be empty: no
    bucket configured, or a listing that failed. A failure must not
    make the page claim the footage is gone, so a caller treats an
    empty bucket set as "the bucket could not be asked" and leans on
    the local set -- see `_have_source`.
    """
    import os

    from ..services import storage
    from .admin import CLIPS_DIR

    _local: set = set()
    try:
        with os.scandir(CLIPS_DIR) as _it:
            for _e in _it:
                if _e.is_file():
                    _local.add(_e.name)
    except OSError as exc:
        log.warning("flow: could not list %s (%s)", CLIPS_DIR, exc)
    _bucket: set = set()
    try:
        _bucket = storage.list_names() or set()
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: could not list the bucket (%s)", exc)
    return _local, _bucket


def _have_source(name, local: set, bucket: set) -> bool:
    """Is this recording's video actually anywhere?

    A row whose source is gone is a row nothing can be done with: no
    cut, no produce, no angle to open. It used to sit in the list
    showing "—" for its duration and two dead buttons, which reads as
    a recording waiting to be worked on.

    LOCAL *OR* BUCKET. The disk is wiped on every deploy here, so "not
    under CLIPS_DIR" is not "gone" -- it is the normal state of
    anything older than the last restart.
    """
    if not name:
        return False
    _base = Path(name).name
    return _base in local or _base in bucket


def _day_bounds_utc(day: str):
    """A local calendar day, as the half-open UTC range it covers.

    THE COLUMN IS NAIVE UTC AND THE DAY WANTED IS LOCAL, and the first
    version of this filtered in Python AFTER the page had been taken,
    which is the bug this exists to fix: with 1560 recordings and a
    page of 40, choosing any day older than the newest forty found
    nothing -- while the dropdown went on offering it, because the
    dropdown is built from the whole table.

    So the day has to narrow the QUERY, and that means arithmetic
    rather than a string comparison. `zoneinfo` resolves the offset
    for that date, so the two days a year that are 23 or 25 hours long
    come out right without this having to know about them.

    Returns (lo, hi) as naive UTC, half-open, or None when the date
    cannot be read.
    """
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        _z = ZoneInfo(GROUP_TZ)
        _d = _dt.strptime(day, "%Y-%m-%d").date()
        _lo = _dt.combine(_d, _dt.min.time(), tzinfo=_z)
        _hi = _lo + timedelta(days=1)
        return (_lo.astimezone(timezone.utc).replace(tzinfo=None),
                _hi.astimezone(timezone.utc).replace(tzinfo=None))
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: could not read the day %r (%s)", day, exc)
        return None


def _zone_upload_ids(db, q):
    """Every upload in `q`, keyed by the trigger zone it fired on.

    TWO QUERIES, NOT ONE PER ROW. The zone lives on the camera event
    behind the upload, and asking per upload is a query per recording
    -- fifteen hundred of them on this course.
    """
    _pairs = q.with_entities(LongVideoUpload.id,
                             LongVideoUpload.camera_event_id).all()
    _ev_ids = {e for _, e in _pairs if e}
    _zone_of: dict = {}
    if _ev_ids:
        for _eid, _d in (db.query(CameraEvent.id, CameraEvent.trigger_detail)
                         .filter(CameraEvent.id.in_(_ev_ids)).all()):
            if isinstance(_d, dict) and _d.get("zone") is not None:
                try:
                    _zone_of[_eid] = int(_d["zone"])
                except (TypeError, ValueError):
                    continue
    out: dict = {}
    for _uid, _eid in _pairs:
        _z = _zone_of.get(_eid)
        if _z is None:
            continue
        out.setdefault(_z, []).append(_uid)
    return out


def _flow_filter(db, course_id: int = 0, day: str = "", zone: int = -1):
    """The recordings a day and a tee box select, or None for no filter.

    ONE ANSWER FOR THE WHOLE PAGE. The raw column, the cuts, the
    drafts, the finals and the counts in the header are five separate
    requests, and a filter applied to only the first of them is how a
    page ends up saying "No recordings" above three columns full of
    other days' work -- with a cell reading "#1896 — not on this page
    of recordings" to explain it.

    Returns a list of upload ids, or None when nothing is filtered.
    An EMPTY LIST is a real answer and means "that day has none",
    which is not the same as None and must not be collapsed into it.
    """
    _day = (day or "").strip()
    _zone = int(zone) if zone is not None else -1
    if not _day and _zone < 0:
        return None
    q = db.query(LongVideoUpload)
    if course_id:
        q = q.filter(LongVideoUpload.course_id == course_id)
    if _day:
        _b = _day_bounds_utc(_day)
        if _b is None:
            return []
        q = q.filter(LongVideoUpload.base_captured_at >= _b[0],
                     LongVideoUpload.base_captured_at < _b[1])
    if _zone < 0:
        return [r[0] for r in q.with_entities(LongVideoUpload.id).all()]
    return list(_zone_upload_ids(db, q).get(_zone, []))


@router.get("/raw")
def flow_raw(course_id: int = 0, limit: int = 40, offset: int = 0,
             day: str = "", zone: int = -1,
             db: Session = Depends(get_db)):
    """The recordings this flow can be started on, newest first.

    `day` is a date in the COURSE'S OWN DAY (YYYY-MM-DD), not UTC --
    the same reckoning the groups use. A swing at 19:30 Central is the
    25th in UTC and the 24th to everybody who was standing there, so
    filtering on the UTC date splits one evening across two days.

    `zone` is a stored trigger-zone INDEX, counting from 0, which is
    what the column prints beside its name.

    `days` comes back with every page: the dropdown cannot be built
    from the rows on screen, because the rows on screen are the ones
    the filter already let through.
    """
    from .admin import _local_tee

    q = db.query(LongVideoUpload)
    if course_id:
        q = q.filter(LongVideoUpload.course_id == course_id)
    # ── EVERY DAY THERE IS A RECORDING FOR, BEFORE FILTERING ────────
    #
    # One cheap column read over the whole table rather than a page of
    # it: a dropdown built from the current page offers only the days
    # that page happens to hold, so choosing one can empty the list
    # and then the day that emptied it is gone from the dropdown too.
    #
    # AND ONLY WHAT IS ACTUALLY THERE. The number in brackets is what
    # an operator is about to go and work on, so counting recordings
    # whose video is gone makes it a promise the list cannot keep --
    # "2026-11-14 (27)" over a column of rows showing "—" for their
    # duration and two dead buttons.
    _local, _bucket = _source_index()
    _days: dict = {}
    for (_at, _fn) in db.query(
            LongVideoUpload.base_captured_at,
            LongVideoUpload.tee_filename).filter(
            *([LongVideoUpload.course_id == course_id] if course_id else [])
    ).all():
        _d = _local_dt(_at)
        if _d is None or not _have_source(_fn, _local, _bucket):
            continue
        _k = _d.strftime("%Y-%m-%d")
        _days[_k] = _days.get(_k, 0) + 1
    # ── AND EVERY TEE BOX, THE SAME WAY ─────────────────────────────
    _zone_ids = _zone_upload_ids(db, q)

    # ── THE FILTER NARROWS THE QUERY, NOT THE PAGE ──────────────────
    #
    # THIS IS THE BUG THIS FUNCTION WAS FIXED FOR. Both filters used
    # to run over `rows` -- the page that had already been taken --
    # so on a course with 1560 recordings and a page of 40, choosing
    # any day older than the newest forty returned nothing while the
    # dropdown went on offering it, because the dropdown counts the
    # whole table. "No recordings" was true of the page and false of
    # the day.
    _ids = _flow_filter(db, course_id, day, zone)
    if _ids is not None:
        # AN EMPTY LIST IS AN ANSWER. `in_([])` is false for every
        # row, which is exactly right: that day really has none.
        q = q.filter(LongVideoUpload.id.in_(_ids))

    # ── AND THE ONES WITH NO FOOTAGE COME OUT BEFORE THE PAGE ───────
    #
    # NOT AFTER IT, which is the same mistake the day filter made:
    # dropping rows from a page already taken means a page of forty
    # shows thirty-seven, `total` promises rows that will never
    # appear, and the next offset steps over whatever the gap was.
    #
    # So the surviving ids are resolved first -- two columns over the
    # filtered set, against one directory scan and one bucket listing
    # -- and the page is taken from those.
    _have = []
    _gone = []
    for _uid, _fn in q.with_entities(LongVideoUpload.id,
                                     LongVideoUpload.tee_filename).all():
        if _have_source(_fn, _local, _bucket):
            _have.append(_uid)
        else:
            _gone.append(_uid)
    _total = len(_have)
    rows = (q.filter(LongVideoUpload.id.in_(_have))
            .order_by(LongVideoUpload.base_captured_at.desc())
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
    # ── EVERY TEE BOX THERE IS A RECORDING FOR ──────────────────────
    #
    # From `_zone_upload_ids` over the whole (unfiltered) table, for
    # the same reason as the days: built from the rows on screen, the
    # list holds only the zone that was chosen and there is no way
    # back to the others.
    #
    # The NAME comes from one recording per zone -- the newest, since
    # the ids come back in no order and the label is read off the
    # event. Two recordings of one zone can disagree about its name,
    # one keeping the name it had at its trigger and the other reading
    # today's box, and a dropdown can only show one.
    _zs: dict = {}
    for _z, _uids in sorted(_zone_ids.items()):
        _lbl = ""
        for _uid in sorted(_uids, reverse=True)[:4]:
            _u = db.get(LongVideoUpload, _uid)
            if _u is None:
                continue
            try:
                from .admin import _trigger_zone_label as _tzl
                _lbl = _tzl(db, _u, _z) or ""
            except Exception:  # noqa: BLE001
                _lbl = ""
            if _lbl:
                break
        _zs[_z] = _lbl
    return {"offset": offset, "limit": limit, "raw": out,
            # HOW MANY THE FILTER ACTUALLY MATCHES, so "no recordings"
            # over an empty column can be told from a page that simply
            # has not reached them.
            "total": int(_total),
            # AND HOW MANY WERE DROPPED FOR HAVING NO FOOTAGE. Said
            # out loud: a row quietly missing from a list is the same
            # shape of silence as a filter that matched nothing.
            "hidden_no_source": len(_gone),
            # WHICH ONES, so a band that still has cuts or drafts can
            # say "its source video is gone" instead of the useless
            # "not on this page of recordings". Capped: this is for
            # naming the handful on screen, not for listing a
            # thousand.
            "hidden_ids": sorted(_gone, reverse=True)[:500],
            # EVERY DAY AND EVERY ZONE THERE IS SOMETHING FOR, so the
            # dropdowns do not shrink to whatever the filter let past.
            "days": [{"day": k, "n": v}
                     for k, v in sorted(_days.items(), reverse=True)],
            "zones": [{"zone": k, "label": v}
                      for k, v in sorted(_zs.items())],
            "day": day or None,
            "zone": int(zone) if zone is not None and int(zone) >= 0
            else None}


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


def flow_run(row, src_path, db, progress=None, cuts_only=False) -> dict:
    """Cut this recording on audio, then produce a draft from each cut.

    `cuts_only` STOPS AFTER THE CUTS. The produce is the expensive
    half by a wide margin -- minutes of sky searching per swing
    against seconds of ffmpeg -- and it is also the half an operator
    most often wants to do by hand: on a recording where the ascent
    search is going to find nothing anyway, waiting for it to fail
    four times before being allowed to plot the swings is the long way
    round to the same place. The cuts alone give the cuts column, and
    every cut carries a Produce Clip button.

    IT STILL CLEARS THE EARLIER RUN'S CUTS. Re-cutting is re-cutting,
    and leaving the old rows would mean the column showed the cuts of
    two different gate settings at once. What it does NOT clear is
    produced clips: those are somebody's work, and a cut is not an
    instruction to throw them away. They lose their link to a cut
    that no longer exists, which the card already says out loud.

    Never raises: it is handed to the console's background runner, and
    a traceback there is a spinner that never stops.
    """
    import time as _time
    from pathlib import Path

    from ..services.video import cut_segment
    from .admin import (
        CLIPS_DIR, _audio_lag_sec, _clip_url_for, _cutter_hits, _local_green,
        _trigger_zone, probe_fps,
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
    # THE SAME LAG THE PRODUCE WILL SUBTRACT, read once here and kept
    # on every cut row. Without it the cuts column reports a second
    # and the drafts column reports a frame, and the only way to ask
    # whether they are the same instant was to know this hole's lag
    # and the recording's frame rate by heart.
    _lag = _audio_lag_sec(db, row)
    # ONTO THE DEBUG THE SENTENCE IS BUILT FROM, so the audio phase
    # reports it beside the thresholds.
    dbg["audio_lag_sec"] = round(float(_lag), 3)
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
            duration_sec=round(_b - _a, 2), size_mb=_size,
            fps=round(float(fps), 3), lag_sec=round(float(_lag), 3))
        rep["cuts"].append({
            "idx": idx, "peak_time_sec": round(float(_peak), 2),
            "start_sec": round(_a, 2), "end_sec": round(_b, 2),
            "fps": round(float(fps), 2), "lag_sec": round(float(_lag), 3),
            "peak_frame": int(round(float(_peak) * float(fps))),
            "impact_frame": int(round(
                max(0.0, float(_peak) - float(_lag)) * float(fps))),
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

    # ── STOP HERE IF THAT IS ALL THAT WAS ASKED FOR ───────────────
    if cuts_only:
        rep["cuts_only"] = True
        rep["total_sec"] = round(_time.monotonic() - _t_run, 2)
        rep["ok"] = True
        # WHAT IS LEFT OF AN EARLIER PRODUCE, counted rather than
        # removed. A draft of a cut this run has just replaced is not
        # wrong, it is OLD -- and quietly deleting it would be this
        # function deciding something it was not asked about.
        try:
            rep["n_clips"] = (
                db.query(VideoClip)
                .filter(VideoClip.long_upload_id == row.id).count())
        except Exception:  # noqa: BLE001
            rep["n_clips"] = 0
        rep["reason"] = (
            "%d cut(s) — the produce was not run, so each cut is waiting "
            "for Produce Clip%s"
            % (rep["n_cuts"],
               ". The %d clip(s) already on this recording were left "
               "alone, and no longer name a cut" % rep["n_clips"]
               if rep["n_clips"] else ""))
        _flow_phase(rep, "produce", "Producing a draft from each cut",
                    0.0, count=0, units="clips",
                    detail="not run — audio cuts only")
        return rep

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

    out = {"cuts": 0, "files": 0, "kept_manual": 0}
    try:
        # ── THE MACHINE'S OWN, NOT THE OPERATOR'S ───────────────────
        #
        # This clears so that a re-cut is a re-cut and not a history of
        # experiments. A cut somebody MADE BY HAND is not an earlier
        # run's leftovers -- it is work, and the operator pressing
        # "Audio cuts only" afterwards is asking the machine to have
        # another go at the strikes, not to throw away the swings they
        # marked themselves. Those live at or above MANUAL_IDX_BASE and
        # are left exactly where they are.
        out["kept_manual"] = (
            db.query(func.count(AudioCut.id))
            .filter(AudioCut.long_upload_id == row.id,
                    AudioCut.idx >= MANUAL_IDX_BASE).scalar() or 0)
        old = (db.query(AudioCut)
               .filter(AudioCut.long_upload_id == row.id,
                       AudioCut.idx < MANUAL_IDX_BASE).all())
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
        # ── AN EXISTING STAMP IS LEFT ALONE ────────────────────────
        #
        # The stamp says "the flow produced this" and its value is
        # when it was first claimed. Re-stamping every clip on every
        # claim made this unusable as an answer to "when", and it is
        # what lets this run a second time -- which it now does, after
        # an operator edits or adds a swing from the flow page -- as a
        # no-op over clips that were already claimed.
        #
        # A re-run of the whole flow is unaffected: it deletes the
        # cuts and the produce clears the upload's clips, so its
        # clips are new rows with nothing stamped on them.
        if c.flow_run_at is None:
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
    # ── AND THE LAG, WHICH IS NOT A THRESHOLD BUT DECIDES A FRAME ───
    #
    # Said out loud, including when it is zero. A lag of zero means
    # "the microphone's peak IS the strike", which is an assertion
    # about this hole's rig and not a neutral absence -- and it is the
    # whole reason a produce can land on the peak frame and look like
    # it ignored the audio.
    _lag = (dbg or {}).get("audio_lag_sec")
    if _lag is not None:
        bits.append("%.2fs audio lag%s"
                    % (float(_lag),
                       " (the peak is taken as the strike)"
                       if float(_lag) == 0.0 else ""))
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


@router.post("/raw/{upload_id}/cut")
def flow_start_cuts(upload_id: int, db: Session = Depends(get_db)):
    """Cut this recording on audio and stop there.

    THE SAME RUN, STOPPED AFTER PHASE 2 — not a second cutter. Which
    gates ran, which tee box they came from and which strikes cleared
    them are decided in one place, so the cuts this makes are the cuts
    the full flow would have made.

    FILED UNDER THE SAME KEY ("flow"), because it is the same
    recording and the page polls one status for it. Two kinds would
    mean a recording could appear to be running twice, and the second
    one to finish would be the one the columns believed.
    """
    from .admin import _debugx_start

    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "long upload not found")
    return _debugx_start("flow", upload_id, _flow_runner_cuts)


def _flow_runner(row, src_path, db, progress=None):
    return flow_run(row, src_path, db, progress=progress)


def _flow_runner_cuts(row, src_path, db, progress=None):
    return flow_run(row, src_path, db, progress=progress, cuts_only=True)


# ══ CUTTING BY HAND ═══════════════════════════════════════════════
#
# THIRTEEN SECONDS FROM WHERE THE OPERATOR SAID, which is a different
# statement from the one the cutter makes. The audio cutter finds a
# peak and takes FLOW_CUT_PRE_SEC before it and FLOW_CUT_POST_SEC
# after, because it has located a strike and is centring on it. An
# operator pressing "Start clip" is not marking a strike -- they are
# marking where the clip should BEGIN, and everything they want is
# after that. So this is a span forward, not a window around.
MANUAL_CUT_SEC = 13.0

# ── A NUMBERING RANGE OF ITS OWN ──────────────────────────────────
#
# `record_cut` is idempotent on (upload, idx) and the audio cutter
# numbers its strikes 0..n-1 from scratch on every re-run. Share the
# range and a hand-made cut at idx 0 is silently REPLACED by whatever
# the cutter hears first next time -- the row survives, the file is
# overwritten, and the clip the operator chose quietly becomes a
# different swing. A thousand is past any recording's strike count, so
# the two schemes can never meet and "is this one mine?" is answerable
# from the number alone.
MANUAL_IDX_BASE = 1000


@router.delete("/cuts/{cut_id}")
def delete_flow_cut(cut_id: int, db: Session = Depends(get_db)):
    """Throw one cut away — the row and both angle files.

    THE UNDO FOR THE HAND CUTTER. Marking a clip is one button press
    at the speed of a video playing, so a press half a second early,
    or twice, or on the practice swing, is the single most likely
    thing to happen on that page. Without this the only remedies were
    to re-cut the whole recording — which does not even remove a
    hand-made cut any more — or to leave a wrong clip in the stage
    and remember it was wrong.

    THE DRAFT IS NOT TAKEN WITH IT. A cut that has already been
    produced from has a clip somewhere downstream, possibly accepted
    or already out, and quietly deleting that as a consequence of
    tidying up a cut list is a much larger action than the operator
    asked for. The clip is unpointed instead — the same thing
    `_clear_previous` does, and for the same reason — so it survives
    as a clip that no longer claims provenance it does not have.
    """
    from .admin import CLIPS_DIR

    k = db.get(AudioCut, cut_id)
    if not k:
        raise HTTPException(404, "cut not found")

    out = {"ok": True, "id": cut_id, "idx": k.idx, "files": 0,
           "unpointed": 0}
    for c in (db.query(VideoClip)
              .filter(VideoClip.audio_cut_id == cut_id).all()):
        c.audio_cut_id = None
        out["unpointed"] += 1
    for field in ("tee_filename", "green_filename"):
        name = getattr(k, field, None)
        if not name:
            continue
        try:
            path = (CLIPS_DIR / name).resolve()
            # INSIDE CLIPS_DIR OR NOT AT ALL. The name comes off a
            # database row; resolving first and checking the parent
            # after is what stops a "../.." in one from reaching a
            # file this has no business touching.
            if CLIPS_DIR.resolve() in path.parents and path.is_file():
                path.unlink()
                out["files"] += 1
        except OSError as exc:
            log.warning("flow: could not delete %s: %s", name, exc)
    db.delete(k)
    db.commit()
    log.info("flow: deleted cut %s (idx %s) — %d file(s), %d clip(s) "
             "unpointed", cut_id, out["idx"], out["files"], out["unpointed"])
    return out


def reset_flow(db, upload_ids=None) -> dict:
    """Put the flow back to raw clips: delete its cuts and its drafts.

    THE ONE ANSWER, shared by the button on the page and the script in
    backend/tools, because "start over" has to mean the same thing
    whichever one is used. Two copies would drift, and the one that
    drifts is the one run at two in the morning.

    THE FENCE IS `flow_run_at`. About a thousand clips in this
    database come from earlier pipelines and some are in player
    galleries or have been shared. A clip produced through the flow
    carries `flow_run_at`; nothing else does. That is the same
    predicate `flow_clips` filters on, so what this deletes is exactly
    what the page counts, and a legacy clip cannot be caught by it.
    The count of those is taken before and after and returned, so a
    caller can assert on it rather than trust this docstring.

    THE CONSOLE'S OWN DELETES, not a third answer to "what does
    deleting this mean". Four real foreign keys point at video_clips
    -- camera_events.produced_clip_id is stamped on every clip the
    moment it is produced -- and the hand-rolled version is always the
    one missing a table.

    NO RECORDING IS TOUCHED. Going back to raw clips means going back
    to the recordings, not losing them.

    `upload_ids` None means everything; a set narrows it to those
    recordings, which is how the page's day and tee-box filters reach
    this.
    """
    from .admin import delete_clip

    out = {"cuts": 0, "clips": 0, "failed": 0,
           "legacy_before": 0, "legacy_after": 0}

    _legacy = db.query(func.count(VideoClip.id)).filter(
        VideoClip.flow_run_at.is_(None))
    out["legacy_before"] = _legacy.scalar() or 0

    # ── IDS, NOT ROWS ──────────────────────────────────────────────
    #
    # Both deletes commit, and a commit expires every object in the
    # session. Holding rows across the loop means each `.id` on the
    # next pass is a fresh SELECT for a row that may by then be gone
    # -- an ObjectDeletedError halfway through, with no way to tell
    # how far it had got. A list of integers cannot go stale.
    cq = db.query(VideoClip.id).filter(VideoClip.flow_run_at.isnot(None))
    kq = db.query(AudioCut.id)
    if upload_ids is not None:
        cq = cq.filter(VideoClip.long_upload_id.in_(upload_ids))
        kq = kq.filter(AudioCut.long_upload_id.in_(upload_ids))
    clip_ids = [r[0] for r in cq.all()]
    cut_ids = [r[0] for r in kq.all()]

    for cid in clip_ids:
        try:
            # keep_swing: the swing still happened. Its cut row is
            # about to go anyway, but failing halfway through must not
            # leave a recording's edit list mangled.
            delete_clip(cid, keep_swing=True, db=db)
            out["clips"] += 1
        except Exception as exc:  # noqa: BLE001
            # ROLLED BACK, OR THE REST FAIL TOO. One bad delete leaves
            # the session in a failed transaction and every delete
            # after it raises, so a single stuck row reported as a
            # total failure.
            db.rollback()
            out["failed"] += 1
            log.warning("flow reset: clip %s: %s", cid, exc)
    for kid in cut_ids:
        try:
            delete_flow_cut(kid, db=db)
            out["cuts"] += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out["failed"] += 1
            log.warning("flow reset: cut %s: %s", kid, exc)

    out["legacy_after"] = _legacy.scalar() or 0
    log.info("flow reset: deleted %d clip(s) and %d cut(s), %d failed; "
             "legacy clips %d -> %d", out["clips"], out["cuts"],
             out["failed"], out["legacy_before"], out["legacy_after"])
    return out


@router.post("/reset")
def flow_reset(course_id: int = 0, day: str = "", zone: int = -1,
               db: Session = Depends(get_db)):
    """Start over: throw away this flow's cuts and drafts.

    Takes the same filter the rest of the page does, so "start over"
    while looking at one day means that day. Resetting everything
    while the operator is looking at a filtered page would be a much
    larger action than the button they pressed appears to be.
    """
    _ids = _flow_filter(db, course_id, day, zone)
    out = reset_flow(db, upload_ids=_ids)
    if out["legacy_after"] != out["legacy_before"]:
        # Loud, because it would mean `flow_run_at` is not the fence
        # this is built on.
        log.error("flow reset: the legacy clip count CHANGED (%d -> %d)",
                  out["legacy_before"], out["legacy_after"])
    return {"ok": True, **out}


@router.get("/raw/{upload_id}")
def flow_raw_one(upload_id: int, db: Session = Depends(get_db)):
    """One recording, shaped exactly like a row of the raw list.

    The manual cutter needs the tee URL, the duration and the cuts it
    has already made. The list endpoint could answer that, but only by
    being asked for a page of forty and filtered down on the client --
    which breaks the moment the recording is not on the first page,
    and breaks silently, as an empty player.
    """
    from .admin import _local_tee

    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "long upload not found")

    def _dur(r):
        # Same probe, same refusal to fail the row over it, as the list.
        try:
            from ..services.video import probe_video_info
            _p = _local_tee(r)
            if not _p or not _p.exists():
                return None
            _d = (probe_video_info(_p) or {}).get("duration")
            return round(float(_d), 1) if _d else None
        except Exception:  # noqa: BLE001
            return None

    out = _raw_row(db, row, _dur)
    _c = db.get(Course, row.course_id) if row.course_id else None
    out["course_name"] = _c.name if _c else None
    out["cuts"] = (db.query(func.count(AudioCut.id))
                   .filter(AudioCut.long_upload_id == row.id).scalar() or 0)
    return out


@router.post("/raw/{upload_id}/manual-cut")
def flow_manual_cut(upload_id: int, payload: dict = Body(default={}),
                    db: Session = Depends(get_db)):
    """Cut MANUAL_CUT_SEC of both angles from where the operator said.

    No microphone, no gates, no judgement: somebody watched the
    recording and said "here". That is the entire input.

    IT MAKES A CUT, NOT A CLIP. A produce is minutes of sky-searching
    per swing, and queueing one behind every button press would turn
    a pass through a three-minute recording into an afternoon of
    waiting. These land in the Audio cuts stage and are produced from
    there, exactly like the cutter's own -- the stage means "a
    swing-sized piece of the recording", and how the piece was chosen
    is what `detector` is for.

    FRAME-ACCURATE, NOT FAST-SEEK. `cut_segment(fast=True)` is what
    the audio cutter uses and it snaps to the nearest keyframe --
    0-2s off the requested start, which its own docstring says not to
    use for deliverables. The cutter can afford that because it pads
    four seconds either side of a peak it measured; an operator who
    pressed the button ON the moment they wanted cannot, and a swing
    that starts 1.5s before the clip does is the whole clip wasted.
    So this re-encodes, and the page is built not to wait on it: the
    operator keeps watching while the cut finishes behind them.
    """
    from pathlib import Path

    from ..services.video import cut_segment, probe_video_info
    from .admin import (
        CLIPS_DIR, _audio_lag_sec, _clip_url_for, _local_green, _local_tee,
        _trigger_zone, probe_fps,
    )

    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "long upload not found")

    try:
        start = float(payload.get("start_sec"))
    except (TypeError, ValueError):
        raise HTTPException(400, "start_sec must be a number of seconds")
    if start < 0 or start != start:          # NaN fails both comparisons
        raise HTTPException(400, "start_sec must not be negative")

    src = _local_tee(row)
    if not src or not Path(src).exists():
        # THE SAME REASON THE RAW LIST HIDES IT. A recording whose
        # source video has been swept cannot be cut from, and saying
        # so is better than an ffmpeg error four layers down.
        raise HTTPException(
            409, "this recording's tee video is gone — it cannot be cut")

    _dur = None
    try:
        _dur = (probe_video_info(Path(src)) or {}).get("duration")
        _dur = float(_dur) if _dur else None
    except Exception:  # noqa: BLE001
        _dur = None
    if _dur and start >= _dur:
        raise HTTPException(
            400, "start_sec %.2f is past the end of this %.2fs recording"
                 % (start, _dur))

    end = start + MANUAL_CUT_SEC
    if _dur:
        # SHORT IS FINE, EMPTY IS NOT. Pressing the button nine
        # seconds from the end should give nine seconds, not refuse.
        end = min(end, _dur)

    _green_src = None
    try:
        _g = _local_green(row)
        _green_src = _g if (_g and Path(_g).exists()) else None
    except Exception:  # noqa: BLE001
        _green_src = None

    # The next free slot in the hand-cut range. See MANUAL_IDX_BASE.
    _max = (db.query(func.max(AudioCut.idx))
            .filter(AudioCut.long_upload_id == row.id,
                    AudioCut.idx >= MANUAL_IDX_BASE).scalar())
    idx = int(_max) + 1 if _max is not None else MANUAL_IDX_BASE

    _sub = CLIPS_DIR / FLOW_CUTS_SUBDIR
    _sub.mkdir(parents=True, exist_ok=True)
    _tok = "%d-%02d" % (row.id, idx)
    _tee_name = "flowcut-%s-tee.mp4" % _tok
    _green_name = "flowcut-%s-green.mp4" % _tok

    _tee_ok = _green_ok = False
    try:
        _tee_ok = bool(cut_segment(src, _sub / _tee_name, start, end))
    except Exception as exc:  # noqa: BLE001
        log.warning("flow: manual tee cut %s of %s failed: %s",
                    idx, row.id, exc)
    if not _tee_ok:
        # NOTHING RECORDED WITHOUT A FILE. A cut row whose tee angle
        # does not exist shows up in the stage as a cut that cannot be
        # opened or produced, and the operator has no way to tell it
        # from one that merely has not finished.
        raise HTTPException(500, "ffmpeg could not cut the tee angle")
    if _green_src is not None:
        try:
            _green_ok = bool(cut_segment(_green_src, _sub / _green_name,
                                         start, end))
        except Exception as exc:  # noqa: BLE001
            log.warning("flow: manual green cut %s of %s failed: %s",
                        idx, row.id, exc)

    _size = None
    try:
        _p = _sub / _tee_name
        _size = round(_p.stat().st_size / 1e6, 2) if _p.exists() else None
    except OSError:
        pass

    _zone = None
    try:
        _zone = _trigger_zone(db, row)
    except Exception:  # noqa: BLE001
        _zone = None
    try:
        fps = float(probe_fps(src) or 0.0) or 30.0
    except Exception:  # noqa: BLE001
        fps = 30.0
    try:
        _lag = float(_audio_lag_sec(db, row))
    except Exception:  # noqa: BLE001
        _lag = 0.0

    # ── WHERE THE STRIKE IS ASSUMED TO BE ──────────────────────────
    #
    # A manual cut has no measured peak -- nobody listened. But the
    # produce anchors on `impact_frame`, which is derived from one, so
    # leaving it empty would hand the produce a cut it cannot start
    # from, and `_cut_frames` would report no frames at all.
    #
    # FLOW_CUT_PRE_SEC IN gives the produce the same geometry it gets
    # from the cutter -- a cut whose strike sits four seconds from the
    # start -- so a manual cut and an automatic one behave alike
    # downstream. It is an ASSUMPTION, not a measurement, and
    # `detector="manual"` is what says so on the row: an operator
    # producing from this cut should expect to move the impact frame.
    _peak = start + FLOW_CUT_PRE_SEC
    if _dur:
        _peak = min(_peak, max(start, _dur - 0.01))

    k = record_cut(
        db, upload=row, idx=idx,
        peak_time_sec=round(_peak, 2),
        start_sec=round(start, 2), end_sec=round(end, 2),
        detector="manual", zone=_zone,
        tee_filename="%s/%s" % (FLOW_CUTS_SUBDIR, _tee_name),
        green_filename=("%s/%s" % (FLOW_CUTS_SUBDIR, _green_name)
                        if _green_ok else None),
        duration_sec=round(end - start, 2), size_mb=_size,
        fps=round(fps, 3), lag_sec=round(_lag, 3))
    if k is None:
        raise HTTPException(500, "the cut was made but could not be recorded")

    log.info("flow: manual cut upload=%s idx=%s %.2f-%.2fs green=%s",
             row.id, idx, start, end, _green_ok)
    return {
        "ok": True, "id": k.id, "idx": idx,
        "start_sec": round(start, 2), "end_sec": round(end, 2),
        "duration_sec": round(end - start, 2),
        "source_duration_sec": round(_dur, 2) if _dur else None,
        "tee_url": _clip_url_for("%s/%s" % (FLOW_CUTS_SUBDIR, _tee_name)),
        "green_url": (_clip_url_for("%s/%s" % (FLOW_CUTS_SUBDIR, _green_name))
                      if _green_ok else None),
    }


@router.delete("/raw/{upload_id}")
def delete_flow_raw(upload_id: int, db: Session = Depends(get_db)):
    """Throw a whole recording away: the cuts, the drafts and the files.

    THE CONSOLE'S OWN UPLOAD DELETE IS THE ONE THAT KNOWS THE FILES
    and the camera event behind them, so that part delegates. What it
    does NOT know is the flow: it says in its own docstring that
    "per-swing VideoClips produced from this upload are kept", which
    is right on a page where a produced clip is an independent thing
    and wrong here, where the row being deleted is the recording those
    clips are OF. Left behind they are drafts of a recording nobody
    can open, pointing at a `long_upload_id` that resolves to nothing.
    The cuts are worse: the rows cascade on the foreign key but the
    MP4s under `_flowcuts/` do not, so they become files in a folder
    no retention sweep walks.

    So the order is: refuse if anything has gone out, remove the
    drafts through the delete that knows their four foreign keys,
    clear the cuts and their files, and only then delete the
    recording.

    NOTHING BROADCAST IS DELETED BY A STRAY CLICK. A clip that has
    gone out is public; retracting one is a decision, and it is made
    on that clip rather than by deleting the thing it came from.
    """
    from .admin import delete_clip, delete_long_upload

    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "recording not found")
    _clips = (db.query(VideoClip)
              .filter(VideoClip.long_upload_id == upload_id).all())
    _out = [c for c in _clips if c.broadcast_at is not None]
    if _out:
        raise HTTPException(
            409,
            "%d clip%s from this recording %s been broadcast — "
            "un-broadcast %s first if you really mean to delete the "
            "recording they came from"
            % (len(_out), "" if len(_out) == 1 else "s",
               "has" if len(_out) == 1 else "have",
               "it" if len(_out) == 1 else "them"))

    _n_clips = 0
    for c in _clips:
        try:
            # `keep_swing` is irrelevant here -- the edit list goes
            # with the recording -- but the FOUR FOREIGN KEYS are
            # not, and this is the only delete that clears them.
            delete_clip(c.id, db=db)
            _n_clips += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("flow: could not delete clip %s of %s: %s",
                        c.id, upload_id, exc)
    _cuts = _clear_previous(db, row)
    _gone = delete_long_upload(upload_id, db=db)
    log.info("flow: recording %s deleted — %d clip(s), %d cut(s), "
             "%d cut file(s), %s bytes of source",
             upload_id, _n_clips, _cuts.get("cuts"), _cuts.get("files"),
             (_gone or {}).get("freed_bytes"))
    return dict(_gone or {}, ok=True, deleted=upload_id,
                clips=_n_clips, cuts=_cuts.get("cuts"),
                cut_files=_cuts.get("files"))


@router.get("/raw/{upload_id}/produce-report")
def flow_produce_report(upload_id: int, db: Session = Depends(get_db)):
    """What the last produce on this recording was trying to do.

    THE SAME RUN THE PRODUCTION PAGE NARRATES. The flow's phase 3 and
    the Production page's "Produce from ascents" are one function --
    `_produce_run` -- and the only difference between them is which
    key the live progress is filed under, which is what decides
    whether the card narrates it or the modal does. So the flow could
    run the identical detector and show none of its reasoning, and
    "why did it produce that" had no answer on this page.

    Served from the row rather than from a running process's memory,
    so it survives the run that made it and a backend restart. See
    `_reduce_produce_report` for what is kept and what is dropped.
    """
    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "recording not found")
    rep = row.produce_report
    return {
        "ok": bool(rep),
        "upload_id": upload_id,
        "at": (row.produce_report_at.isoformat()
               if row.produce_report_at else None),
        # NOT AN ERROR. A recording that has never been produced, or
        # one produced before this was kept, simply has nothing to
        # show -- and saying so is different from failing.
        "reason": None if rep else (
            "no produce report is kept for this recording — it has not "
            "been produced since the report started being saved. Start "
            "the flow, or produce it, and the work will be here."),
        "report": rep,
    }


@router.post("/raw/{upload_id}/claim")
def claim_clips(upload_id: int, db: Session = Depends(get_db)):
    """Adopt clips produced for this recording FROM this page.

    WHY THIS EXISTS. The columns show what the flow produced --
    `flow_run_at` is the stamp, and filtering on it is what stops a
    thousand unrelated clips burying the two an operator is looking
    at. The stamp is put on during a flow run.

    But editing a draft here, or adding a swing here, goes through the
    wizard's produce, and that writes a BRAND NEW clip row: the old
    one is replaced, the new one has no stamp, and the corrected draft
    therefore vanished from the column it was corrected in. Which
    reads as the edit having deleted the clip.

    So the flow adopts what it asked for. Called by the page once the
    produce it started has finished, and deliberately NOT on every
    poll: a clip produced from the Production page is not this flow's
    output, and quietly claiming it would make the stamp mean "some
    clip of a recording the flow has touched" instead.

    Idempotent. Clips already stamped keep their stamp and their cut.
    """
    row = db.get(LongVideoUpload, upload_id)
    if not row:
        raise HTTPException(404, "recording not found")
    # THE CUT WINDOWS FROM THE ROWS, in the shape `_claim_clips` reads
    # them -- it was written against a run's report, and the report is
    # long gone by the time an operator edits a draft.
    cuts = [{"id": k.id, "start_sec": k.start_sec, "end_sec": k.end_sec}
            for k in (db.query(AudioCut)
                      .filter(AudioCut.long_upload_id == upload_id)
                      .order_by(AudioCut.idx.asc()).all())]
    claimed, matched = _claim_clips(db, row, cuts)
    return {"ok": True, "upload_id": upload_id,
            "claimed": claimed, "matched": matched,
            "cuts": len(cuts)}


@router.get("/raw/{upload_id}/status")
def flow_status(upload_id: int):
    """How the run is going, or how it went.

    AND A WIZARD PRODUCE ON THE SAME RECORDING, which is a different
    run under a different key and was invisible here.

    THE FAILURE THIS FIXES. Editing a draft from this page calls the
    wizard's produce, which RETURNS AS SOON AS THE JOB IS QUEUED --
    the render takes a minute on a thread. The page reloaded its
    columns the instant the modal closed, read the clip that was still
    being replaced, and then never asked again. So the draft an
    operator had just corrected sat there as the old one until they
    reloaded the whole page by hand, which looks exactly like the edit
    having done nothing.

    One `running` for the page to poll, whichever kind started the
    work, so the existing tick covers both: it reloads the columns
    when the work stops, and that is when there is something new to
    see. `produce_running` stays separate because the two are not the
    same event and a caller may want to say which.
    """
    from .admin import _debugx_get, _debugx_status, _json_safe

    out = dict(_debugx_status("flow", upload_id) or {})
    _p = _debugx_get("produce", upload_id) or {}
    _busy = bool(_p.get("running"))
    out["produce_running"] = _busy
    out["produce_stage"] = _p.get("stage") if _busy else None
    out["produce_done"] = _p.get("done") if _busy else None
    out["produce_total"] = _p.get("total") if _busy else None
    # NOT `or` ON THE REPORT. `running` is the only field folded
    # together: the flow run's report is the flow run's, and a produce
    # finishing must not make this claim the flow produced it.
    out["running"] = bool(out.get("running")) or _busy
    return _json_safe(out)


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