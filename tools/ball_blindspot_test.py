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


# ── THE DRAWING IS DELIBERATELY GONE ────────────────────────────────
#
# These three sections used to assert that each measurement appeared
# ON the strip, and each addition was reasonable on its own. Together
# they put a band of circles, crosses and diamonds across the tee with
# the ball somewhere underneath, and the operator asked for it
# removed: a picture you cannot read is not a diagnostic.
#
# So the assertions follow the decision rather than going quiet. What
# is REPORTED is what matters and is checked above; what is DRAWN for
# a failed check is now nothing but the swing judge's own ball mark,
# which `ai_ball_xy_test` covers.
print("\n-- a found ball is still marked; a failed check is left clean")
ZONES = [(100, 140, 120, 40)]
base = frame(_sliver)
_found = draw_zones(base, ZONES, ball={"spot": [130, IN_BAND_Y]})
check("a found ball still draws its spot", _found is not None, True)

r = run(_sliver)
_zonly = draw_zones(base, ZONES, ball={"spot": None})
_fail = draw_zones(base, ZONES, ball=r)
check("a failed check adds nothing to the zones",
      int((_fail != _zonly).any(axis=2).sum()), 0)
check("  though it still reports what it refused",
      (r.get("shape_rejects") or {}).get("n", 0) > 0, True)

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
# ONE ENTRY PER THING, which is the invariant -- not a fixed total.
# The count is not fixed because the under-floor probe below adds
# whatever the turf's own texture throws up once nothing was kept.
_pts = [(e["x"], e["y"]) for e in r["looked_at"]]
check("one entry per thing, not one per frame",
      all(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 > 5.0
          for i, a in enumerate(_pts) for b in _pts[i + 1:]), True)
check("  and it says how many frames held it",
      (_look.get("off the ground") or {}).get("seen"), 6)

print("\n-- the REPORT still tells the two apart, which is the half "
      "that mattered")
# The decoy and the ball need opposite fixes -- one is a bar to lower,
# the other a band to widen -- and the two are still distinguished by
# `verdict`, which is where the operator reads them now.
check("the decoy and the ball carry different verdicts",
      _look.get("too faint", {}).get("verdict")
      != _look.get("off the ground", {}).get("verdict"), True)
check("  and the picture stays out of it",
      int((draw_zones(_turf(), [(150, 190, 160, 40)], ball=r)
           != draw_zones(_turf(), [(150, 190, 160, 40)],
                         ball={"spot": None})).any(axis=2).sum()), 0)

print("\n-- \"nothing at all\" is an answer the report could not explain")
# THE FLOOR IS A BINARISATION THRESHOLD, not a filter: below
# MIN_LOCAL_CONTRAST a ball never becomes a connected component, so it
# is not refused, it is ABSENT -- and absent looks exactly like an
# empty patch of turf. Those need opposite responses: one is a floor
# set above this hole's light, the other a ball this method cannot
# see. The operator reported "nothing at all" and no mark on the
# picture, which is precisely this gap.
from app.services.ball_vanish import (  # noqa: E402
    MIN_LOCAL_CONTRAST as _FLOOR, BALL_MIN_AREA as _MINA, _detections,
)

DIM = (300, 212)          # inside the band, and well under the floor


def _dim_turf(ball=True):
    rng = np.random.default_rng(5)
    f = np.full((300, 420, 3), 70, np.uint8)
    f[:] = (55, 95, 60)
    f += rng.integers(0, 10, (300, 420, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(f, (180, 70), (210, 215), (60, 55, 50), -1)
    if ball:
        cv2.circle(f, DIM, 2, (67, 107, 72), -1)
    return f


# First, the premise: this ball really is invisible to the normal pass.
_g = cv2.cvtColor(_dim_turf(), cv2.COLOR_BGR2GRAY)
check("the ball is under the floor for the normal pass",
      _detections(_g, [(0, 0, 420, 300)], rel=0.0) == [], True)
check("  and the floor can be overridden for a look",
      len(_detections(_g, [(0, 0, 420, 300)], rel=0.0, floor=7.0)) >= 0,
      True)

r = ball_in_frames([_dim_turf() for _ in range(6)], offsets=OFFS,
                   anchor=_tall, people=[{"box": [180, 70, 30, 150]}])
_uf = [e for e in (r.get("looked_at") or [])
       if e["verdict"] == "under the floor"]
check("the probe finds something under the floor", len(_uf) > 0, True)
_hit = [e for e in _uf
        if abs(e["x"] - DIM[0]) <= 4 and abs(e["y"] - DIM[1]) <= 4]
check("  including the ball itself", len(_hit) == 1, True)
# IT IS REPORTED, NEVER BELIEVED. Nothing the probe sees may become a
# ball: it lowers a threshold the detector deliberately sets.
check("  and it is still not a ball", r["state"], "NO_BALL_DETECTED")
check("  the sentence says where to look",
      "Under the detector's floor" in r["why"], True)

print("\n-- the probe does not drown it in the turf's own texture")
# Below the floor the grass breaks into single pixels by the hundred,
# every one refused for area. They are noise and they crowd out the
# one blob worth seeing -- 14px against their 1px.
check("nothing under the minimum area is kept",
      all((e.get("area") or 0) >= _MINA for e in _uf), True)
check("  and the list stays readable", len(r["looked_at"]) <= 14, True)
# RANKED BY PLACE, NOT BRIGHTNESS. Sorting by score would put every
# under-floor entry last by construction and the cap would cut them,
# which is the opposite of why the probe exists.
check("the under-floor entries survive the cap", len(_uf) > 0, True)

print("\n-- and only when the normal pass found nothing")
# A SECOND PASS ON EVERY CLIP WOULD BE WASTE: it is six more
# detections, and a run that found its ball has no question to answer.
def _bright(f):
    cv2.circle(f, (IN_BAND_Y and 160 or 160, IN_BAND_Y), 3,
               (245, 245, 245), -1)


_found = run(_bright)
check("a successful check keeps something", _found["counts"]["kept"] > 0,
      True)
check("  so it does not probe below the floor",
      any(e["verdict"] == "under the floor"
          for e in (_found.get("looked_at") or [])), False)

print("\n-- and it is reported, not drawn")
check("the under-floor finding is in the sentence",
      "Under the detector's floor" in r["why"], True)
check("  and nothing is drawn for it",
      int((draw_zones(_dim_turf(), [(150, 190, 160, 40)], ball=r)
           != draw_zones(_dim_turf(), [(150, 190, 160, 40)],
                         ball={"spot": None})).any(axis=2).sum()), 0)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
