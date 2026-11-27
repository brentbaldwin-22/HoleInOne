#!/usr/bin/env python3
"""How long the hand-drawn arc hangs in the air.

THE ARC IS PACED BY THE FRAMES IT COVERS, so its duration IS its
speed. The first version used four and a half seconds for every shot,
and a 145-yard shot -- whose carry time is five seconds -- was drawn
up and down inside one, because the span was then quietly clamped to
whatever window a previous produce had chosen. It looked like a ball
fired into the sky and yanked back down.

THE NUMBER ALREADY EXISTS AND IS NOT THE BROWSER'S TO INVENT.
`flight_seconds_for_yardage` is the operator's own carry-time table
and is what the renderer paces its own continuation with. A second
copy in the page is a copy that drifts, and the one that drifts is
the one the operator is looking at.

But `tracer-shape`, the endpoint that serves it, refused everything
-- flight time included -- the moment there were fewer than two
tracked points. That is exactly the swing the arc exists for: the one
with no track to extend. So the guard moved below the flight-time
block, and a trackless call now comes back with no tail, a plain
reason, and the number that never depended on a tail.

    python3 tools/arc_flight_time_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ai_tracer import flight_seconds_for_yardage as carry  # noqa: E402
import app.routers.admin as A  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print(__doc__.strip().splitlines()[0])

print("\n1. the table says what the operator's table says")
# 200yd 6.2-6.8  175yd 5.6-6.1  150yd 5.0-5.4  125yd 4.3-4.7
# 100yd 3.6-4.0   75yd 2.9-3.3
for yards, lo, hi in ((200, 6.2, 6.8), (175, 5.6, 6.1), (150, 5.0, 5.4),
                      (125, 4.3, 4.7), (100, 3.6, 4.0), (75, 2.9, 3.3)):
    got = carry(yards)
    check("%dyd lands in %s-%ss (%ss)" % (yards, lo, hi, got),
          lo <= got <= hi)
# THE HOLE IN THE SCREENSHOTS. "The ball should be in the air for 5-6
# seconds on average" -- and hole 8 plays 145.
check("hole 8 at 145yd is five seconds", carry(145), 5.0)
check("a hole with no yardage on record gets no number", carry(None), None)
check("...and neither does a nonsense one", carry(0), None)

print("\n2. a swing with no track still gets the number")
src = inspect.getsource(A.tracer_shape)
_guard = src.index("if len(pts) < 2:")
_flight = src.index("_flight, _flight_from = None, None")
# THE ORDER IS THE FIX. With the guard first, the one call that needs
# this -- the arc, which exists because there is no track -- was
# answered with a refusal and nothing else.
check("the flight time is worked out before the refusal",
      _flight < _guard)
check("...and the refusal carries it",
      '"flight_sec": _flight, "flight_from": _flight_from}'
      in src[_guard:_guard + 600])
check("...and still says there is no tail",
      "fewer than two points" in src[_guard:_guard + 600])
# AND IT IS STILL THE SAME SOURCE OF TRUTH, in the same order: the two
# cameras' clocks where both saw the shot, then the operator, then the
# table.
check("measured beats everything", "the two cameras' clocks" in src)
check("...the table is the fallback",
      "flight_seconds_for_yardage(_yards)" in src)

print("\n3. and the page asks for it instead of guessing")
page = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text()
check("the arc asks the server", "async function askFlightSec()" in page)
check("...with an empty track, which is the whole point",
      "track_frames: []," in page)
check("...and does not ask again once it has one",
      "if (arcSec != null) return arcSec;" in page)
# A HOLE WITH NO YARDAGE, OR A SERVER THAT CANNOT BE REACHED, STILL
# GETS AN ARC. Five seconds, and the panel says it was assumed.
check("a failure still draws an arc",
      "THE TABLE IS A COURTESY, NOT A GATE." in page)
check("...at a stated default", "(arcSec || 5.0) * arcFps" in page)

print("\n" + ("all ok" if not FAILED
               else "%d FAILED:\n  %s" % (len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
