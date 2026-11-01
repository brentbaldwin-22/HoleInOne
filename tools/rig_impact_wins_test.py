#!/usr/bin/env python3
"""When the rig HEARD the strike, that is the impact frame.

#1265: the microphone heard the strike 4.88s into the clip. The lead
puts the real instant at 4.63s, which on a 60fps camera is f277 -- and
the operator stepped the raw video and confirmed f277 is the strike.
The edit wizard showed f289.

WHERE 289 CAME FROM. `_trace_segment` runs Debug3's `find_flight`
before using the pin, and when the fit succeeds it REPLACED the pin:

    verified_impact_frame = int(_d3["launch_frame"])   # was unconditional

`launch_frame` is the fitted flight extrapolated back to a ground
line. On #1265 it landed at f289 -- one frame before the first
detection, eleven frames after the strike. So the one number in the
pipeline that was measured rather than fitted was being thrown away by
the fit, on every audio-triggered clip, and the audio lead had no
effect on the impact frame at all.

Now: the rig's arithmetic wins wherever it exists, on both produce
paths, and Debug3 keeps the jobs it is good at -- where the ball was
and where it went.

    python3 tools/rig_impact_wins_test.py
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class _Ev:
    def __init__(self, heard_sec, started=None, hole=8):
        self.tee_recording_started_at = started or datetime(2026, 10, 29, 16, 11)
        self.impact_at = (None if heard_sec is None
                          else self.tee_recording_started_at
                          + timedelta(seconds=heard_sec))
        self.hole_number = hole


class _Db:
    """Just enough of a Session: one event, fetched by id."""

    def __init__(self, ev):
        self._ev = ev

    def get(self, _model, _id):
        return self._ev


class _Row:
    def __init__(self, ev_id=42):
        self.id = 1265
        self.camera_event_id = ev_id


FPS = 60.0
LEAD = float(admin.settings.audio_impact_lead_sec)

# ── 1. the arithmetic, once ──────────────────────────────────────────
print("the rig's instant, placed in the clip:")
_rig = admin._rig_impact_sec(_Db(_Ev(4.88)), _Row(), 1265)
check("it heard the strike at 4.88s", round(_rig["heard_sec"], 2), 4.88)
check("the lead is applied", round(_rig["sec"], 2),
      round(4.88 - LEAD, 2))
# THE FRAME THE INSTANT IS IN, which is the floor: f277 spans 4.617s
# to 4.633s and 4.63s is inside it. Rounding to the nearest boundary
# gives f278 instead -- one frame after the strike, and the operator
# stepped the raw video to check.
check("...which on #1265's camera is f277", int(_rig["sec"] * FPS), 277)
check("...where rounding would have said f278",
      int(round(_rig["sec"] * FPS)), 278)
# WHAT THE MICROPHONE SAID IS KEPT. A report carrying only the
# corrected number cannot be checked against the rig's own log, and
# the gap between the two is the evidence for what the lead should be.
check("what it heard is carried too, not just what was used",
      round(_rig["heard_sec"] - _rig["sec"], 2), round(LEAD, 2))
check("a dwell clip has nothing to say",
      admin._rig_impact_sec(_Db(_Ev(None)), _Row(), 1265), None)
check("...and so does an upload with no camera event",
      admin._rig_impact_sec(_Db(_Ev(4.88)), _Row(ev_id=None), 1265), None)
check("an instant before the clip starts cannot go negative",
      admin._rig_impact_sec(_Db(_Ev(0.10)), _Row(), 1265)["sec"], 0.0)

# ONE READER, NOT THREE. Each path used to turn `impact_at` into an
# offset with its own copy, kept in step by hand.
_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")
print()
print("there is one copy of it:")
check("the produce pin asks the helper",
      "_rig = _rig_impact_sec(db, row, upload_id)" in _src, True)
check("the ascent search window asks the helper",
      "_rig = _rig_impact_sec(db, row, getattr(row, \"id\", None))" in _src,
      True)
check("the lead is subtracted in exactly one place",
      len(re.findall(r"- _lead\b", _src)), 1)
check("...and that place is the helper",
      bool(re.search(r"def _rig_impact_sec\(.*?- _lead", _src, re.S)), True)
check("the setting is read once, by it",
      len(re.findall(r'getattr\(settings, "audio_impact_lead_sec"', _src)), 1)

# ── 2. the ascent path pins the heard swing ──────────────────────────
print()
print("the ascent run gives the heard swing the heard frame:")
_rep = {"clips": [{"ball": [100, 200], "impact_frame": 289,
                   "impact_sec": 4.82}]}
_note = admin._ascent_pin_rig_impact(_Db(_Ev(4.88)), _Row(), _rep, FPS)
check("the swing now carries the rig's frame",
      _rep["clips"][0]["impact_frame"], 277)
check("...and says so", _rep["clips"][0]["impact_from"],
      "the rig's audio impact")
# THE FIT IS KEPT, NOT DISCARDED. The two disagreeing is the
# measurement that says what the lead should be; throwing the fit away
# destroys the only evidence there is.
check("the chain's own answer is kept beside it",
      _rep["clips"][0]["impact_frame_fit"], 289)
check("the report says it was applied", _note["applied"], True)
check("...and by how much it moved", _note["moved_frames"], -12)

# A SECOND SWING MUST NOT BE GIVEN SOMEBODY ELSE'S STRIKE.
_rep2 = {"clips": [{"ball": [1, 2], "impact_frame": 291},
                   {"ball": [3, 4], "impact_frame": 2400}]}
admin._ascent_pin_rig_impact(_Db(_Ev(4.88)), _Row(), _rep2, FPS)
check("the nearest swing takes it", _rep2["clips"][0]["impact_frame"], 277)
check("...and the one 35s away is left alone",
      _rep2["clips"][1]["impact_frame"], 2400)
check("...which is also left unlabelled",
      _rep2["clips"][1].get("impact_from"), None)

# AND NOT AT ALL WHEN NOTHING IS NEAR IT. A dwell clip that happens to
# carry an impact from a different shot must not drag a good frame.
_rep3 = {"clips": [{"ball": [1, 2], "impact_frame": 1800}]}
_n3 = admin._ascent_pin_rig_impact(_Db(_Ev(4.88)), _Row(), _rep3, FPS)
check("a swing 25s from the strike keeps its own frame",
      _rep3["clips"][0]["impact_frame"], 1800)
check("...and the report says why not", _n3["applied"], False)
check("...with the distance named", "too far" in (_n3["reason"] or ""), True)

_rep4 = {"clips": [{"ball": [1, 2], "impact_frame": 289}]}
check("a dwell clip changes nothing",
      admin._ascent_pin_rig_impact(_Db(_Ev(None)), _Row(), _rep4, FPS), None)
check("...and the swing is untouched", _rep4["clips"][0]["impact_frame"], 289)

# ── 3. the produce path does not let the fit overrule it ─────────────
print()
print("the produce path locks it against the flight fit:")
_ts = _src[_src.index("def _trace_segment("):]
_ts = _ts[:_ts.index("\ndef ", 10)]
check("the tracer takes a lock", "impact_locked=False" in _ts, True)
check("the fit may not replace a locked frame",
      bool(re.search(r"if impact_locked and verified_impact_frame "
                     r"is not None:", _ts)), True)
# EVERYTHING DOWNSTREAM READS `launch_frame` -- the render, the plot
# background and the reported impact -- so it is moved once, there,
# rather than at each of the three.
check("...and the locked frame becomes the one everything renders from",
      '_d3["launch_frame"] = int(verified_impact_frame)' in _ts, True)
check("the fit's own answer is still reported",
      '"impact_frame_fit": _d3.get("launch_frame_fit")' in _ts, True)
check("a swing with no heard impact still takes the fit",
      "verified_impact_frame = int(_d3[\"launch_frame\"])" in _ts, True)
check("both produce call sites set the lock",
      _src.count("impact_locked=bool(seg.get(\"from_rig_audio\"))"), 2)
check("...and from_rig_audio is what the rig pin sets",
      '"from_rig_audio": True' in _src, True)

# ── 4. it reaches the screen ─────────────────────────────────────────
print()
print("the wizard says where the frame came from:")
check("the swing record carries the source",
      'nsw["impact_from"] = (tracer_info.get("impact_from")' in _src, True)
check("...in the same frame base as the frame itself",
      'int(_impfit) + offset' in _src, True)
check("...and the ascent path records it too",
      '_rec["impact_from"] = c["impact_from"]' in _src, True)
_jsx = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text(encoding="utf-8")
check("the impact field prints it", "swing.impact_from &&" in _jsx, True)
check("...and the fit it disagreed with",
      "the flight fit said f${swing.impact_frame_fit}" in _jsx, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — a heard impact is the impact")
