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

_o3 = admin._ascent_ai_launch(_Db(_Ev(4.88)), _Row(), Path("/nope.mp4"),
                              60.0, {}, {"roi": None})
check("no tee box, no zoom", _o3["ran"], False)
check("...and it says where to draw one",
      "cameras page" in _o3["reason"], True)
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
check("stage 1 is the launch plot", '_stage(1, "AI launch plot"' in _run,
      True)
check("the sweep is stage 2 now",
      '_stage(2, "Balls leaving the tee"' in _run, True)
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
