#!/usr/bin/env python3
"""The descent is three measurements, and it stops where one is missing.

  1. a ball seen LEAVING the tee
  2. a ball found ON THE GREEN
  3. a chain of dots POINTING AT it

NO ASCENT, NO DESCENT. On #1916 step 2 reported 0 SEEN LEAVING in 4.4
seconds and step 3 then ran for 2m 54s -- 48% of a six minute produce
-- and matched a descent to a swing that had no ascent. Finding one
is worse than finding none: it is a landing attached to a shot nobody
can show leaving.

AND THE CHAIN REPLACES THE WALK AND THE CRAWL. The walk tracked
backwards from the ball at rest, frame by frame, mostly over a ball
lying still; the crawl then went back further with no motion model at
all, which is how it crossed the bounce -- and also how it ran up the
true fall and then turned off it onto the treeline. A line that is
right for two thirds of its length and invented for the rest is worse
than one that stops.

The question asked now is the one an operator asks of the edit
wizard's own scan picture: which line of dots is AIMED at the ball.
It cannot make that turn, because a chain that bends away from the
ball stops being aimed at it.

    python3 tools/descent_chain_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    _ascent_descents, _swing_saw_an_ascent,
)
from app.services.greencrawl import (  # noqa: E402
    POINT_AT_FRAC, POINT_AT_MIN_PX, _judge_points_at,
)

FAILED = []
SRC = inspect.getsource(_ascent_descents)


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def chain(pts):
    return {"points": [{"frame": f, "x": float(x), "y": float(y)}
                       for f, x, y in pts]}


def falling(x0, y0, x1, y1, n=6, f0=100):
    """A chain of n points from (x0,y0) to (x1,y1)."""
    return chain([(f0 + 2 * i,
                   x0 + (x1 - x0) * i / (n - 1.0),
                   y0 + (y1 - y0) * i / (n - 1.0)) for i in range(n)])


print(__doc__.strip().splitlines()[0])

print("\n1. no ball left the tee, so nothing came down")
_rep = {"swings": [{"idx": 0, "ascent": None}], "clips": [{"n": 1}]}
check("a swing with no chain saw no ascent",
      _swing_saw_an_ascent(_rep, 0, _rep["clips"][0]), False)
_rep2 = {"swings": [{"idx": 0, "ascent": {"first_frame": 2010}}],
         "clips": [{"n": 1}]}
check("one with a chain did", _swing_saw_an_ascent(_rep2, 0,
                                                   _rep2["clips"][0]))
# A REPORT BUILT BEFORE THE CHAIN WAS RECORDED ON THE SWING still has
# the chain's first frame on the clip row.
check("...and the clip row is the fallback",
      _swing_saw_an_ascent({"swings": []}, 0, {"seen_frame": 2010}))
check("the whole step returns before the green is even opened",
      "no ball was seen leaving the tee on any swing" in SRC)
check("...and says the green was not opened",
      "the green was not opened" in SRC)
check("the rest search is gated per swing too",
      "the green was not searched for one coming down" in SRC)
check("...and so is the chain search",
      SRC.count("_swing_saw_an_ascent(rep, i, c)") >= 2, True)

print("\n2. a chain aimed at the ball is taken")
BALL = (500, 900)
_r = _judge_points_at(falling(500, 300, 500, 600), BALL)
check("straight down onto it", _r["ok"])
check("...with the miss reported", _r["miss_px"], 0)
check("...and how far ahead the ball is", _r["ahead_px"], 300)
_r2 = _judge_points_at(falling(460, 300, 490, 600), BALL)
check("a line angled onto it is taken too", _r2["ok"])

print("\n3. and one aimed somewhere else is not")
_w = _judge_points_at(falling(200, 300, 200, 600), BALL)
check("300px wide of it is refused", _w["ok"], False)
check("...saying by how much and what was allowed",
      "300px wide" in _w["why"] and "allowed" in _w["why"], True)
# THE TOLERANCE IS ANGULAR. The same 40px miss is a different claim
# at 200px out and at 1000px out, so an absolute pixel bar would
# refuse real chains far away and accept nonsense close up.
_near = _judge_points_at(falling(500, 760, 460, 860), (500, 900))
_far = _judge_points_at(falling(500, 0, 460, 100), (500, 900))
check("the allowance grows with the distance",
      _far["allowed_px"] > _near["allowed_px"], True)
check("...at the stated fraction",
      _far["allowed_px"], int(round(POINT_AT_FRAC * _far["ahead_px"])))
check("...with a floor for chains that end close",
      _near["allowed_px"], int(round(POINT_AT_MIN_PX)))

print("\n4. the things that are not a descent at all")
_up = _judge_points_at(falling(500, 600, 500, 300), BALL)
check("a chain going UP is refused", _up["ok"], False)
check("...for not falling", "does not fall" in _up["why"], True)
_short = _judge_points_at(chain([(100, 500, 300), (102, 500, 420)]), BALL)
check("two points have no heading worth extending", _short["ok"], False)
# BEHIND IS NOT AHEAD. A chain falling away PAST the ball, extended
# backwards, also passes through it -- and that is another group's
# shot leaving the frame.
_past = _judge_points_at(falling(500, 1000, 500, 1300), BALL)
check("a chain already past the ball is refused", _past["ok"], False)
check("...saying the ball is behind it",
      "BEHIND" in _past["why"], True)
_miles = _judge_points_at(falling(500, -2000, 500, -1700), BALL)
check("a chain that would have to carry 2000px is refused",
      _miles["ok"], False)
check("...as an extrapolation, not a sighting",
      "not a sighting" in _miles["why"], True)

print("\n5. the line stops where the dots stop")
# The gap between the last dot and the ball is the bounce and the
# roll. Drawing through it would be a claim about footage nobody
# looked at -- which is exactly what the crawl did when it veered.
check("the landing is the chain's last sighting",
      'c["landing_frame"] = int(_flight["pitch_frame"])' in SRC)
check("...described as where the fall stops being watched",
      "roll from there to the ball is not drawn" in
      (ROOT / "backend/app/services/greencrawl.py").read_text())
check("the comet is the chain and nothing else",
      'c["green_track"] = [{"frame": int(q["frame"])' in SRC)
# COMMENTS STRIPPED. The note explaining WHY they are gone names
# them both, and a text search that cannot tell a call from a comment
# would read that note as the call still being there.
_code = "\n".join(ln.split("#", 1)[0] for ln in SRC.splitlines())
check("the walk is gone from the produce",
      "track_descent_back" in _code, False)
check("...and so is the crawl", "crawl_flight_back" in _code, False)
check("but both are named, so somebody can find them",
      "crawl_flight_back" in SRC and "greendescent" in SRC, True)
check("...and said to be reachable by hand",
      "green-crawl button" in SRC, True)

print("\n6. the window is the flight, not the seconds before rest")
# ANCHORING ON THE REST FRAME WOULD MISS THE FALL ENTIRELY. The ball
# pitches, bounces and rolls, and the rest frame is impact+8/14/20s --
# so "the four seconds before it is lying still" can be seconds after
# the flight ended.
check("the window runs from the impact on the green clock",
      "_base + FLIGHT_LO_SEC" in SRC and "_base + FLIGHT_HI_SEC" in SRC,
      True)
check("...and never past the frame the ball was measured at rest",
      "_fhi = min(_fhi, _rf)" in SRC)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
