#!/usr/bin/env python3
"""The ball box goes at the GOLFER'S feet, not at the zone's.

THE CLIP: a player tees up BEHIND the back zone. The six tiles show
the whole swing — address, top, impact, follow-through — and the
measured ball comes back as a red ring out in open grass, up the
picture from where the man is standing. From it: last seen f1641,
first gone f1653, impact f1647, "measured from the ball". Every one
of those numbers is about something that was never his ball.

WHY. `ball_region` took the box's top and bottom from `zone_y`, which
the caller builds as the union of EVERY trigger zone's y lines. That
is where a golfer may stand, and this one was not standing there. The
box was built beside his feet horizontally and at the zones' depth
vertically.

AND NOTHING DOWNSTREAM CAUGHT IT. A detection inside the stance box
skips `_on_the_ground` by design — the box is meant to be the stricter
ground test — so once the box is at the wrong depth there is no second
opinion anywhere.

The feet are measured, on this clip and on every clip where the swing
judge found a body. They win.

    python3 tools/ball_band_feet_test.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ball_vanish import (  # noqa: E402
    BALL_BAND_ABOVE_FEET, BALL_BOX_BELOW_FEET, _on_the_ground, ball_region,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# The clip, in round numbers off the tiles: a 1920x1080 frame, the
# golfer 125px tall with his feet at y=300, and the zones drawn for
# players standing further down the tee — y 180..260, well above him.
FRAME = (1920, 1080)
CROP = (200, 100, 900, 600)
FEET_X = 430.0
ANCHOR = (300.0, 125.0)         # (feet_y, height)
ZONE_Y = (180.0, 260.0)         # the union of every zone's lines
REAL_BALL_Y = 290.0             # at his feet, a shade above the line
FALSE_BALL_Y = 240.0            # the speck up in the zone band

box, why = ball_region(feet_x=FEET_X, crop=CROP, zone_y=ZONE_Y,
                       anchor=ANCHOR, handed="right", frame=FRAME)

print("\n1. the band comes from the golfer who swung")
check("a box was built", box is not None, True)
_y0, _y1 = (box[1], box[3]) if box else (None, None)
# Rounded INWARDS: the box skips the band test, so it must not hold a
# row the band would refuse.
check("its top is the foot band's top",
      _y0, math.ceil(300.0 - BALL_BAND_ABOVE_FEET * 125.0))
# BELOW the feet the box keeps the reach the zone gave it -- this
# change moves the box, it does not resize it. See
# BALL_BOX_BELOW_FEET.
check("its bottom keeps the box's own reach below the feet",
      _y1, math.floor(300.0 + BALL_BOX_BELOW_FEET * 125.0))
check("the reason says so", "foot band of the golfer who swung" in why, True)
check("the zone is not what placed it", "down the trigger zone" in why, False)

print("\n2. which is the difference between the two balls")
check("the ball at his feet is inside the box",
      _y0 <= REAL_BALL_Y <= _y1, True)
check("the speck up in the zone band is NOT",
      _y0 <= FALSE_BALL_Y <= _y1, False)
# The old behaviour, stated so the regression is unmistakable.
check("...whereas the zone band would have admitted it",
      ZONE_Y[0] <= FALSE_BALL_Y <= ZONE_Y[1], True)
check("...and would have EXCLUDED the real one",
      ZONE_Y[0] <= REAL_BALL_Y <= ZONE_Y[1], False)

print("\n3. the top edge agrees with the band; below it is looser")
# A detection inside the box skips _on_the_ground. Above the feet the
# two use the same figure, so the bypass cannot admit anything the
# band would refuse up there -- which is the side the speck was on.
check("the box's top edge is on the ground by the band's own test",
      _on_the_ground(_y0, ANCHOR), True)
check("the real ball is too", _on_the_ground(REAL_BALL_Y, ANCHOR), True)
check("and the speck is not", _on_the_ground(FALSE_BALL_Y, ANCHOR), False)
check("below the feet the box deliberately reaches past the band",
      _y1 > 300.0 + 0.02 * 125.0, True)

print("\n4. no golfer, and the zone is still better than the whole crop")
box2, why2 = ball_region(feet_x=FEET_X, crop=CROP, zone_y=ZONE_Y,
                         anchor=None, handed="right", frame=FRAME)
check("a box is still built", box2 is not None, True)
check("from the zone this time", (box2[1], box2[3]), (180, 260))
check("saying why it fell back",
      "no golfer to measure from" in why2, True)

print("\n5. neither one, and it says what was missing")
box3, why3 = ball_region(feet_x=FEET_X, crop=CROP, zone_y=None,
                         anchor=None, handed="right", frame=FRAME)
check("no box", box3, None)
check("and a reason naming both", "no golfer" in why3 and "zone" in why3,
      True)

print("\n6. the horizontal rule is untouched")
check("a right-hander's box runs from the feet to the right edge",
      (box[0] > FEET_X, box[2]), (True, CROP[2]))
box4, _ = ball_region(feet_x=FEET_X, crop=CROP, zone_y=ZONE_Y,
                      anchor=ANCHOR, handed="left", frame=FRAME)
check("a left-hander's runs the other way",
      (box4[0], box4[2] < FEET_X), (CROP[0], True))

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
