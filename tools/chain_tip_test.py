#!/usr/bin/env python3
"""A chain has to follow the bend it ends on, not the straight it began.

`_refine_fit` fits ONE quadratic over the whole chain. A tee shot's
chain is mostly the straight part low down, so the curve that has to
say where the ball goes NEXT is dominated by the twenty frames where
it was not yet going anywhere. Extrapolated, it carries straight on
while the ball is already bending away, and every detection on the
bend lands outside the 7px tolerance.

Seen on a sweep that kept f314-335 and left five detections at f337,
f339, f341, f344 and f347 sitting along the continuation. f347 was not
even reachable -- REFINE_REACH is 10 frames past the end, and the pass
that would have carried the chain there adds nothing and stops.

So the tip aims, one step at a time, refitting with each point taken.
Nothing is extrapolated more than a few frames past something
measured. This checks that it follows a bend, that it still refuses
what is not on the chain, and that it cannot run away.

    python3 tools/chain_tip_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.debug3 import (  # noqa: E402
    FIT_TOL_PX,
    TIP_MAX_EXTRA,
    _extend_tip,
    _refine_fit,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def chain(f0, f1, fn):
    """Detections on a curve, one per frame."""
    out = []
    for f in range(f0, f1 + 1):
        x, y = fn(f)
        out.append({"frame": f, "x": float(x), "y": float(y)})
    return out


def index(pts, extra=()):
    by = {}
    for p in list(pts) + list(extra):
        by.setdefault(p["frame"], []).append(p)
    return by


# A SLICE, AND DELIBERATELY NOT A PARABOLA.
#
# The first version of this fixture was quadratic in both axes, which
# a quadratic extrapolates perfectly -- so the global refit sailed to
# the end and the fixture proved nothing. If the truth were a
# parabola there would be no bug to fix.
#
# A sliced ball's sideways drift ACCELERATES: the sideways force grows
# as the ball turns into its own spin axis, and perspective stretches
# it further as it goes away. Cubic in x is the cheapest honest shape
# for that, and it is exactly what a degree-2 fit over the straight
# lower run cannot see coming.
def slicer(f):
    t = f - 314
    return (160.0 + 0.007 * t ** 3, 840.0 - 26.0 * t + 0.30 * t * t)


FULL = chain(314, 347, slicer)
STRAIGHT = [p for p in FULL if p["frame"] <= 335]
BY = index(FULL)

print("the straight part cannot aim the bend:")
# The control: the chain as the sweep had it, refit over all of itself.
_r = _refine_fit(BY, list(STRAIGHT), FIT_TOL_PX)
_got = max(p["frame"] for p in _r)
print("    a global refit reaches f%d of f347 — %d detection(s) left behind"
      % (_got, 347 - _got))
# The real sweep left FIVE on the table. The fixture is tuned to lose
# about as many, so a change that quietly narrows the gap still shows.
check("...and it leaves the bend behind", 347 - _got >= 5, True)

print()
print("the tip can:")
_e = _extend_tip(BY, list(STRAIGHT), FIT_TOL_PX)
_frames = sorted(p["frame"] for p in _e)
check("the chain now reaches the last detection", max(_frames), 347)
check("...and keeps everything it started with",
      all(p["frame"] in _frames for p in STRAIGHT), True)
check("...in frame order", _frames, sorted(set(_frames)))

print()
print("what it must NOT take:")
# A detection well off the continuation is not part of this flight.
_off = {"frame": 337, "x": 900.0, "y": 300.0}
_e = _extend_tip(index(STRAIGHT, [_off]), list(STRAIGHT), FIT_TOL_PX)
check("a detection 700px off the line is refused",
      max(p["frame"] for p in _e), 335)
# Nothing there at all: the chain ends where it ended.
_e = _extend_tip(index(STRAIGHT), list(STRAIGHT), FIT_TOL_PX)
check("no detections past the end changes nothing", len(_e), len(STRAIGHT))

print()
print("a gap it may cross, and one it may not:")
# TIP_STEP_FRAMES is 4, so a detector that missed two frames is still
# picked up -- that is the whole reason the step is not 1.
_gapped = [p for p in FULL if p["frame"] <= 335 or p["frame"] >= 338]
_e = _extend_tip(index(_gapped), list(STRAIGHT), FIT_TOL_PX)
check("a two-frame hole is stepped over", max(p["frame"] for p in _e), 347)
# A ten-frame hole is not a hole, it is the end of the chain.
_far = [p for p in FULL if p["frame"] <= 335 or p["frame"] >= 346]
_e = _extend_tip(index(_far), list(STRAIGHT), FIT_TOL_PX)
check("a ten-frame hole ends it", max(p["frame"] for p in _e), 335)

print()
print("it cannot walk off across the sky:")
# A long clean curve would otherwise be followed for ever.
LONG = chain(314, 500, slicer)
_e = _extend_tip(index(LONG), list(STRAIGHT), FIT_TOL_PX)
# AGAINST A LITERAL, not against TIP_MAX_EXTRA. Comparing to the
# constant made the assertion move with it: raising the cap raised the
# bound too, so removing the cap altogether passed. The number is the
# point here, so the number is written down.
check("the extension stops at 24 points", len(_e) - len(STRAIGHT), 24)
check("...well short of the 186 on offer", max(p["frame"] for p in _e), 359)
check("...and that is the cap", TIP_MAX_EXTRA, 24)

print()
print("a chain too short to aim with is left alone:")
_tiny = STRAIGHT[:4]
check("four points are not a tip", _extend_tip(BY, _tiny, FIT_TOL_PX), _tiny)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
