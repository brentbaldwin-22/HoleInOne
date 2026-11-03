#!/usr/bin/env python3
"""The click-to-plot scan has to keep the ball, not just the trees.

The operator can SEE the ball — a white smear against clear blue sky,
two frames of it, unmistakable — and the scan put dots on the
treeline, the houses, the bunker edge and the golfer, and none on the
ball.

WHY. The per-frame budget was spent at the two ends of the size range:
the largest few, on the reasoning that a ball beats foliage sparkle,
and the smallest few, added later because on a busy tee it does not. A
struck ball near the camera is a SMEAR of a few dozen pixels — bigger
than sparkle, far smaller than a golfer or a trolley. It sits in the
middle of the distribution, which is the one part nothing was keeping.

So the budget is spread across the PICTURE instead: the region is cut
into cells and the slots dealt round-robin, each cell offering its
largest unclaimed blob. A cell of trees gets one slot per round; a
ball alone in a cell of sky is that cell's largest and goes in round
one. No idea of how big a ball is required — which is the thing
nobody here has ever managed to calibrate.

    python3 tools/scan_budget_test.py
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    SCAN_GRID, _pick_scan_blobs)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


W, H = 1280, 720
CELL = max(8, int(min(W, H) / SCAN_GRID))
BALL = (46.0, 620.0, 120.0)          # a smeared ball, alone in the sky


def tee_frame(seed=4, n=300):
    """A tee frame like the one this came from: a saturated treeline
    and golfer across the bottom half, clear sky above, one ball."""
    rng = random.Random(seed)
    hits = [(float(rng.choice([3, 4, 5] * 6 + [40, 90, 200, 400, 700])),
             rng.uniform(0, W), rng.uniform(H * 0.45, H))
            for _ in range(n)]
    hits.append(BALL)
    return hits


print("the frame that started it:")
_hits = tee_frame()
_old = sorted(_hits, reverse=True)
_old_keep = _old[:7] + sorted((h for h in _old[7:] if h[0] >= 3),
                              key=lambda h: h[0])[:3]
print("      %d blobs; the old split kept %s"
      % (len(_hits), sorted(int(h[0]) for h in _old_keep)))
check("the old largest-and-smallest split missed the ball",
      BALL in _old_keep, False)

_new = _pick_scan_blobs(_hits, 10, CELL, small_slots=3)
print("      the grid keeps %s" % sorted(int(h[0]) for h in _new))
check("the ball is kept now", BALL in _new, True)
check("...without spending more of the budget", len(_new) <= 10, True)
# THE TREES ARE STILL WORTH KEEPING. This is not "prefer small": a
# frame where the big movers are the story must still show them.
# BY AREA, not by identity -- the fixture has a dozen blobs of exactly
# 700px and which tuple sorts first is an accident of their
# coordinates, not something the picker owes anybody.
check("the biggest mover is still kept",
      max(h[0] for h in _new), max(h[0] for h in _hits))

print()
print("it is the ISOLATION that wins the slot, not the size:")
# Same ball, dropped into the middle of the treeline instead of the
# sky. The grid cannot help there -- that is what the small slots are
# still for -- and the test says so rather than pretending otherwise.
_crowded = tee_frame()
_crowded.remove(BALL)
_crowded.append((46.0, 300.0, 600.0))
_pick = _pick_scan_blobs(_crowded, 10, CELL, small_slots=3)
print("      a ball over the trees: kept=%s"
      % ((46.0, 300.0, 600.0) in _pick))

print()
print("the shape of the budget:")
check("an empty frame gives nothing", _pick_scan_blobs([], 10, CELL), [])
check("a budget of nothing keeps nothing",
      _pick_scan_blobs(_hits, 0, CELL), [])
check("fewer blobs than slots keeps them all",
      len(_pick_scan_blobs(_hits[:4], 10, CELL)), 4)
check("no blob is handed back twice",
      len(_new), len({id(h) for h in _new}))
# A SINGLE CELL MUST NOT STARVE THE REST. With every blob in one
# corner the round-robin degenerates to "largest first", which is the
# old behaviour and the right one when there is nowhere else to look.
_one = [(float(a), 10.0, 10.0) for a in (5, 50, 500, 900, 20)]
check("all in one cell falls back to largest-first",
      [int(h[0]) for h in _pick_scan_blobs(_one, 3, CELL)],
      [900, 500, 50])

print()
print("zooming in helps, which is what the tooltip promises:")
# THE GRID IS CUT FROM THE REGION, so zooming does not shrink the
# cells against their contents -- it shrinks what is in view. A scan
# zoomed onto the flight sees a few branch tips instead of the whole
# treeline, and the ball's cell is still the quiet one.
# A window around the flight that still takes in the top of the
# treeline -- a zoom with no trees in it would prove nothing.
_zx0, _zy0, _zoom_w, _zoom_h = 380, 0, W // 2, int(H * 0.72)
_zoomed = [h for h in tee_frame()
           if _zx0 <= h[1] < _zx0 + _zoom_w
           and _zy0 <= h[2] < _zy0 + _zoom_h] + [BALL]
_zcell = max(8, int(min(_zoom_w, _zoom_h) / SCAN_GRID))
print("      zoomed in, %d blobs in view" % len(_zoomed))
check("a zoomed scan keeps it too",
      BALL in _pick_scan_blobs(_zoomed, 10, _zcell, small_slots=3), True)
# AND THE CELL SIZE IS NOT A MAGIC NUMBER. The same frame at half and
# double the grid still keeps it, or the fix is really "this one
# fixture happens to line up".
for _g in (2, 3, 6, 8):
    _c = max(8, int(min(W, H) / _g))
    check("...and at a %dx%d grid" % (_g, _g),
          BALL in _pick_scan_blobs(_hits, 10, _c, small_slots=3), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — the sky gets a slot of its own")
