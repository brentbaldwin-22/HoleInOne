#!/usr/bin/env python3
"""The crest of a tracer is a top, not a vertex.

A tee camera looks down the hole, so the landing sits near the launch
ray, and `_ballistic_tail` -- whose across-axis starts at zero speed by
definition -- brings the ball back down almost its own line. The two
legs converge to a point. That is faithful to the model and reads as a
spike, and a shot tracer is a drawing of a flight: a drawing that reads
as wrong IS wrong.

FIVE SHAPES WERE TRIED BEFORE THE RIGHT ONE, all of them sideways
offsets -- weighted by sin^2, by a Gaussian, by a held tanh step, along
one normal and then along each point's own. Every one moved the legs
about (up to 42px) and left the vertex exactly as sharp, because
shifting two lines sideways does not round the place they meet. A
corner is rounded by a FILLET, stepped back by DISTANCE along each leg
(at the crest the ball crawls, so a span of N frames covers no ground).

TWO THINGS MUST BOTH HOLD before it touches anything, and the second is
the one that protects good work: the turn must be sharp AND
concentrated at one point. A steep but smooth parabola reads about 40
degrees at this sampling and must not be "repaired".

    python3 tools/open_crest_test.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import ai_tracer as T  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# A real flight: x linear, y quadratic. Steep, and perfectly smooth.
SMOOTH = [(i, 700 + 2.2 * i, 470 - 6.0 * i + 0.022 * i * i)
          for i in range(300)]
# A spike: up one ray, back down another.
SPIKE = ([(i, 700 + 1.9 * i, 470 - 2.4 * i) for i in range(150)]
         + [(150 + i, 985 + 2.1 * i, 110 + 2.3 * i) for i in range(150)])


def moved(a, b):
    return max(math.hypot(p[1] - q[1], p[2] - q[2]) for p, q in zip(a, b))


print("\n-- the measure tells a spike from a steep flight")
_s, _k = T.apex_sharpness(SMOOTH), T.apex_sharpness(SPIKE)
print("     smooth: %.1f deg, concentration %s"
      % (_s["deg"], _s["concentration"]))
print("     spike:  %.1f deg, concentration %s"
      % (_k["deg"], _k["concentration"]))
# ANGLE ALONE DOES NOT SEPARATE THEM -- that is the whole point of
# measuring concentration, and why the first trigger was wrong.
check("a smooth flight is still a big angle", _s["deg"] > 10.0, True)
check("  but its turn is SPREAD", _s["concentration"] < 0.5, True)
check("a spike turns harder", _k["deg"] > 60.0, True)
check("  and all in one place", _k["concentration"] > 0.9, True)


print("\n-- a good arc is left completely alone")
_r = T.open_crest(SMOOTH)
check("not one point moves", moved(SMOOTH, _r), 0.0)


print("\n-- a spike gets a top")
_r = T.open_crest(SPIKE)
_a = T.apex_sharpness(_r)
print("     %.1f deg -> %.1f deg, concentration %s -> %s, moved %.1fpx"
      % (_k["deg"], _a["deg"], _k["concentration"], _a["concentration"],
         moved(SPIKE, _r)))
check("the corner comes down by half", _a["deg"] < 0.6 * _k["deg"], True)
check("  and stops being concentrated", _a["concentration"] < 0.8, True)
check("  within the bound", moved(SPIKE, _r) <= 40.0, True)
# THE TWO MEASUREMENTS DO NOT MOVE. The hand-off is where the ball was
# last seen; the last point is where the other camera says it landed.
check("the hand-off is untouched", _r[0][1:], SPIKE[0][1:])
check("  and so is the landing",
      _r[-1][1:], (int(round(SPIKE[-1][1])), int(round(SPIKE[-1][2]))))
check("  and the frame stamps are unchanged",
      [p[0] for p in _r], [p[0] for p in SPIKE])


print("\n-- a bigger bow rounds more, up to the cap")
_small = T.apex_sharpness(T.open_crest(SPIKE, bow_px=40.0))["deg"]
_big = T.apex_sharpness(T.open_crest(SPIKE, bow_px=80.0))["deg"]
check("40px helps", _small < _k["deg"], True)
check("  80px helps more", _big < _small, True)
# CONTROL: it cannot run away. The cap is a share of the shorter leg,
# so asking for a huge bow on this arc changes nothing further.
check("control, a huge bow is capped",
      T.apex_sharpness(T.open_crest(SPIKE, bow_px=4000.0))["deg"], _big)


print("\n-- it never raises on what a renderer hands it")
for name, bad in (("None", None), ("empty", []),
                  ("two points", [(1, 2.0, 3.0), (2, 4.0, 5.0)]),
                  ("junk", [("a", "b", "c")] * 20)):
    T.open_crest(bad)
    print("  %-58s ok" % ("%s is returned untouched, no crash" % name))


print("\n-- the renderer actually applies it")
src = (ROOT / "backend" / "app" / "services"
       / "ai_tracer.py").read_text(encoding="utf-8")
check("it runs on the drawn tail",
      "open_crest(_shape_tail(tail, apex_lift, apex_at))" in src, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
