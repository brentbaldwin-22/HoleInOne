#!/usr/bin/env python3
"""Search the grass beside the golfer, at a bar that grass allows.

THE MEASUREMENT THIS IS BUILT ON. On a real clip the ball's top-hat
response was about 11 grey levels. MIN_LOCAL_CONTRAST is 18 and it is
a BINARISATION THRESHOLD, so the ball never became a blob at all --
and the floor is 18 because the search covers the whole crop, where
below it the turf breaks into hundreds of single pixels.

Narrow the search and that reasoning stops applying. In a patch of
grass beside the stance there is almost nothing to mistake for a
ball, so the bar can come down to where the ball actually lives.

A UNION, NOT A REPLACEMENT: everything the wide pass found is still
found. The box only ADDS candidates the wide pass would have refused,
so a box in the wrong place costs nothing but the pass.

    python3 tools/ball_stance_box_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


import numpy as np  # noqa: E402
import cv2  # noqa: E402

from app.services.ball_vanish import (  # noqa: E402
    ball_in_frames, ball_region, MIN_LOCAL_CONTRAST, REGION_MIN_FILL,
    MIN_FILL, _detections,
)

OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)
FEET_X, FEET_Y, GH = 300, 215.0, 150.0
ANCHOR = (FEET_Y, GH)
BALL = (258, 220)          # LEFT of the stance (a righty), BELOW the feet
PEOPLE = [{"box": [285, 65, 30, 150]}]


def shot(ball=True):
    rng = np.random.default_rng(5)
    f = np.full((300, 420, 3), 70, np.uint8)
    f[:] = (55, 95, 60)
    f += rng.integers(0, 10, (300, 420, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(f, (285, 65), (315, 215), (60, 55, 50), -1)
    if ball:
        cv2.circle(f, BALL, 2, (67, 107, 72), -1)   # ~11 over the turf
    return f


HIT = [shot(p) for p in (1, 1, 1, 0, 0, 0)]


def run(**kw):
    return ball_in_frames(HIT, offsets=OFFS, anchor=ANCHOR,
                          people=PEOPLE, **kw)


print("\n-- the premise: this ball is invisible to the wide search")
_g = cv2.cvtColor(shot(), cv2.COLOR_BGR2GRAY)
_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
_hat = cv2.morphologyEx(_g, cv2.MORPH_TOPHAT, _k)
check("the ball is under the detector's floor",
      int(_hat[BALL[1], BALL[0]]) < MIN_LOCAL_CONTRAST, True)
check("  so the wide pass finds nothing",
      run()["state"], "NO_BALL_DETECTED")


print("\n-- the box finds it, and gets the verdict right both ways")
check("a struck ball reads as struck",
      run(feet_x=FEET_X)["state"], "BALL_PRESENT_DISAPPEARED")
check("  at the spot it was drawn",
      run(feet_x=FEET_X)["spot"], list(BALL))
check("  and the report says which search answered",
      run(feet_x=FEET_X)["found_by"], "the stance box")
_stay = ball_in_frames([shot() for _ in range(6)], offsets=OFFS,
                       anchor=ANCHOR, people=PEOPLE, feet_x=FEET_X)
check("a ball nobody touched reads as untouched",
      _stay["state"], "BALL_PRESENT_REMAINED")


print("\n-- it is a UNION: a box in the wrong place costs nothing")
# The side is a real lever and a WRONG side is worse than none, which
# is why no caller passes one today. Asserted so that a future caller
# wiring handedness in has to come past this line.
_wrong = run(feet_x=FEET_X, handed="left")
check("a box on the wrong side finds nothing extra",
      _wrong["state"], "NO_BALL_DETECTED")
check("  but it is no worse than the wide search alone",
      _wrong["state"], run()["state"])
check("both sides is the default", ball_region(ANCHOR, None, feet_x=FEET_X)
      [0] < FEET_X < ball_region(ANCHOR, None, feet_x=FEET_X)[2], True)
# RIGHT-HANDED MEANS THE BALL IS LEFT OF THE HANDS in image x -- the
# sign convention `_handedness_from_slopes` uses in tracer.py.
check("righty puts the box left of the stance",
      ball_region(ANCHOR, "right", feet_x=FEET_X)[2] <= FEET_X, True)
check("  and lefty to the right",
      ball_region(ANCHOR, "left", feet_x=FEET_X)[0] >= FEET_X, True)


print("\n-- the box is its own ground test, and reaches below the feet")
_rg = ball_region(ANCHOR, None, feet_x=FEET_X)
check("it extends below the foot line", _rg[3] > FEET_Y, True)
# THE BUG THIS REPLACES: the box searched ground the band then
# refused, so the narrowed search found nothing on its first outing.
check("  and a ball down there is kept, not called off-the-ground",
      run(feet_x=FEET_X)["spot"], list(BALL))
check("  which the band alone would have refused",
      BALL[1] > FEET_Y + 0.02 * GH, True)


print("\n-- and the shape bars inside it are looser, for a measured reason")
# A blob at the contrast the box exists to reach is RAGGED.
check("the box's fill bar is below the wide one",
      REGION_MIN_FILL < MIN_FILL, True)
_rj = []
_detections(_g, [_rg], rel=0.0, floor=8.0, rejects=_rj)
_hits = _detections(_g, [_rg], rel=0.0, floor=8.0,
                    min_fill=REGION_MIN_FILL, max_aspect=2.6)
check("  at the wide fill bar the ball is refused",
      any(abs(r["x"] - BALL[0]) < 5 and "not round" in r["why"]
          for r in _rj), True)
check("  at the box's it is kept",
      any(abs(d[0] - BALL[0]) < 5 for d in _hits), True)


print("\n-- the region is clamped to the frame, not trusted")
_huge = ball_in_frames(HIT, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                       region=(-500, -500, 99999, 99999))
_r = _huge.get("region")
check("a region off the edge is brought back in",
      _r[0] >= 0 and _r[1] >= 0 and _r[2] <= 420 and _r[3] <= 300, True)

print("\n-- AND IT MUST NOT INVENT ONE. A bar of 8 is near the noise")
# THE RISK THE WHOLE DESIGN TURNS ON. Dropping the bar this far only
# works because of what comes after it: ball-sized, inside a box the
# size of a stance, in the same few pixels in two of the first three
# frames, and gone from every frame after. Turf texture does none of
# those, and this asserts it rather than hoping.
_empty = [shot(False) for _ in range(6)]
_r = ball_in_frames(_empty, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                    feet_x=FEET_X)
check("bare turf is still no ball", _r["state"], "NO_BALL_DETECTED")
check("  and certainly not a struck one", _r["left"], None)

# TURF THAT CHANGES. Noise that moves between frames is the thing a
# low bar is most likely to turn into "a ball that left".
_shifting = []
for _i in range(6):
    _f = np.full((300, 420, 3), 70, np.uint8)
    _f[:] = (55, 95, 60)
    _rng = np.random.default_rng(100 + _i)
    _f += _rng.integers(0, 16, (300, 420, 3),
                        dtype=np.int16).astype(np.uint8)
    cv2.rectangle(_f, (285, 65), (315, 215), (60, 55, 50), -1)
    _shifting.append(_f)
_r2 = ball_in_frames(_shifting, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X)
check("moving noise is not a ball that left",
      _r2["left"] is True, False)
# AND IT SAYS WHY, because "the box found nothing" and "the box was
# too crowded to conclude anything" are different facts about the
# clip: the second is about the turf and the bar, not the ball.
check("  the box reports that it gave up",
      (_r2.get("region_busy") or 0) > 0, True)
check("  and says so in words",
      "stance box was abandoned" in _r2["why"], True)
check("  naming a count over the limit",
      _r2["region_busy"] > 6, True)
# A QUIET BOX MUST NOT GIVE UP, or the whole thing is off.
check("a box with one ball in it does not give up",
      (run(feet_x=FEET_X).get("region_busy") or 0), 0)

# A BRIGHT THING THAT STAYS PUT is not a struck ball either, whatever
# its brightness -- the shoe case, which this detector has been
# bitten by before.
_shoe = []
for _i in range(6):
    _f = shot(False)
    cv2.circle(_f, (262, 219), 3, (210, 215, 210), -1)
    _shoe.append(_f)
_r3 = ball_in_frames(_shoe, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X)
check("something bright that never moves did not get hit",
      _r3["left"] is True, False)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
