#!/usr/bin/env python3
"""What frame did the AUDIO say the strike was on?

THE QUESTION THAT COULD NOT BE ASKED. The cut is recorded in seconds,
because that is what a microphone deals in. Everything downstream --
the produce, the tracer, the window on the card -- is in frames. So
"the cut is at 33.48s" and "it produced impact frame 2009" were two
numbers nobody could put side by side without knowing the recording's
frame rate and the hole's audio lag by heart, neither of which was
anywhere near the row.

Which means a produce working off a frame the audio never pointed at
looked exactly like one working off the right frame. The cut now
carries the frame rate it was made at and the lag that was in force,
and reports the peak frame and the strike frame it implies.

AND THE LAG IS A STORED, EDITABLE, PER-TEE-BOX VALUE. It always was
(`audio_lag_sec`), and there was nowhere to see or set it -- the one
number on the screen that decided which frame a heard strike became,
reachable only by hand in the database.

    python3 tools/cut_frames_test.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AudioCut, Camera, CameraEvent, Course, LongVideoUpload,
)
from app.routers.admin import (  # noqa: E402
    _audio_lag_sec, get_audio_gates, set_gate_tuning,
)
from app.routers.production_flow import _cut_frames, record_cut  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    for _i, _r in ((1, "tee"), (2, "green")):
        db.add(Camera(id=_i, course_id=1, assigned_hole=8,
                      assigned_role=_r, auth_token="t%d" % _i))
    # A GATE IS TUNED AGAINST A PAIR-AND-HOLE, never against one
    # upload -- so the fixture needs the camera event that says which
    # two cameras shot this and which hole they cover, or there is
    # nothing to file a tuning under. The upload points AT the event,
    # not the other way round.
    ev = CameraEvent(id=1, session_id="s1", tee_camera_id=1,
                     green_camera_id=2, course_id=1, hole_number=8,
                     triggered_at=datetime.now())
    db.add(ev)
    db.commit()
    db.add(LongVideoUpload(id=1916, course_id=1, tee_filename="t.mp4",
                           camera_event_id=ev.id,
                           base_captured_at=datetime.now()))
    db.commit()
    return db


print(__doc__.strip().splitlines()[0])

print("\n1. the peak, in frames")
db = a_db()
up = db.get(LongVideoUpload, 1916)
# THE REAL NUMBERS FROM #1916: a peak at 33.48s on a 60fps recording.
k = record_cut(db, upload=up, idx=0, peak_time_sec=33.48, fps=60.0,
               lag_sec=0.0)
f = _cut_frames(k)
check("the peak's own frame", f["peak_frame"], 2009)
check("the frame rate travels with it", f["fps"], 60.0)
# AT ZERO LAG THE PEAK *IS* THE STRIKE, and that is an assertion about
# this hole's rig, not a neutral absence. 2009 is then the audio's own
# answer -- so a produce that lands on f2009 agreed with the audio,
# which is the opposite of what it looks like.
check("with no lag, the strike is the peak", f["impact_frame"], 2009)
check("...and the shift is zero frames", f["lag_frames"], 0)

print("\n2. a lag moves the strike, and only the strike")
k2 = record_cut(db, upload=up, idx=1, peak_time_sec=33.48, fps=60.0,
                lag_sec=0.317)
f2 = _cut_frames(k2)
check("the peak frame is unchanged", f2["peak_frame"], 2009)
check("the lag in frames", f2["lag_frames"], 19)
check("the strike is that much earlier", f2["impact_frame"], 1990)
# 19 frames at 60fps is the measured lag on this course, and 1920 is
# 89 frames before the peak -- 1.48s, which no lag explains. So a
# produce landing on 1920 did not get there by subtraction.
check("a lag cannot reach f1920 from this peak",
      f2["impact_frame"] != 1920, True)

print("\n3. a cut keeps what it was made with")
# RESOLVED AT CUT TIME, NOT ON READ. Otherwise changing the lag
# tomorrow silently rewrites what every earlier cut was made with, and
# "the lag was 0.00s then and is 0.32s now" becomes untellable.
k2.lag_sec = 0.0
db.commit()
check("changing the row changes its own answer",
      _cut_frames(k2)["impact_frame"], 2009)
check("...and the other cut is untouched",
      _cut_frames(db.get(AudioCut, k.id))["impact_frame"], 2009)

print("\n4. no frame rate means no frame, not a guess")
k3 = record_cut(db, upload=up, idx=2, peak_time_sec=33.48)
f3 = _cut_frames(k3)
check("a cut from before the column says nothing",
      f3["peak_frame"], None)
check("...rather than guessing 30 or 60", f3["impact_frame"], None)
# A frame number off by a factor of two is worse than no frame number:
# it reads as a measurement.

print("\n5. the lag is one stored value, per tee box")
db2 = a_db()
row = db2.get(LongVideoUpload, 1916)
check("it defaults to zero", _audio_lag_sec(db2, row), 0.0)
_names = {g["name"]: g for g in get_audio_gates(1916, db=db2)["gates"]}
check("and it is on the panel", "audio_lag_sec" in _names)
_lag_g = _names.get("audio_lag_sec") or {}
check("...in its own group, not among the thresholds",
      _lag_g.get("group"), "timing")
# "min audio lag 0.15s" is not what the number means.
check("...with no min/max direction", _lag_g.get("cmp"), None)
# Zero is a real value here (the peak IS the strike), not "off", so
# the audio1 off-switch must not be offered for it.
check("...and no off switch", _lag_g.get("off_is"), None)
check("...but it bites as soon as it is saved",
      _lag_g.get("detector_gate"), True)

print("\n6. saving it from the flow page changes what the next cut uses")
set_gate_tuning(1916, {"gates": {"audio_lag_sec": 0.317}, "merge": True},
                db=db2)
check("the resolver sees it", round(_audio_lag_sec(db2, row), 3), 0.317)
_v = {g["name"]: g for g in get_audio_gates(1916, db=db2)["gates"]}
check("the panel reads it back", _v["audio_lag_sec"]["value"], 0.317)
check("...and says it was set, not defaulted",
      _v["audio_lag_sec"]["source"] in ("hole", "tee box"), True)
# AND THE RUN SAYS IT OUT LOUD. A lag of zero is the interesting case
# and the silent one, so the sentence mentions it either way.
_sent = get_audio_gates(1916, db=db2)["sentence"]
check("the gates sentence carries the lag", "audio lag" in _sent)
db3 = a_db()
check("...including when it is zero",
      "the peak is taken as the strike"
      in get_audio_gates(1916, db=db3)["sentence"])

print("\n7. and the draft says whose frame it was produced from")
# THE QUESTION, VERBATIM: "it identified impact frame as 2009 instead
# of 1920 -- just curious what the audio cut identified it as?" The
# answer belongs on the card, beside the number being asked about.
from app.models import VideoClip  # noqa: E402
from app.routers.production_flow import flow_clips  # noqa: E402

db4 = a_db()
up4 = db4.get(LongVideoUpload, 1916)
k4 = record_cut(db4, upload=up4, idx=0, peak_time_sec=33.48, fps=60.0,
                lag_sec=0.0)
c4 = VideoClip(id=5, course_id=1, hole_number=8, camera_type="tee",
               captured_at=datetime.now(), long_upload_id=1916,
               audio_cut_id=k4.id, flow_run_at=datetime.utcnow(),
               source_url="http://x/uploads/clips/d3prod-1916-ab-0.mp4")
db4.add(c4)
up4.edit_metrics = {"swings": [{
    "idx": 0, "clip_id": 5, "start_frame": 1890, "end_frame": 2370,
    "impact_frame": 2009,
    "impact_from": "the audio cutter's peak at 33.48s less the 0.00s "
                   "audio lag"}]}
db4.commit()
_r = (flow_clips(flow_only=0, db=db4).get("clips") or [{}])[0]
check("the audio's frame is on the clip row",
      _r.get("audio_impact_frame"), 2009)
check("the peak it came from too", _r.get("audio_peak_sec"), 33.48)
check("and the lag that was applied", _r.get("audio_lag_sec"), 0.0)
check("agreement says nothing alarming", _r.get("impact_differs"), None)
check("...and still shows how the frame was arrived at",
      "less the 0.00s" in str(_r.get("impact_from")), True)

# NOW MOVE THE PRODUCE'S ANSWER and the gap is spelled out: frames,
# seconds, and where the number came from. 1920 is 89 frames before
# the audio's 2009 -- 1.48s, which no plausible lag explains.
up4.edit_metrics = {"swings": [dict(
    up4.edit_metrics["swings"][0], impact_frame=1920,
    impact_from="measured from the ball leaving its spot")]}
db4.commit()
_r2 = (flow_clips(flow_only=0, db=db4).get("clips") or [{}])[0]
_msg = str(_r2.get("impact_differs") or "")
check("a disagreement is spelled out", bool(_msg))
check("...naming the audio's frame", "f2009" in _msg)
check("...and the one produced", "f1920" in _msg)
check("...the gap in frames", "89 frame" in _msg)
check("...the gap in seconds", "1.48s" in _msg)
check("...and where the produce's number came from",
      "measured from the ball" in _msg)
print("     %s" % _msg)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
