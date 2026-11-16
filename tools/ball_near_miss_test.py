#!/usr/bin/env python3
""""102 too faint" does not say whether the bar is wrong.

A real report read:

  Looked at 133 bright spot(s) across the six: 7 too big, 102 too
  faint, 2 on the golfer, 22 off the ground. Bars: area <=36,
  contrast >=30

and the only question it raises -- is 30 too high for this light? --
is the one it cannot answer. 102 rejections read identically whether
the brightest of them scored 29 or scored 5, and those want opposite
responses: a bar set a hair too high on a bright day, or a clip with
no ball visible in it at all.

Exactly the hole the audio gate had before `below_gate_peaks`, which
is why that one now says "a gate under x5.8 would have caught the
loudest".

    python3 tools/ball_near_miss_test.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ball_vanish import ball_in_frames  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-60s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def frames(speck=140, base=120, at=(150, 100), n=6):
    """Six frames of flat grass with one pale speck on the ground."""
    rng = np.random.default_rng(7)
    out = []
    for _ in range(n):
        f = np.full((200, 200, 3), base, np.uint8)
        f += rng.integers(0, 12, (200, 200, 3)).astype(np.uint8)
        if speck is not None:
            y, x = at
            f[y:y + 3, x:x + 3] = speck
        out.append(f)
    return out


print("\n-- a speck that nearly cleared the bar says how near")
out = ball_in_frames(frames(speck=140), anchor=(152.0, 110.0))
print("     %s" % out["why"])
check("it was rejected as faint", out["counts"]["too faint"] > 0, True)
check("  and nothing was kept", out["counts"]["kept"], 0)
_n = out.get("near") or {}
check("the best reject is reported", _n.get("best_contrast") is not None,
      True)
check("  with where it was", isinstance(_n.get("best_at"), list), True)
check("  and the sentence says a lower bar would keep it",
      "would have kept it" in out["why"], True)


print("\n-- a brighter speck scores higher, so the number means something")
_dim = (ball_in_frames(frames(speck=140), anchor=(152.0, 110.0))
        .get("near") or {}).get("best_contrast")
_bright = (ball_in_frames(frames(speck=145), anchor=(152.0, 110.0))
           .get("near") or {}).get("best_contrast")
print("     dimmer speck -> %s, brighter -> %s" % (_dim, _bright))
check("brighter reads higher", _bright > _dim, True)


print("\n-- there is a FLOOR below which nothing is even counted")
# `_detections` applies MIN_LOCAL_CONTRAST (18) before any of this, so
# "102 too faint" counts specks that cleared 18 and missed 30 -- not
# everything pale in the frame. Worth knowing before concluding from
# the number: a ball dimmer than 18 over its surroundings is invisible
# to this report entirely, and would read as "no bright spots" rather
# than as a near miss.
_under = ball_in_frames(frames(speck=130), anchor=(152.0, 110.0))
check("a speck under the detector's own floor is never seen",
      _under["counts"]["seen"], 0)
check("  so it is not reported as a near miss",
      (_under.get("near") or {}).get("best_contrast"), None)


print("\n-- and it names the floor, so the range is readable")
# 19 out of 30 reads as "close" only if you know the number could not
# have been lower than 18. A "nowhere near the bar" warning was
# written here and removed: with the floor at 18 and the bar at 30,
# nothing can score under half the bar, so it could never fire.
out = ball_in_frames(frames(speck=140), anchor=(152.0, 110.0))
check("the sentence says what is not counted at all",
      "nothing under 18 is counted at all" in out["why"], True)


print("\n-- nothing bright at all reports nothing, rather than a zero")
out = ball_in_frames(frames(speck=None), anchor=(152.0, 110.0))
_n = out.get("near") or {}
check("no rejects means no best reject", _n.get("best_contrast"), None)
check("  and the sentence does not offer a bar",
      "would have kept it" in (out["why"] or ""), False)


print("\n-- the in-band reject is preferred over the brightest anywhere")
# The brightest reject is often a highlight on a shoulder or a cart
# roof; the useful number is the brightest thing where a ball sits.
# BOTH MUST BE FAINT REJECTS. A bright thing off the ground passes
# the contrast test and is counted "off the ground", never reaching
# the faint tally -- the first version of this test used one and
# proved nothing.
f = frames(speck=140, at=(150, 100))
for img in f:
    img[20:24, 20:24] = 144        # faint, and well off the ground
out = ball_in_frames(f, anchor=(152.0, 110.0))
_n = out.get("near") or {}
check("the in-band best is recorded separately",
      _n.get("best_in_band") is not None, True)
check("  and is not the off-ground highlight",
      _n.get("best_in_band") < (_n.get("best_contrast") or 999), True)
check("  the sentence names which it used",
      "where a teed ball sits" in out["why"], True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
