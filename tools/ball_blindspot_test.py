#!/usr/bin/env python3
"""What the ball check looked at and refused, not just what it kept.

TWO HOLES THIS CLOSES, both found on one real clip where the operator
could see the ball in the frame and the check reported "no ball
identified. Looked at 133 bright spot(s)".

ONE: the counts only ever described blobs `_detections` HANDED BACK.
A blob it refused itself -- too small, not round, a sliver -- left no
trace at all, so a ball sitting down in grass that fails MIN_FILL
because blades cut into its outline is not "too faint" or "too big",
it is simply absent, and the sentence invites the reader to conclude
it lost on a number it was never measured against. `_detections`
already works out the reason for each one and takes a list to put
them in; nothing passed one.

TWO: the drawn-on strip circled the ball when it found one and drew
NOTHING when it did not -- blank in exactly the case it was wanted
for. It now marks the band it searched, the brightest thing in it
with the score it missed by, and any blob refused on shape.

And the size cap was measured against the mean height of everyone
detected while the band was measured against the one golfer the crop
was cut around: two rulers for one picture.

    python3 tools/ball_blindspot_test.py
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
    ball_in_frames, BALL_MAX_DIAM_OVER_GOLFER,
)
from app.services.tracer import draw_zones  # noqa: E402

W, H = 320, 240
FEET_Y, GOLFER_H = 170.0, 120.0
ANCHOR = (FEET_Y, GOLFER_H)
# In the band: FEET_Y - 0.18*H .. FEET_Y + 0.02*H  ->  148.4 .. 172.4
IN_BAND_Y = 160
OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)


def frame(draw=None):
    f = np.full((H, W, 3), 60, np.uint8)
    f[::7, ::7] = 72
    if draw is not None:
        draw(f)
    return f


def run(drawer, n=6, **kw):
    return ball_in_frames([frame(drawer) for _ in range(n)],
                          offsets=OFFS, anchor=ANCHOR, **kw)


print("\n-- the size cap is the CROPPED golfer's, not everyone's average")
# The anchor is 120 tall; the bystander is 60. The mean would halve
# the cap and call a correctly-sized ball too big.
_ppl = [{"box": [10, 110, 20, 60]}, {"box": [150, 50, 30, 120]}]
r = run(None, people=_ppl)
_want = int((BALL_MAX_DIAM_OVER_GOLFER * GOLFER_H) ** 2)
check("the cap comes from the anchor's height",
      r["limits"]["max area"], _want)
check("  which is not what the average would give",
      r["limits"]["max area"] == int(
          (BALL_MAX_DIAM_OVER_GOLFER * 90.0) ** 2), False)
# WITHOUT AN ANCHOR the old average is still better than nothing.
r2 = ball_in_frames([frame(None) for _ in range(6)], offsets=OFFS,
                    anchor=None, people=_ppl)
check("with no anchor it falls back to the average",
      r2["limits"]["max area"],
      int((BALL_MAX_DIAM_OVER_GOLFER * 90.0) ** 2))


print("\n-- a blob refused on SHAPE is reported, not silently dropped")
def _sliver(f):
    # Bright, well over any contrast bar, and far too long to be round.
    cv2.line(f, (120, IN_BAND_Y), (150, IN_BAND_Y), (250, 250, 250), 2)


r = run(_sliver)
check("the check still finds no ball", r["state"], "NO_BALL_DETECTED")
_sr = r.get("shape_rejects") or {}
check("  but it counts what never became a candidate", _sr.get("n", 0) > 0,
      True)
_band = _sr.get("in_band") or []
check("  and keeps the ones where a teed ball sits", len(_band) > 0, True)
if _band:
    check("    with the position", abs(_band[0]["y"] - IN_BAND_Y) <= 4, True)
    check("    and the reason, in words",
          bool(_band[0]["why"]), True)
    print("      -> %dpx at (%d, %d): %s"
          % (_band[0]["area"], _band[0]["x"], _band[0]["y"],
             _band[0]["why"]))
check("  the sentence says so too",
      "never became candidates" in r["why"], True)
# THE POINT OF THE SENTENCE: it must not read as if the ball was
# among the ones that lost on a number.
check("    naming the biggest one in the band",
      "where a teed ball sits was" in r["why"], True)


print("\n-- an empty frame reports no shape rejects, not a fake one")
r = run(None)
check("nothing refused on shape", (r.get("shape_rejects") or {}).get("n"), 0)
check("  and no sentence about it",
      "never became candidates" in r["why"], False)


print("\n-- the strip DRAWS what it looked at when it found nothing")
ZONES = [(100, 140, 120, 40)]
base = frame(_sliver)


def drawn(ball):
    out = draw_zones(base, ZONES, ball=ball)
    return None if out is None else out


_found = drawn({"spot": [130, IN_BAND_Y]})
check("a found ball still draws", _found is not None, True)

r = run(_sliver)
_fail = drawn(r)
check("a FAILED check draws too", _fail is not None, True)
# IT USED TO BE BLANK HERE. `spot` is None, so the old code drew the
# zones and nothing else -- the same picture whether the check had
# looked in the right place or not.
_zonly = draw_zones(base, ZONES, ball={"spot": None})
check("  and it is not the same picture as zones alone",
      _fail is not None and _zonly is not None
      and bool((_fail != _zonly).any()), True)
# THE BAND, as two lines across the picture.
_rows = sorted(set(int(y) for y in
                   np.where((_fail != _zonly).any(axis=2))[0]))
check("  it marks rows near the band edges",
      any(abs(y - 148) <= 3 for y in _rows)
      and any(abs(y - 172) <= 3 for y in _rows), True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
