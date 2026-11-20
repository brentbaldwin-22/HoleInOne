#!/usr/bin/env python3
"""Each swing's search must be anchored on ITS OWN golfer.

ASKED DIRECTLY, looking at step 2's pictures: "when we have multiple
swings in a clip, are we finding the ascent and descent one by one or
are we sweeping the entire clip?"

Mostly one by one — the chain selection, the green window, the match
and the produce all loop per swing — but two things were not:

  1. THE CONE'S APEX. `sweep_ascents` took a single `cone_origin`, and
     the caller handed it `rep["ai_launch"]`, which is set to
     `rep["swings"][0]` — the FIRST swing's ball, kept there for a
     panel that wants exactly one. On a clip with three golfers spread
     across the tee box, the below-skyline fallback was aimed at where
     the first player stood, for all three swings.

  2. THE PICTURES. The only ascent images on the page were the two
     whole-clip ones, while a per-chain picture had been drawn all
     along and never displayed. A per-swing pipeline whose only
     evidence is whole-clip evidence reads as a whole-clip sweep,
     which is exactly how it was read.

    python3 tools/per_swing_search_test.py
"""
import inspect
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SRC = (ROOT / "backend/app/routers/admin.py").read_text()
D3 = inspect.getsource(d3.sweep_ascents)

print("\n1. the cone takes one apex per swing")
check("a list of origins is read, not a single point",
      "_origins = list(dict.fromkeys(_origins))" in D3, True)
check("a detection counts if it is in ANY wedge",
      bool(re.search(r"for _ax, _ay in _origins:.*?break", D3, re.S)), True)
check("the rectangle spans every wedge, so the clip decodes once",
      "_ay_max = max(_y for _, _y in _origins)" in D3, True)
check("...and only once: one detect call per span",
      D3.count("_below += detect_movers_by_diff("), 1)
check("each wedge brings its own per-frame budget",
      "per_frame=_per_frame * len(_origins)" in D3, True)
check("the report names every apex, not a loop variable",
      '"origins": [list(_o) for _o in _origins]' in D3, True)

print("\n2. the caller hands over every swing's ball")
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("built from the swings, not from rep['ai_launch']",
      '_cone_origin_for(_sw0.get("ai_launch"))' in _run, True)
check("with swing 1's as the fallback when no swing has one",
      '_o0, _ = _cone_origin_for(rep.get("ai_launch"))' in _run, True)
check("and the apexes are reported",
      'rep["cone_origins"]' in _run, True)
# 2b's own accumulator was already per swing; this guards it.
check("2b's accumulator still runs on the swing's own ball",
      '_ascent_cone_accumulate(\n                row, src_path, sweep, '
      '_sw.get("ai_launch")' in _run, True)

print("\n3. a wedge each side of a tee keeps a ball the other would refuse")
# Two golfers 600px apart, a 30-degree cone each. A ball rising off the
# RIGHT golfer is 60+ degrees from the LEFT golfer's apex.
LEFT, RIGHT = (400, 900), (1000, 900)
BALL = (1040, 500)          # just right of, and above, the right golfer


def bearing(origin, pt):
    return math.degrees(math.atan2(pt[0] - origin[0], -(pt[1] - origin[1])))


_b_left = bearing(LEFT, BALL)
_b_right = bearing(RIGHT, BALL)
check("from the first golfer's apex it is far outside a ±30° cone",
      abs(_b_left) > 30, True)
check("from its own golfer's apex it is well inside",
      abs(_b_right) < 30, True)
check("so one apex for the clip loses it and one per swing keeps it",
      (abs(_b_left) > 30) and (abs(_b_right) < 30), True)

print("\n4. every swing's chain is drawn, and the clip-wide ones say so")
check("the chosen chain's picture is kept on the swing",
      '_sw["ascent_image_url"] = _best.get("image_url")' in _run, True)
check("...and shown in that swing's group on 2a",
      '"url": _s2["ascent_image_url"]' in _run, True)
check("the two whole-clip pictures are labelled ALL SWINGS",
      _run.count('"ALL SWINGS'), 2)
check("and 2c names which swing's chain it is showing",
      '_st.get("n") != "2c"' in _run
      and '"The best chain IN THE CLIP, not in a swing' in _run, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
