#!/usr/bin/env python3
"""The continuation carries the chain's bend, not just its heading.

"THE MAPPED TRACER CHAIN IS ALREADY STARTING TO SLICE, BUT THE TRACER
LOOKS STRAIGHTER UNTIL THE TURN. THE TRACER SHOULD SHOW AS IT IS ON
THE CHAIN AND KEEP SLICING TO ITS APEX."

A chain measures three things: where the ball is, where it is going,
and how it is turning. `_arc_tail` takes a linear regression of the
tail -- a straight tangent by construction -- so the drawn line leaves
straight and only turns at the end. `_ballistic_tail` fits a quadratic
but then solves for its own curvature from the landing and throws the
measured one away. Either way the slice stops at the hand-off and
starts again later, which is the kink.

    python3 tools/tracer_curvature_test.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.ai_tracer import (  # noqa: E402
    _arc_tail, _curved_tail,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


def chain(n=34, rise=393.0, tilt_deg=26.6, bend=0.0, vy_decay=0.0,
          x0=1010.0, y0=510.0, f_end=330):
    """A tracked ascent. `bend` curves it sideways as it climbs."""
    out = []
    for i in range(n):
        u = i / (n - 1.0)
        # Vertical slows as it climbs when vy_decay > 0.
        _u = u - vy_decay * u * u
        y = y0 - rise * _u
        x = x0 - rise * math.tan(math.radians(tilt_deg)) * _u + bend * u * u
        out.append((f_end - n + 1 + i, x, y))
    return out


def heading(a, b):
    return math.degrees(math.atan2(-(b[2] - a[2]), b[1] - a[1]))


TGT = (1000.0, 450.0)
S, FPS, W, H = 140, 30.0, 1280, 720
SLICE = chain(bend=24.0, vy_decay=0.25)


def run(fn, c=None):
    c = c or SLICE
    return fn(c, TGT, FPS, W, H, land_frame=c[-1][0] + S)


print("the chain is bending when the track ends:")
_h_early = heading(SLICE[-14], SLICE[-9])
_h_late = heading(SLICE[-5], SLICE[-1])
print("  heading over its last points: %.1f -> %.1f deg" % (_h_early, _h_late))
check("it really is turning", abs(_h_late - _h_early) > 1.0, True)
_turn_sign = 1.0 if _h_late > _h_early else -1.0

print()
print("THE JOIN CARRIES IT ON:")
_c = run(_curved_tail)
check("the curved model draws", _c is not None, True)
_a = run(_arc_tail)
check("the arc model draws too", _a is not None, True)


def join_turn(tail):
    """Heading change across the hand-off, chain's last leg to tail's
    first -- what the eye reads as a kink."""
    return heading((0,) + tuple(tail[0][1:])[0:2], (0,) + tuple(tail[4][1:])) \
        - heading(SLICE[-5], SLICE[-1])


def first_leg(tail):
    return heading((0, SLICE[-1][1], SLICE[-1][2]), (0, tail[3][1], tail[3][2]))


_chain_head = heading(SLICE[-5], SLICE[-1])
_cur_head = first_leg(_c)
_arc_head = first_leg(_a)
print("  chain's last heading %.1f | curved leaves %.1f | arc leaves %.1f"
      % (_chain_head, _cur_head, _arc_head))
# THE ARC LEAVES ON A STRAIGHT TANGENT, so it leaves along where the
# ball WAS going rather than where it is going NOW -- the chain has
# turned since, and the drawn line has to make that up later.
check("the curved model leaves closer to the chain's own heading",
      abs(_cur_head - _chain_head) <= abs(_arc_head - _chain_head), True)

print()
print("AND KEEPS TURNING THE SAME WAY the chain was:")
_e = heading(_c[0], _c[4])
_l = heading(_c[18], _c[22])
print("  curved heading over its first 22 frames: %.1f -> %.1f" % (_e, _l))
check("it carries on turning, not straightening",
      (_l - _e) * _turn_sign > 0, True)

print()
print("a chain with NO bend gets no invented one:")
_straight = chain(bend=0.0, vy_decay=0.25)
_cs = _curved_tail(_straight, TGT, FPS, W, H,
                   land_frame=_straight[-1][0] + S)
if _cs:
    _e2 = heading(_cs[0], _cs[4])
    _l2 = heading(_cs[18], _cs[22])
    check("it stays near straight early on", abs(_l2 - _e2) < 6.0, True)
else:
    check("...or declines, which is also honest", _cs, None)

print()
print("it declines when the landing disagrees with the measurement:")
# THE CLAIM OF THIS MODEL is that the early tail IS the measurement
# carried on. If the cubic correction already dominates a quarter of
# the way in, that claim is false and the arc should reconcile them.
# IT TAKES AN EXTREME TARGET, and that is the right sensitivity: the
# apex and loop guards downstream catch the merely implausible, and
# this one is only about whether the EARLY tail is still the
# measurement. A landing behind and above the ball is not.
check("a landing behind and above the ball",
      _curved_tail(SLICE, (-2000.0, -1500.0), FPS, W, H,
                   land_frame=SLICE[-1][0] + 60), None)
check("...and one far downrange in no time at all",
      _curved_tail(SLICE, (2600.0, 1700.0), FPS, W, H,
                   land_frame=SLICE[-1][0] + 25), None)
# A LANDING IT CAN REACH IS STILL DRAWN. The guard must not become a
# reason to refuse ordinary shots.
check("but an ordinary one is drawn",
      _curved_tail(SLICE, (1180.0, 600.0), FPS, W, H,
                   land_frame=SLICE[-1][0] + 90) is not None, True)

print()
print("and the ordinary refusals still refuse:")
check("no target", _curved_tail(SLICE, None, FPS, W, H), None)
check("too few points", _curved_tail(SLICE[:4], TGT, FPS, W, H), None)
# A SECOND DERIVATIVE NEEDS A LEVER ARM. Over a handful of frames the
# curvature is jitter, and extrapolating jitter is how a tracer ends
# up pointing at the sky.
check("too short a lever arm",
      _curved_tail(chain(n=8, rise=40.0), TGT, FPS, W, H,
                   land_frame=330 + S), None)

print()
print("it lands where it was aimed:")
check("the last point is the target",
      (_c[-1][1], _c[-1][2]), (int(TGT[0]), int(TGT[1])))
check("one point per frame of the flight", len(_c), S)

print()
print("and it is tried BEFORE the arc and AFTER the ballistic model:")
import inspect  # noqa: E402

from backend.app.services import ai_tracer as _T  # noqa: E402

_src = inspect.getsource(_T._extend_to_target)
check("ballistic first", _src.index("_ballistic_tail(")
      < _src.index("_curved_tail("), True)
check("...then curved, then the arc",
      _src.index("_curved_tail(") < _src.index("_arc_tail("), True)
# WHICH MODEL DREW IT. Three can, they disagree about the shape, and
# the question is unanswerable without the name.
check("and the model that won is logged",
      "tracer tail: %s model drew" in inspect.getsource(
          _T.render_tracer_video), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
