#!/usr/bin/env python3
"""How fast the drawn tail moves, frame by frame.

"THE SPEED AND DIRECTION OF THE TRACER NEED TO BE CONSISTENT. SLOW TO
APEX, GRADUAL/SMOOTH TURN TOWARD THE DESCENT PATH."

Measured off d3prod-1082 by tracking the drawn head: the tracked
ascent handed off at about 15.5px a frame, the tail left at 8.9,
collapsed to 1.1 by its third eighth, and arrived at 1.0. It never
picked up again, because the curve parameter advanced by
1-(1-u)^1.35 -- monotonically decelerating, with zero rate at the
landing by construction.

A ball flying away from the camera does slow. It slows INTO the apex
and picks up on the way down, and an ease that only ever slows cannot
do the second half.

    python3 tools/tracer_pacing_test.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.ai_tracer import _arc_tail  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


# The hand-off measured off upload 1082.
A = (931.0, 358.0)
V = (-6.4, -14.1)
TARGET = (1000.0, 450.0)
S, FPS, W, H = 140, 30.0, 1280, 720
PTS = [(i, A[0] + V[0] * (i - 11), A[1] + V[1] * (i - 11)) for i in range(12)]


def tail():
    return _arc_tail(PTS, TARGET, FPS, W, H, land_frame=PTS[-1][0] + S)


def steps(t):
    return [math.hypot(t[i + 1][1] - t[i][1], t[i + 1][2] - t[i][2])
            for i in range(len(t) - 1)]


T = tail()
check("the tail is drawn at all", T is not None, True)
check("one point per frame of the flight", len(T), S)
ST = steps(T)
n = len(ST)
first, mid, last = ST[:n // 6], ST[2 * n // 5:3 * n // 5], ST[-n // 6:]
mf, mm, ml = sum(first) / len(first), sum(mid) / len(mid), sum(last) / len(last)
print()
print("speed by sixth: %s" % " ".join(
    "%.1f" % (sum(ST[j:j + n // 6]) / (n // 6))
    for j in range(0, n - n // 6 + 1, n // 6)))

print()
print("SLOW TO APEX:")
check("it slows from the hand-off into the crest", mm < mf * 0.8, True)

print()
print("AND PICKS UP AGAIN, which the old ease could not:")
# THIS IS THE WHOLE BUG. 1-(1-u)^1.35 has rate 1.35/n at u=0 and
# exactly 0 at u=1, so the head could only ever decelerate.
check("the last sixth is faster than the crest", ml > mm * 1.3, True)
check("...and the final step is not a standstill", ST[-1] > mm, True)
# The old curve arrived at 1.0px a frame off a 15.5px hand-off.
check("the arrival is a real speed, not a crawl",
      ST[-1] > 0.25 * math.hypot(*V), True)

print()
print("and the profile is symmetric, not a one-way decay:")
check("the two ends are within a third of each other",
      abs(mf - ml) < 0.33 * max(mf, ml), True)

print()
print("NOTHING JUMPS:")
# MEASURED ON THE TREND, NOT ON ADJACENT STEPS. The tail is drawn on
# a pixel grid, so near the crest a true step of 2.6px lands as (1,2)
# and the next as (2,3) -- 2.24 then 3.61, a 61% "jump" that is
# entirely the rounding and invisible at 30fps. A rolling mean cancels
# it and still catches a real discontinuity in the pacing.
_k = 5
_roll = [sum(ST[i:i + _k]) / _k for i in range(len(ST) - _k + 1)]
_worst = max(_roll[i + 1] / _roll[i] if _roll[i] > 0.05 else 1.0
             for i in range(len(_roll) - 1))
check("the trend never jumps by a quarter", _worst < 1.25, True)
# AND THE QUANTISATION IS ONLY EVER SUB-PIXEL-ISH. A step of 6px
# rounding to 5 or 7 would be the pacing, not the grid. The default of
# 0 is not a dodge: no ragged step at all is the better outcome, and
# max() of an empty sequence raises -- which it did, on a pass.
_ragged = [ST[i] for i in range(len(ST) - 1)
           if ST[i] > 0.05 and ST[i + 1] / ST[i] > 1.5]
print("  (%d ragged step(s), largest %.1fpx)" % (
    len(_ragged), max(_ragged) if _ragged else 0.0))
check("the ragged steps are all small ones",
      max(_ragged, default=0.0) < 3.0, True)

print()
print("the arc itself is untouched -- it still goes over the top:")
# CLAMPING THE HORIZONTAL REVERSAL OUT WAS TRIED HERE AND IS WRONG. A
# ball struck nearly straight away from this camera climbs away and
# comes back beside its own line; forbidding that collapsed a 500px
# arc over the top into a 115px stub along the chord.
_len = sum(ST)
_chord = math.hypot(TARGET[0] - A[0], TARGET[1] - A[1])
check("the path is far longer than the chord", _len > 3.0 * _chord, True)
check("...and it reaches out past the launch point",
      min(p[1] for p in T) < A[0] - 40, True)

print()
print("THE REACH FACTOR SETS THE HAND-OFF, and is a named number:")
import backend.app.services.ai_tracer as _T  # noqa: E402
check("reach is named", hasattr(_T, "ARC_REACH_FRAC"), True)
check("...and the caps that bound it",
      (hasattr(_T, "ARC_SPAN_CAP_FRAC"), hasattr(_T, "ARC_HANDLE_CAP_FRAC")),
      (True, True))
# THE OLD CAPS SATURATED THE FACTOR AT ABOUT 0.53, so raising the
# reach past that did nothing at all -- which is the kind of knob that
# gets turned, observed to do nothing, and turned further.
_o = (_T.ARC_REACH_FRAC, _T.ARC_SPAN_CAP_FRAC, _T.ARC_HANDLE_CAP_FRAC)


def _arc_len(reach, spancap, hcap):
    _T.ARC_REACH_FRAC, _T.ARC_SPAN_CAP_FRAC, _T.ARC_HANDLE_CAP_FRAC = (
        reach, spancap, hcap)
    try:
        _t = tail()
        return sum(steps(_t))
    finally:
        (_T.ARC_REACH_FRAC, _T.ARC_SPAN_CAP_FRAC,
         _T.ARC_HANDLE_CAP_FRAC) = _o


check("the old caps saturated past ~0.53",
      round(_arc_len(0.7, 0.9, 0.5)) == round(_arc_len(1.2, 0.9, 0.5)), True)
check("...and the new ones do not",
      _arc_len(0.9, 1.4, 0.7) > _arc_len(0.6, 1.4, 0.7) + 20, True)
# A BIGGER ARC IS A FASTER HAND-OFF. That is what the factor buys,
# and the only thing it buys -- see the note on the constant.
check("a bigger reach leaves faster",
      _arc_len(0.6, 1.4, 0.7) > _arc_len(0.35, 0.9, 0.5) * 1.4, True)

print()
print("a shot with no clock still draws:")
check("no land_frame", _arc_tail(PTS, TARGET, FPS, W, H) is not None, True)
print("and the refusals still refuse:")
check("no target", _arc_tail(PTS, None, FPS, W, H), None)
check("too few points", _arc_tail(PTS[:1], TARGET, FPS, W, H), None)
# A LANDING ON TOP OF THE BALL is not an arc.
check("landing already at the ball",
      _arc_tail(PTS, (A[0] + 1, A[1] + 1), FPS, W, H), None)

print()
print("a straight-up shot, where there is no horizontal to pace by:")
_up = [(i, 640.0, 600.0 - 14.0 * (11 - i)) for i in range(12)]
_t = _arc_tail(_up, (643.0, 520.0), FPS, W, H, land_frame=11 + 90)
check("still draws", _t is not None, True)
if _t:
    _s = steps(_t)
    check("...and still moves on every frame", min(_s) > 0.0, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
