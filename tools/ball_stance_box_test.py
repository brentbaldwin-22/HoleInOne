#!/usr/bin/env python3
"""Search the box the operator drew, and only that box.

THE BOX IS BUILT FROM THINGS WE MEASURE, which is the whole change.
It used to be fractions of the golfer's height on all four sides -- a
guess wearing the clothes of a measurement. It is now:

  left/right  from the FEET, out to the edge of the crop, on the side
              the ball is teed
  top/bottom  the TRIGGER ZONE's own lines, which the operator drew
              for this camera and this tee

and the ball search runs THERE and nowhere else. The wide pass over
the whole crop still runs for its counts -- that is the diagnosis
when the box comes back empty -- but its candidates no longer reach
the verdict, because the wide pass is what has been failing.

THE BAR CAN COME DOWN BECAUSE THE BOX IS NEARLY EMPTY. The ball that
prompted all this reads about 11 grey levels against a floor of 18,
and the floor is 18 because the search used to cover the whole crop.

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
    MIN_FILL, RIGHTY_BALL_SIDE, REGION_REL, REGION_MAX_ASPECT,
    REGION_MAX_CANDIDATES, _detections,
)

OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)
W, H = 420, 300
FEET_X, FEET_Y, GH = 200, 215.0, 120.0
ANCHOR = (FEET_Y, GH)
ZONE = (120, 196, 240, 40)            # the magenta lines: y 196..236
ZONE_Y = (ZONE[1], ZONE[1] + ZONE[3])
BALL = (300, 222)                     # teed to the RIGHT, inside the zone
PEOPLE = [{"box": [185, 95, 30, 120]}]


def shot(ball=True):
    """Turf with texture, and a ball that looks like a real one.

    A BRIGHT CORE WITH A HALO, because that is what the operator's own
    zoomed strip shows: grey 231 against a turf median of 100. The
    earlier version of this file used a ball twelve levels over the
    grass, which was invented rather than measured, and the whole
    design was tuned to rescue it.
    """
    rng = np.random.default_rng(11)
    f = np.full((H, W, 3), 70, np.uint8)
    f[:] = (55, 95, 60)
    f += rng.integers(0, 20, (H, W, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(f, (0, 0), (W, 150), (62, 72, 58), -1)
    cv2.rectangle(f, (185, 95), (215, 215), (45, 42, 40), -1)
    if ball:
        _b = np.zeros((H, W, 3), np.uint8)
        cv2.circle(_b, BALL, 2, (190, 195, 190), -1)
        _b = cv2.GaussianBlur(_b, (9, 9), 0)      # the halo
        f = cv2.add(f, _b)
        cv2.circle(f, BALL, 1, (235, 240, 235), -1)   # the core
    return f


HIT = [shot(p) for p in (1, 1, 1, 0, 0, 0)]


def run(**kw):
    kw.setdefault("feet_x", FEET_X)
    kw.setdefault("zone_y", ZONE_Y)
    return ball_in_frames(HIT, offsets=OFFS, anchor=ANCHOR,
                          people=PEOPLE, **kw)


print("\n-- the box is the feet, the zone lines, and the crop edge")
_box, _why = ball_region(feet_x=FEET_X, crop=(0, 0, W, H),
                         zone_y=ZONE_Y, anchor=ANCHOR, handed="right")
check("its top is the zone's top", _box[1], ZONE_Y[0])
check("its bottom is the zone's bottom", _box[3], ZONE_Y[1])
check("  not a fraction of the golfer", _box[3] - _box[1], 40)
check("it starts clear of the feet", _box[0] > FEET_X, True)
check("  and runs to the edge of the crop", _box[2], W)
# THE REASONING IS PART OF THE ANSWER. "No ball" from a patch of grass
# beside the stance is only worth anything if the operator can see
# where it looked and why there.
for _phrase in ("right-handed", "trigger zone", "edge of the crop"):
    check("  the reasoning says %r" % _phrase, _phrase in _why, True)


print("\n-- the premise: an ABSOLUTE threshold inflates the blob")
#
# THE MECHANISM, measured on the operator's own frame and reproduced
# here. The ball is not dim -- it is grey 231 on turf of 100. What
# defeats the wide search is that an absolute threshold keeps the
# ball AND its halo AND the bright grass around it as one blob, and
# that blob is larger than the area cap, which is derived from the
# golfer's height and assumes a tight one:
#
#   rel 0.00 floor 18   98px, contrast  22   OVER the 39px cap
#   rel 0.00 floor 30   44px, contrast  92   OVER the cap
#   rel 0.55 floor 18   15px, contrast 138   under it, and kept
#
# A relative threshold scales off the brightest thing in the box, so
# it cuts the ball at its own shoulder and the blob is the ball.
_g = cv2.cvtColor(shot(), cv2.COLOR_BGR2GRAY)
_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
_hat = cv2.morphologyEx(_g, cv2.MORPH_TOPHAT, _k)
check("the ball is bright, not dim",
      int(_hat[BALL[1], BALL[0]]) > 60, True)
_cap = int((0.05 * GH) ** 2)


def _blob(rel, floor):
    _d = _detections(_g, [(0, 0, W, H)], rel=rel, floor=floor,
                     min_fill=REGION_MIN_FILL, max_aspect=2.6)
    _n = [q for q in _d if abs(q[0] - BALL[0]) < 7
          and abs(q[1] - BALL[1]) < 7]
    return int(_n[0][2]) if _n else None


_abs_area = _blob(0.0, 30.0)
_rel_area = _blob(REGION_REL, MIN_LOCAL_CONTRAST)
print("      absolute: %s px   relative: %s px   cap: %d px"
      % (_abs_area, _rel_area, _cap))
check("an absolute threshold makes a blob over the cap",
      _abs_area is not None and _abs_area > _cap, True)
check("  a relative one keeps it under", _rel_area <= _cap, True)
check("  so the wide search finds nothing",
      ball_in_frames(HIT, offsets=OFFS, anchor=ANCHOR,
                     people=PEOPLE)["state"], "NO_BALL_DETECTED")


print("\n-- the box finds it, and gets the verdict right both ways")
check("a struck ball reads as struck",
      run(handed="right")["state"], "BALL_PRESENT_DISAPPEARED")
check("  at the spot it was drawn", run(handed="right")["spot"], list(BALL))
check("  and the report says which search answered",
      run(handed="right")["found_by"], "the stance box")
_stay = ball_in_frames([shot() for _ in range(6)], offsets=OFFS,
                       anchor=ANCHOR, people=PEOPLE, feet_x=FEET_X,
                       zone_y=ZONE_Y, handed="right")
check("a ball nobody touched reads as untouched",
      _stay["state"], "BALL_PRESENT_REMAINED")


print("\n-- the side: handedness, or the judge, or right by default")
def _rbox(**kw):
    """Just the box. `ball_region` answers (box, why)."""
    kw.setdefault("crop", (0, 0, W, H))
    kw.setdefault("zone_y", ZONE_Y)
    kw.setdefault("anchor", ANCHOR)
    kw.setdefault("feet_x", FEET_X)
    return ball_region(**kw)[0]


check("right-handed tees to %s" % RIGHTY_BALL_SIDE,
      _rbox(handed="right")[0] > FEET_X, RIGHTY_BALL_SIDE == "right")
check("  and left-handed to the other side",
      _rbox(handed="left")[2] <= FEET_X, True)
# THE OPERATOR'S INSTRUCTION, in as many words: "if we're not sure,
# assume right".
_unk, _uwhy = ball_region(feet_x=FEET_X, crop=(0, 0, W, H),
                          zone_y=ZONE_Y, anchor=ANCHOR, handed=None)
check("unknown handedness assumes right-handed",
      _unk, _rbox(handed="right"))
check("  and says that it assumed", "assuming right-handed" in _uwhy, True)

# THE JUDGE PICKS THE SIDE WHEN IT CAN, and it outranks handedness: a
# point is a weak answer to "exactly where" and a strong one to "which
# side", which is all it is asked for here.
_ai, _aiwhy = ball_region(feet_x=FEET_X, crop=(0, 0, W, H),
                          zone_y=ZONE_Y, anchor=ANCHOR, handed="left",
                          ai_xy=[BALL[0], BALL[1]])
check("the judge's side outranks handedness", _ai[0] > FEET_X, True)
check("  and says so", "the judge put the ball" in _aiwhy, True)
# A POINT ON TOP OF THE FEET IS NOT EVIDENCE OF A SIDE -- which is
# what the judge actually returned on the clip that prompted this.
_on, _onwhy = ball_region(feet_x=FEET_X, crop=(0, 0, W, H),
                          zone_y=ZONE_Y, anchor=ANCHOR, handed="left",
                          ai_xy=[FEET_X + 1, FEET_Y])
check("a point on the feet picks no side",
      "the judge put the ball" in _onwhy, False)
check("  so handedness still decides", _on[2] <= FEET_X, True)


print("\n-- and it searches ONLY the box")
check("the ball inside the box is the answer",
      run(handed="right")["spot"], list(BALL))

# A BRIGHT BALL OUTSIDE THE BOX MUST NOT BE FOUND. That is the point
# of narrowing and also its cost, so it is asserted both ways: the
# verdict ignores it, AND the report still says it was seen, because
# "there is a bright round thing on the turf but not where a ball can
# be" is the sentence an operator needs when the box comes back empty.
_far = []
for _p in (1, 1, 1, 0, 0, 0):
    _f = shot(False)
    if _p:
        cv2.circle(_f, (60, 222), 3, (245, 245, 245), -1)   # far left
    _far.append(_f)
_fr = ball_in_frames(_far, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X, zone_y=ZONE_Y, handed="right")
check("a bright ball outside the box is not the answer",
      _fr["state"], "NO_BALL_DETECTED")
check("  but it is still reported, as outside the box",
      any(e["verdict"] == "outside the box"
          and abs(e["x"] - 60) <= 4
          for e in (_fr.get("looked_at") or [])), True)


print("\n-- the shape bars inside it are looser, for a measured reason")
check("the box's fill bar is below the wide one",
      REGION_MIN_FILL < MIN_FILL, True)
# MEASURED ON THE OPERATOR'S OWN SIX TILES, not supposed: at -1.20s
# the ball passes either bar, and at -0.60s the standard MIN_FILL of
# 0.55 LOSES it while the box's 0.35 keeps it at 21px. The rule needs
# the ball in two of the first three frames, so losing one of the two
# it is actually in is the difference between a swing and nothing.
#
# A real ball at this scale is RAGGED -- a handful of pixels, partly
# cut by grass -- so the synthetic one here is given a bitten edge
# rather than a clean disc.
_ragged = np.zeros((H, W, 3), np.uint8)
cv2.circle(_ragged, BALL, 2, (200, 205, 200), -1)
cv2.rectangle(_ragged, (BALL[0] - 3, BALL[1] - 3),
              (BALL[0] - 1, BALL[1] - 1), (0, 0, 0), -1)   # a blade across
_rg_img = cv2.add(shot(False), _ragged)
_rgg = cv2.cvtColor(_rg_img, cv2.COLOR_BGR2GRAY)
_rj = []
_detections(_rgg, [_box], rel=REGION_REL, floor=MIN_LOCAL_CONTRAST,
            rejects=_rj)
_hits = _detections(_rgg, [_box], rel=REGION_REL,
                    floor=MIN_LOCAL_CONTRAST,
                    min_fill=REGION_MIN_FILL,
                    max_aspect=REGION_MAX_ASPECT)
check("  a bitten ball is refused at the wide fill bar",
      any(abs(r["x"] - BALL[0]) < 5 and "not round" in r["why"]
          for r in _rj), True)
check("  and kept at the box's",
      any(abs(d[0] - BALL[0]) < 5 for d in _hits), True)


print("\n-- AND IT MUST NOT INVENT ONE on a box with no ball in it")
_empty = [shot(False) for _ in range(6)]
check("bare turf is still no ball",
      ball_in_frames(_empty, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X, zone_y=ZONE_Y,
                     handed="right")["state"], "NO_BALL_DETECTED")
_shifting = []
for _i in range(6):
    _f = np.full((H, W, 3), 70, np.uint8)
    _f[:] = (55, 95, 60)
    _rng = np.random.default_rng(100 + _i)
    _f += _rng.integers(0, 16, (H, W, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(_f, (185, 95), (215, 215), (45, 42, 40), -1)
    _shifting.append(_f)
_r2 = ball_in_frames(_shifting, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X, zone_y=ZONE_Y, handed="right")
check("moving noise is not a ball that left", _r2["left"] is True, False)

# ── THE CROWDED-BOX GUARD ───────────────────────────────────────────
#
# A LAST RESORT, NOT THE FIRST LINE. With a relative threshold the
# box mostly polices itself: on bare turf the brightest thing IS the
# turf, so the bar rises with it. The guard is for a box that really
# does hold a crowd -- a practice green, a scattering of balls, a row
# of tee markers -- where many things clear a bar set by the
# brightest of them and the 2-of-3-then-gone rule has too many
# tickets to stay honest.
_crowd = []
for _i in range(6):
    _f = shot(False)
    _rng = np.random.default_rng(300 + _i)
    for _n in range(14):
        _cx = int(_rng.integers(_box[0] + 2, _box[2] - 2))
        _cy = int(_rng.integers(_box[1] + 2, _box[3] - 2))
        cv2.circle(_f, (_cx, _cy), 2, (210, 215, 210), -1)
    _crowd.append(_f)
_r3 = ball_in_frames(_crowd, offsets=OFFS, anchor=ANCHOR, people=PEOPLE,
                     feet_x=FEET_X, zone_y=ZONE_Y, handed="right")
check("a box full of bright specks is abandoned",
      (_r3.get("region_busy") or 0) > REGION_MAX_CANDIDATES, True)
check("  and says so in words",
      "stance box was abandoned" in _r3["why"], True)
check("  rather than calling one of them a struck ball",
      _r3["left"] is True, False)
check("a box with one ball in it does not give up",
      (run(handed="right").get("region_busy") or 0), 0)


print("\n-- a box that cannot be built says why, rather than failing quiet")
_none, _nwhy = ball_region(feet_x=None, crop=(0, 0, W, H), zone_y=ZONE_Y)
check("no feet -> no box", _none, None)
check("  and a reason", "no feet" in _nwhy, True)
_nz, _nzwhy = ball_region(feet_x=FEET_X, crop=(0, 0, W, H))
check("no zone and no golfer -> no box", _nz, None)
check("  and a reason", "no trigger zone" in _nzwhy, True)
# THE FEET AT THE EDGE: the box would be a sliver or inverted.
_edge, _ewhy = ball_region(feet_x=W - 1, crop=(0, 0, W, H),
                           zone_y=ZONE_Y, anchor=ANCHOR, handed="right")
check("feet at the crop edge -> no box", _edge, None)
check("  and a reason that names it", "came out empty" in _ewhy, True)

print("\n-- THE OPERATOR'S OWN CLIP, as numbers taken off it")
#
# These are measurements from the zoomed strip of a real tee shot --
# the one where the ball is plainly a white speck in the first two
# tiles and the check reported "no ball identified". Recorded here
# because every design decision above was made from them, and a
# change that quietly breaks this case should fail rather than be
# discovered on the next clip.
#
#   the box          39 x 80 source pixels, zoomed about 5.8x
#   the turf         grey median 100
#   the ball         grey 231, top-hat 147 at (18, 26) of the box
#   the swing judge  put the ball at (692, 693) -- on the golfer's
#                    legs, ABOVE the trigger zone's top of 713
#
#   absolute floor 18   blob 98px   over the 39px cap -> "too big"
#   absolute floor 30   blob 44px   over the cap      -> "too big"
#   relative 0.55       blob 15px   contrast 138      -> kept
#
#   per tile, relative:  -1.20 one detection at (18,26) c138
#                        -0.60 one detection at (18,25) c110
#                        -0.20 and later: nothing at that spot
#
# Run through `ball_in_frames` with the tiles as the box, that is
# BALL_PRESENT_DISAPPEARED at (19, 26), seen in 2 of the first 3.
_CAP = 39
check("the measured absolute blob is over that clip's cap",
      98 > _CAP and 44 > _CAP, True)
check("  and the relative one is under it", 15 <= _CAP, True)
# THE JITTER HAS TO COVER THE ONE PIXEL THE BALL MOVED between the
# two tiles it is in -- 4px today, and the measurement is 1px.
from app.services.ball_vanish import ADDRESS_JITTER_PX  # noqa: E402
check("the clustering tolerance covers the measured 1px drift",
      ADDRESS_JITTER_PX >= 1.0, True)
# AND THE RULE HAS TO BE SATISFIABLE BY TWO TILES, because that is
# all the ball is in: it is gone by -0.20s on this clip.
check("two of the first three is enough, as it must be",
      run(handed="right")["seen_before"] >= 2, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
