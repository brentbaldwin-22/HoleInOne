#!/usr/bin/env python3
"""The model looks at the launch before the sweep looks for it.

THE PART OF THE FLIGHT NOTHING MEASURED. The ascent sweep searches a
band ABOVE the golfers, because that is the only place a ball can be
told apart from a club, a cap, a divot and a shadow. So the climb from
the tee INTO the band is never observed: it was run backwards out of
the chain afterwards, which is a fit, and the first thing the tracer
draws was the one piece of the flight nobody had looked at.

On a clip the rig heard, the impact frame is known without the chain,
the sweep or any fit. That is enough to zoom into the tee box on that
frame, find the ball sitting there, and follow it out -- five pictures,
every third frame, covering a dozen frames of the climb.

Two answers, used differently: the ball at address is an OBSERVATION of
what the back-run only estimates, so it becomes the tee spot; the
flight points are merged into the front of the chain, through a
corridor, because one bad point at the foot drags the whole fitted
curve through it.

    python3 tools/ai_launch_plot_test.py
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
    def __init__(self, heard_sec):
        self.tee_recording_started_at = datetime(2026, 10, 29, 16, 11)
        self.impact_at = (None if heard_sec is None
                          else self.tee_recording_started_at
                          + timedelta(seconds=heard_sec))
        self.hole_number = 8


class _Db:
    def __init__(self, ev):
        self._ev = ev

    def get(self, _m, _i):
        return self._ev


class _Row:
    id = 1265
    camera_event_id = 42


# ── 1. the corridor arithmetic ───────────────────────────────────────
print("distance from a point to the launch line:")
check("a point on the line is on it",
      round(admin._point_to_segment_px(5, 5, 0, 0, 10, 10), 3), 0.0)
check("a point beside it is its perpendicular distance",
      round(admin._point_to_segment_px(0, 10, 0, 0, 10, 0), 1), 10.0)
# PAST THE END IS NOT ON THE LINE. A segment, not an infinite line:
# a find beyond the chain's foot is measured from the foot, which is
# what stops a wild point far up the frame reading as "on the line".
check("past the end it is measured from the end",
      round(admin._point_to_segment_px(20, 0, 0, 0, 10, 0), 1), 10.0)
check("a zero-length line does not divide by zero",
      round(admin._point_to_segment_px(3, 4, 0, 0, 0, 0), 1), 5.0)

# ── 2. the plot lands on the swing ───────────────────────────────────
#
# Geometry: the ball sits at (700,900) and the chain's foot is at
# (760,500) on f300. The climb between them is a straight line, and
# the AI points are placed on it.
def _rep(ai_points, rest=(700, 900), chain_first=300):
    return {
        "clips": [{"ball": [690, 880], "impact_frame": 277}],
        "ascents": [{"points": [
            {"frame": chain_first + i, "x": 760 + i, "y": 500 - 20 * i}
            for i in range(6)]}],
        "ai_launch": {
            "ran": True, "n_points": len(ai_points),
            "points": [{"frame": f, "x": x, "y": y}
                       for f, x, y in ai_points],
            "rest": ({"frame": 277, "x": rest[0], "y": rest[1]}
                     if rest else None),
            "reason": "AI plotted %d/4 launch frames" % len(ai_points),
        },
    }


print()
print("the ball at address becomes the tee spot:")
r = _rep([(280, 710, 840), (283, 725, 760), (286, 740, 680)])
out = admin._ascent_apply_ai_launch(r, 0)
check("the swing's ball is the model's find", r["clips"][0]["ball"],
      [700, 900])
check("...and says where it came from", r["clips"][0]["ball_source"],
      "the AI launch plot")
# THE ESTIMATE IS KEPT. The two disagreeing by a lot is the signal
# that one of them is wrong, and discarding the chain's answer throws
# away the only thing there is to compare against.
check("the chain's own estimate is kept beside it",
      r["clips"][0]["ball_fit"], [690, 880])
check("...with the distance between them named",
      out["ball_moved_px"], 22.4)

print()
print("the climb goes on the front of the chain:")
check("all three points merged", out["n_merged"], 3)
check("none refused", out["n_refused"], 0)
_pts = r["ascents"][0]["points"]
check("the chain now starts at the earliest AI frame",
      min(int(q["frame"]) for q in _pts), 280)
check("...and is in frame order",
      [int(q["frame"]) for q in _pts]
      == sorted(int(q["frame"]) for q in _pts), True)
check("the merged points are marked as the model's",
      [q.get("from") for q in _pts if int(q["frame"]) < 300],
      ["ai", "ai", "ai"])
check("the chain's first_frame is corrected",
      r["ascents"][0]["first_frame"], 280)

print()
print("a point that is not on the launch line is refused:")
# 400px to the side of a line the chain and the ball define: a
# clubhead, a cap, a reflection off a cart -- not the ball.
r2 = _rep([(280, 710, 840), (283, 1150, 760)])
out2 = admin._ascent_apply_ai_launch(r2, 0)
check("the good one is kept", out2["n_merged"], 1)
check("the wild one is refused", out2["n_refused"], 1)
check("...and the refusal says how far off it was",
      "off the launch line" in out2["refused"][0]["why"], True)
check("...and which frame it was",
      out2["refused"][0]["frame"], 283)
check("the chain did not take it",
      any(int(q["x"]) == 1150 for q in r2["ascents"][0]["points"]), False)

print()
print("the chain owns everything from its own foot up:")
# The band is where a ball IS distinguishable, and the sweep measured
# it there. This step is for what is below the band.
# f310, NOT f301: the chain itself holds f300-f305, so a point in
# there is refused for being a frame the sweep already has and this
# would have tested the wrong rule.
r3 = _rep([(280, 710, 840), (310, 766, 380)])
out3 = admin._ascent_apply_ai_launch(r3, 0)
check("a point at or above the foot is refused", out3["n_refused"], 1)
check("...for that reason",
      "foot" in out3["refused"][0]["why"], True)

r4 = _rep([(300, 760, 500)])
out4 = admin._ascent_apply_ai_launch(r4, 0)
check("a frame the sweep already has is refused", out4["n_refused"], 1)
check("...for that reason",
      "already has" in out4["refused"][0]["why"], True)

print()
print("it declines rather than guesses:")
r5 = _rep([(280, 710, 840)])
r5["ai_launch"] = {"ran": False, "reason": "no tee box"}
check("a plot that did not run changes nothing",
      admin._ascent_apply_ai_launch(r5, 0)["applied"], False)
check("...and the chain is untouched",
      min(int(q["frame"]) for q in r5["ascents"][0]["points"]), 300)
r6 = _rep([(280, 710, 840)])
check("no matched swing means nothing to attach it to",
      admin._ascent_apply_ai_launch(r6, None)["applied"], False)

# ── 3. the stage itself ──────────────────────────────────────────────
# ── 2b. the box comes from the golfer, not the hole ──────────────────
print()
print("two boxes overlapped:")
check("the overlap of two boxes is the part in both",
      admin._rect_overlap_px((0, 0, 100, 100), (50, 50, 100, 100)),
      (50, 50, 50, 50))
check("boxes that miss have no overlap",
      admin._rect_overlap_px((0, 0, 40, 40), (200, 200, 40, 40)), None)
# A SLIVER IS NOT AN OVERLAP. Clipping a search box down to four
# pixels of agreement would hand the model a crop with no ball and no
# context in it; the caller wants to hear "these two disagree".
check("a sliver is not an overlap",
      admin._rect_overlap_px((0, 0, 100, 100), (97, 0, 100, 100)), None)

print()
print("the golfer's feet, from the motion heat:")
# THE GEOMETRY, MEASURED. A mask with a body-shaped blob in it: 24px
# wide, 90 tall, feet at y=190, on a detector frame half the size of
# the video -- which is the case that goes wrong silently if the
# scaling is dropped.
import numpy as _np  # noqa: E402

from app.services import tracer as _tr  # noqa: E402

_mask = _np.zeros((240, 320), _np.uint8)
_mask[100:190, 148:172] = 1          # the body
_mask[60:64, 20:300] = 1             # a wire across the sky: wider than tall
_mask[200:203, 40:43] = 1            # a speckle in the grass
_g, _bb = _tr.golfer_and_ball_box(_mask, 0.5, 640, 480)
print("      golfer %s, ball box %s" % (_g, _bb))
check("the body is the golfer, in the VIDEO's pixels, not the detector's",
      _g, [296, 200, 48, 180])
check("...so a wire across the sky is not one", _g[1] > 100, True)
check("the ball box is at the feet",
      _bb[1] <= 380 <= _bb[1] + _bb[3], True)
# A ball at address sits beside the feet, not under the hands, and the
# club is up there -- a box that reaches the hands invites the model
# to pick the clubhead.
check("...and does not reach the hands",
      _bb[1] > _g[1] + _g[3] // 2, True)
check("the box is three body-widths across", _bb[2], 144)
check("...centred on the body",
      abs((_bb[0] + _bb[2] / 2) - (_g[0] + _g[2] / 2)) <= 1, True)
check("an empty mask has no golfer in it",
      _tr.golfer_and_ball_box(_np.zeros((240, 320), _np.uint8), 0.5,
                              640, 480), (None, None))
_shake = _np.zeros((240, 320), _np.uint8)
_shake[20:220, 20:300] = 1
check("a component the size of the picture is the camera, not a golfer",
      _tr.golfer_and_ball_box(_shake, 0.5, 640, 480), (None, None))

_src_t = (ROOT / "backend" / "app" / "services"
          / "tracer.py").read_text(encoding="utf-8")
_hc = _src_t[_src_t.index("def swing_heat_check("):]
_hc = _hc[:_hc.index("\nSWING_HEAT") if "\nSWING_HEAT" in _hc else len(_hc)]
# THE BODY IS THE CONSTANT MOTION, which is the mask the fan band
# already subtracts itself from: the club is a different pixel every
# frame, the grass does not move, and a cart crossing the back of the
# shot is somewhere else each frame.
check("the swing check asks for the boxes",
      "golfer_and_ball_box(constant, det_scale, width," in _hc, True)
# AND AIMS THE PICK. Largest-blob picked the partner leaning on a
# trolley over the golfer mid-swing; the spot the flight chain runs
# back to cannot pick the wrong person, because it starts at the ball
# that was actually struck. See tools/swing_aim_test.py.
check("...aimed at the spot the swing came from",
      "aim=_tee" in _hc, True)
check("...which is the flight chain run back to the impact frame",
      "tee_spot_from_chain(chain, impact_f" in _hc, True)
check("...from the constant motion, which is the body",
      "const_thr = max(5, int(0.15 * counted))" in _hc, True)
check("a failure there is a missing box, not a failed check",
      "DIAGNOSTIC, NOT LOAD-BEARING" in _hc, True)

_run_src = (ROOT / "backend" / "app" / "routers"
            / "admin.py").read_text(encoding="utf-8")
_al = _run_src[_run_src.index("def _ascent_ai_launch("):]
_al = _al[:_al.index("\ndef ", 10)]
check("the launch plot asks the swing check first",
      "_heat = _ascent_swing_heat(" in _al, True)
check("...and zooms on the box it gives back",
      '_rect = tuple(int(v) for v in _heat["ball_box"])' in _al, True)
check("...kept inside the hole's tee box when there is one",
      "_rect_overlap_px(" in _al, True)
# THE WHOLE LADDER, IN ORDER, not one comparison out of it.
#
# This used to pin `elif _box:`, which the chain outgrew: the tee box
# stopped being an alternative SOURCE and became a clip applied to
# the measured box, with a last-resort branch of its own further
# down. The literal vanished, .index() raised ValueError, and the
# check had not run since -- taking the rest of this file with it,
# because the raise is at module level.
#
# What it was protecting is unchanged and worth more said in full:
# every rectangle measured TODAY is tried before the one drawn last
# month. The markers move every morning.
_LADDER = [
    ("the golfer crop Test Cut searched", '_heat.get("frames_rect")'),
    ("the heat's own ball box", '_heat.get("ball_box")'),
    ("the golfer the rig already picked", '_kept.get("golfer_box")'),
    ("the person detector", "find_people_over_time"),
    ("and only then the hole's tee box", "if _rect is None and _box:"),
]
# SCOPED TO THE LADDER, which starts where the rect does. `_kept`
# and the person detector are both named earlier, in the section that
# decides what can be REUSED from the rig's verify call -- so a bare
# .index() over the whole function finds those and reports the ladder
# out of order.
_lad = _al[_al.index("_rect = None"):]
_at = []
for _name, _needle in _LADDER:
    check("  tries %s" % _name, _needle in _lad, True)
    _at.append((_lad.index(_needle) if _needle in _lad else -1, _name))
check("the tee box is the LAST choice, not the first",
      [n for _, n in _at] == [n for _, n in sorted(_at)], True)
check("a clip with neither says so rather than guessing",
      "nowhere to zoom in on" in _al, True)
check("the modal shows the heat the box came from",
      "rep.ai_launch.swing_check?.ran" in (
          ROOT / "frontend" / "src" / "pages"
          / "AdminProduction.jsx").read_text(encoding="utf-8"), True)

print()
print("the stage declines cleanly when it cannot run:")
_was = admin.settings.ascent_ai_launch
try:
    admin.settings.ascent_ai_launch = False
    _o = admin._ascent_ai_launch(_Db(_Ev(4.88)), _Row(), Path("/nope.mp4"),
                                 60.0, {}, {"roi": {"x": 0.1, "y": 0.1,
                                                    "w": 0.2, "h": 0.2}})
    check("the switch turns it off", _o["ran"], False)
    check("...and says so", "switched off" in _o["reason"], True)
finally:
    admin.settings.ascent_ai_launch = _was

_o2 = admin._ascent_ai_launch(_Db(_Ev(None)), _Row(), Path("/nope.mp4"),
                              60.0, {}, {"roi": {"x": 0.1, "y": 0.1,
                                                 "w": 0.2, "h": 0.2}})
check("a dwell clip has no frame to zoom in on", _o2["ran"], False)
check("...and the reason says what happens instead",
      "back to the tee line" in _o2["reason"], True)

# NO BOX FROM EITHER SOURCE, on a real clip with nobody in it: the
# swing check runs and places no golfer, and the hole has no tee box
# to fall back to. The one case with nowhere at all to look.
import tempfile  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_td = tempfile.mkdtemp()
_empty = Path(_td) / "empty.mp4"
_vw = cv2.VideoWriter(str(_empty), cv2.VideoWriter_fourcc(*"mp4v"),
                      60.0, (320, 240))
_rng = np.random.default_rng(5)
for _i in range(360):
    _vw.write(_rng.integers(40, 46, (240, 320, 3), dtype=np.uint8))
_vw.release()

_o3 = admin._ascent_ai_launch(_Db(_Ev(4.88)), _Row(), _empty, 60.0, {},
                              {"roi": None})
check("nobody in the clip and no tee box, no zoom", _o3["ran"], False)
check("...and it says both halves came up empty",
      "nowhere to zoom in on" in (_o3["reason"] or ""), True)
check("...having actually run the swing check",
      bool(_o3.get("swing_check")), True)

# WITH A TEE BOX IT FALLS BACK TO IT rather than giving up, and says
# which of the two it is searching.
_o4 = admin._ascent_ai_launch(_Db(_Ev(4.88)), _Row(), _empty, 60.0, {},
                              {"roi": {"x": 0.3, "y": 0.5,
                                       "w": 0.3, "h": 0.3}})
check("a tee box is the fallback", _o4.get("rect"), [96, 120, 96, 72])
check("...and the card says it is the fallback",
      "could not place the golfer" in (_o4.get("rect_from") or ""), True)
# IT NEVER RAISES. A diagnostic step that can take down a produce is
# worse than one that does not run.
check("a missing video is a reason, not a crash",
      admin._ascent_ai_launch(_Db(_Ev(4.88)), _Row(), Path("/nope.mp4"),
                              60.0, {}, {"roi": {"x": 0.1, "y": 0.1,
                                                 "w": 0.2, "h": 0.2}}
                              )["ran"], False)

# ── 4. wiring ────────────────────────────────────────────────────────
_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")
_run = _src[_src.index("def _ascent_run(row, src_path, db,"):]
_run = _run[:_run.index("\ndef ", 10)]
print()
print("it runs before the sweep, as stage 1:")
# THE NUMBERS AND THE ORDER, NOT THE TITLES. These pinned
# '_stage(1, "Swing check and AI launch plot"' and
# '_stage(2, "Balls leaving the tee"', and 57a92db renamed both when
# it folded the golfer, the ball and the impact frame into one stage.
# The titles are what an operator reads on a card; they will be
# rewritten again, and they are not what this section is about. What
# must hold is that the plot is stage 1 and the sweep is stage 2.
check("the plot's stage is numbered 1", "_stage(1, " in _run, True)
check("the sweep's stage is numbered 2", "_stage(2, " in _run, True)
check("...in that order", _run.index("_stage(1, ") < _run.index("_stage(2, "),
      True)
check("...and the plot is called before it",
      _run.index("_ascent_ai_launch(") < _run.index("_d3.sweep_ascents("),
      True)
check("the sweep's own sub-steps moved with it", '_sub("2b"' in _run, True)
check("the green half's moved too", '_sub("4c"' in _src, True)
_jsx = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text(encoding="utf-8")
check("the panel nests them under the new numbers",
      "2: rep?.scan_stages || []" in _jsx
      and "4: rep?.descent_stages || []" in _jsx, True)
check("the modal shows the plot", "rep?.ai_launch &&" in _jsx, True)
check("...the strip it drew", "rep.ai_launch.image_url" in _jsx, True)
check("...and what was refused", "ai_launch_merge.refused" in _jsx, True)

print()
print("five pictures, every third frame:")
_cfg = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
check("five of them", admin.settings.ascent_ai_launch_frames, 5)
check("every third frame", admin.settings.ascent_ai_launch_step, 3)
check("both are tunable without a deploy",
      "ascent_ai_launch_frames" in _cfg and "ascent_ai_launch_step" in _cfg,
      True)
_svc = (ROOT / "backend" / "app" / "services"
        / "ai_tracer.py").read_text(encoding="utf-8")
_fn = _svc[_svc.index("def plot_launch_frames_ai("):]
_fn = _fn[:_fn.index("\ndef ", 10)]
check("the plotter takes a step",
      bool(re.search(r"frames = list\(range\(f_first, f_first \+ _step \* "
                     r"int\(n_frames\), _step\)\)", _fn)), True)
# READ FORWARD, KEEP WHAT IS WANTED. The old loop read CONSECUTIVE
# frames into stepped keys, which would have labelled f+1 as f+3.
check("...and reads forward, keeping only the frames it wants",
      "if _f in _want:" in _fn, True)
check("the zoom square finds the ball at rest rather than gating it",
      "found (the ball at address)" in _fn, True)
check("...and what it finds becomes the anchor for the rest",
      "rx, ry = nx, ny" in _fn, True)
check("...and is not counted as a point of the flight",
      "if not (i == 0 and first_rect is not None):" in _fn, True)
check("a measured step is the velocity, undamped",
      "vel = (nx - rx, ny - ry)" in _fn, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — the launch is looked at before it is looked for")
