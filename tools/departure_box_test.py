#!/usr/bin/env python3
"""The departure watch searches the box, not the whole crop.

THE PICTURE THAT PROMPTED IT: six tiles, a green box round the golfer,
a yellow box on the turf in front of his feet — and the red ring, the
ball the run says it measured, sitting out in open grass well to the
side of both. "Nothing at all changed on the issue with the ball
search above the feet."

TWO SEARCHES ASK THE SAME QUESTION AND ONLY ONE HAD A BOX.
`ball_in_frames` has been given `feet_x` and `zone_y` for a while and
builds a box a few dozen pixels wide. `ball_departure`, when nobody
hands it a spot, goes and finds one — and it looked across the ENTIRE
verify crop with a single test on it, `_on_the_ground`, which bounds
HEIGHT and says nothing about x. So any still, ball-sized, bright
enough speck at roughly foot height, anywhere along a few hundred
pixels of turf, could be the most-seen blob and become "the ball".
Everything after it — last seen, first gone, the impact frame, where
the tracer starts — is then about that speck.

AND THE BOX WAS NOT A BOX. On the ball's side it ran to the edge of
the crop, so even `ball_in_frames` was searching a strip. Asked for
directly: "can we crop around the golfer and then draw a square in
front of the feet around where the ball should be and just search
there?"

    python3 tools/departure_box_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import ball_vanish as bv  # noqa: E402
from app.services.ball_vanish import (  # noqa: E402
    BALL_BOX_FAR_FEET, BALL_BOX_FOOT_GAP, ball_region,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# The clip, in round numbers off the tiles: a 125px golfer with his
# feet at (430, 300), the verify crop running from x=200 to x=900, and
# the speck the run measured sitting at x=760 — right height, hundreds
# of pixels away.
FRAME = (1920, 1080)
CROP = (200, 100, 900, 600)
FEET_X, FEET_Y, GH = 430.0, 300.0, 125.0
ANCHOR = (FEET_Y, GH)
REAL_BALL = (470.0, 290.0)
SPECK = (760.0, 298.0)

box, why = ball_region(feet_x=FEET_X, crop=CROP, zone_y=(180.0, 260.0),
                       anchor=ANCHOR, handed="right", frame=FRAME)

print("\n1. the box has a far edge, so it is a box")
check("it stops a golfer's height past the feet",
      box[2], int(FEET_X + BALL_BOX_FAR_FEET * GH))
check("...well short of the crop edge", box[2] < CROP[2], True)
check("it still starts clear of the shoes",
      box[0], int(FEET_X + BALL_BOX_FOOT_GAP * GH))
check("the reason calls it a box in front of the feet",
      "box in front of the feet" in why, True)

print("\n2. which is the difference between the ball and the speck")
check("the ball at his feet is inside it",
      box[0] <= REAL_BALL[0] <= box[2]
      and box[1] <= REAL_BALL[1] <= box[3], True)
check("the speck is at the right HEIGHT, which is why it won",
      bv._on_the_ground(SPECK[1], ANCHOR), True)
check("...but it is outside the box", box[0] <= SPECK[0] <= box[2], False)
check("...where the whole crop would have held it",
      CROP[0] <= SPECK[0] <= CROP[2], True)

print("\n3. no golfer to measure from: no far edge is invented")
box2, why2 = ball_region(feet_x=FEET_X, crop=CROP, zone_y=(180.0, 260.0),
                         anchor=None, handed="right", frame=FRAME)
check("the box runs to the crop edge, as before", box2[2], CROP[2])
check("and says that is what it did",
      "no golfer to measure a far edge from" in why2, True)

print("\n4. the watch asks for that same box")
DEP = inspect.getsource(bv.ball_departure)
check("it takes the feet now", "feet_x=None" in DEP, True)
check("and builds the region from them when it has no spot",
      "if spot is None and feet_x is not None:" in DEP, True)
check("searching it instead of the crop", "boxes = [(max(x0" in DEP, True)
check("it reports the box it used", 'out["region"] = list(boxes[0])' in DEP,
      True)
check("and whose answer the spot is", 'out["spot_from"]' in DEP, True)
check("a handed-in spot is still not searched for twice",
      'if spot is None' in DEP, True)

print("\n5. the caller hands it over, and the panel says who answered")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
check("the feet reach the watch", "feet_x=_ffx1, zone_y=_zy1," in SRC, True)
check("the three possible sources are told apart",
      'out["measured_from"]' in SRC
      and "kept on the camera event from an earlier" in SRC, True)
check("and step 1 prints which one placed the ring",
      '"the ball\'s spot came from"' in SRC, True)
check("...and the box it searched",
      '"the box it searched"' in SRC, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
