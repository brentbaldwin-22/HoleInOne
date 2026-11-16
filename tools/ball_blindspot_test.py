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

print("\n-- every blob near the band, with what was decided about it")
# THE CASE THAT COST A WRONG DIAGNOSIS, reproduced. A decoy in the
# band scoring 26 against a bar of 30, and the real ball scoring 44 --
# well over the bar -- sitting a few pixels BELOW the band's bottom
# edge. The old report kept only the single best reject, so it said
# "the brightest reject where a teed ball sits was 26" and the ball,
# brighter than the bar and refused for its position, was invisible.
# I read that 26 as the ball two levels short. It was a different
# object entirely.
DECOY = (150, 200)          # in band
LOW_BALL = (300, 226)       # 8px under the band's bottom edge


def _turf():
    """Real turf colours: the decoy must land ON 26, not near it."""
    rng = np.random.default_rng(5)
    f = np.full((300, 420, 3), 70, np.uint8)
    f[:] = (55, 95, 60)
    f += rng.integers(0, 12, (300, 420, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(f, (180, 70), (210, 215), (60, 55, 50), -1)
    cv2.circle(f, DECOY, 3, (83, 123, 88), -1)
    cv2.circle(f, LOW_BALL, 3, (100, 140, 105), -1)
    return f


_tall = (215.0, 150.0)      # band 188..218
_shots = [_turf() for _ in range(6)]
r = ball_in_frames(_shots, offsets=OFFS, anchor=_tall,
                   people=[{"box": [180, 70, 30, 150]}])
_look = {(e["verdict"]) : e for e in (r.get("looked_at") or [])}
check("the decoy is reported as too faint",
      "too faint" in _look, True)
check("  with its score", _look.get("too faint", {}).get("score"), 26.0)
# THE ONE THE OLD REPORT COULD NOT SHOW.
check("the ball is reported too, as off the ground",
      "off the ground" in _look, True)
_b = _look.get("off the ground") or {}
check("  at the position it was drawn",
      abs(_b.get("x", 0) - LOW_BALL[0]) <= 3
      and abs(_b.get("y", 0) - LOW_BALL[1]) <= 3, True)
check("  and with a score WELL OVER the bar it never got asked about",
      (_b.get("score") or 0) > r["limits"]["min contrast"], True)
# WHICH IS THE WHOLE POINT: the old single-best field still names the
# decoy, so reading it alone would still mislead.
check("the single best reject is still the decoy, not the ball",
      (r["near"] or {}).get("best_in_band"), 26.0)

print("\n-- and the list is a neighbourhood, deduplicated")
check("a blob far from the band is left out",
      all(abs(e["y"] - 203) < 70 for e in r["looked_at"]), True)
# A BALL THAT DOES NOT MOVE IS FOUND SIX TIMES IN THE SAME PLACE, and
# six stacked markers say nothing the first did not.
check("one entry per thing, not one per frame",
      len(r["looked_at"]), 2)
check("  and it says how many frames held it",
      (_look.get("off the ground") or {}).get("seen"), 6)

print("\n-- the picture tells the two apart")
_img = _turf()
_drawn = draw_zones(_img, [(150, 190, 160, 40)], ball=r)
check("it draws", _drawn is not None, True)
_plain = draw_zones(_turf(), [(150, 190, 160, 40)], ball={"spot": None})
_diff = (_drawn != _plain).any(axis=2)
check("  something is marked at the ball",
      bool(_diff[LOW_BALL[1] - 8:LOW_BALL[1] + 8,
                 LOW_BALL[0] - 8:LOW_BALL[0] + 8].any()), True)
check("  and at the decoy",
      bool(_diff[DECOY[1] - 8:DECOY[1] + 8,
                 DECOY[0] - 8:DECOY[0] + 8].any()), True)
# DIFFERENT COLOURS, because the two verdicts need different fixes:
# one is a bar to lower, the other a band to widen.
_bpx = _drawn[LOW_BALL[1] - 8:LOW_BALL[1] + 8,
              LOW_BALL[0] - 8:LOW_BALL[0] + 8].reshape(-1, 3)
_dpx = _drawn[DECOY[1] - 8:DECOY[1] + 8,
              DECOY[0] - 8:DECOY[0] + 8].reshape(-1, 3)
check("  in different colours",
      bool({tuple(c) for c in _bpx} & {tuple(c) for c in _dpx}
           != {tuple(c) for c in _bpx}), True)
# THE LABEL MUST BE ASCII: cv2's Hershey fonts cannot draw a down
# arrow and render it as "???" beside the number, which reads as the
# number being unknown.
TRC = (ROOT / "backend" / "app" / "services"
       / "tracer.py").read_text(encoding="utf-8")
_blk = TRC[TRC.index("EVERYTHING NEAR THE BAND"):][:3000]
check("no non-ASCII reaches putText",
      "\\u2193" in _blk or "\u2193" in _blk, False)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
