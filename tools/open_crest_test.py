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


print("\n-- the thresholds are set from a REAL clip, not two extremes")
# The first real swing this met scored 0.64 on concentration against a
# threshold of 0.65 and was declined by a hundredth -- on a tail whose
# drawn ball reached 0.00 px/frame. Field cases live between the
# synthetic extremes (0.33 smooth, 1.09 spike), not at them.
check("a real spike's concentration clears the bar",
      0.64 >= T._CREST_CONCENTRATION, True)
check("  and a smooth arc still does not",
      _s["concentration"] < T._CREST_CONCENTRATION, True)
# AND A SECOND WAY IN, because a ratio is a knife-edge: a drawn ball
# that stops has a cusp whatever the ratio says.
check("a dead-stop crest is opened on its own",
      T._CREST_DEAD_PX > 0.0, True)


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
# CONTROL: it cannot run away. The cap is a share of the SHORTER leg,
# so a huge request lands on the cap and two huge requests agree --
# asserting it equals some particular bow's result just re-encodes the
# cap's value and breaks whenever that is tuned.
_huge = T.apex_sharpness(T.open_crest(SPIKE, bow_px=4000.0))["deg"]
check("control, a huge bow is capped",
      _huge, T.apex_sharpness(T.open_crest(SPIKE, bow_px=9000.0))["deg"])
check("  and the cap still leaves the ends alone",
      T.open_crest(SPIKE, bow_px=4000.0)[0][1:], SPIKE[0][1:])


print("\n-- it never raises on what a renderer hands it")
for name, bad in (("None", None), ("empty", []),
                  ("two points", [(1, 2.0, 3.0), (2, 4.0, 5.0)]),
                  ("junk", [("a", "b", "c")] * 20)):
    T.open_crest(bad)
    print("  %-58s ok" % ("%s is returned untouched, no crash" % name))


print("\n-- it says what it did, since nothing downstream can")
# Everything after this measures the FINISHED tail, so the produce
# report and its log line can only say how the crest came out, never
# how bad it was before or whether anything was done. This is the only
# place both numbers exist.
import logging  # noqa: E402


class _Catch(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, rec):
        try:
            self.lines.append(rec.getMessage())
        except Exception as exc:  # noqa: BLE001
            self.lines.append("<handler broke: %s>" % exc)


_h = _Catch()
# THE MODULE'S OWN LOGGER BY NAME -- `T.__name__` is
# "app.services.ai_tracer" while it logs to "golfreelz.ai_tracer", and
# a handler on the wrong one collects nothing, which reads exactly like
# "the code logged nothing".
# AND THE LEVEL, not just the handler. `golfreelz.ai_tracer` sets no
# level of its own, so it inherits root's WARNING and an INFO record is
# dropped BEFORE any handler sees it -- which reads exactly like "the
# code logged nothing". The first run of this check failed that way.
_lvl = T.log.level
T.log.setLevel(logging.INFO)
T.log.addHandler(_h)
try:
    T.open_crest(SPIKE)
    _n_spike = len(_h.lines)
    T.open_crest(SMOOTH)
    _n_after = len(_h.lines)
finally:
    T.log.removeHandler(_h)
    T.log.setLevel(_lvl)
check("opening a crest is logged", _n_spike, 1)
# The BEFORE number, which is the one nothing downstream can report.
# Not pinned to a particular after-value: that moves whenever the bow
# or the cap is tuned, and the point of the line is that both numbers
# are present.
check("  it carries the before-number",
      "%.0f" % _k["deg"] in _h.lines[0], True)
check("  and an after-number lower than it",
      T.apex_sharpness(T.open_crest(SPIKE))["deg"] < _k["deg"], True)
check("  and it says the ends did not move",
      "did not move" in _h.lines[0], True)
check("leaving a good arc alone says nothing", _n_after, _n_spike)


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
