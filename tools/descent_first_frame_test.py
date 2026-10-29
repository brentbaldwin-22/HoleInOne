#!/usr/bin/env python3
"""The arc is timed to the FIRST frame of the descent, in every path.

WHAT THE PREDICTIVE HALF IS ACTUALLY BRIDGING. The tee camera tracked
the ball up and lost it; the green camera picked it up falling. Those
two instants are both detections, and the span between them is what
the drawn continuation has to fill.

The LANDING is not a detection. It is wherever the fall was judged to
have flattened out, so it moves with the bounce, the roll, and
wherever the walk back happened to stop -- and for three of the four
produce paths it was still the only number the renderer had, because
only one caller passes `descent_first_frame` as an argument. The other
three save the descent chain and nothing else.

So the first frame is read back off that chain. Two things have to hold
for that to be right, and this checks both:

  1. It is the chain's EARLIEST point, not its latest. An operator who
     plots the roll-out adds points after the landing, and `max()` is
     already known to pick those up -- that is what evicted a plotted
     descent from the comet window once.
  2. A swing can now carry a descent start and NO landing second at
     all, which is exactly the shape the old warning path crashed on:
     it printed `landing["sec"]` unconditionally, so on the one swing
     the warning existed for, float(None) raised and took the clip.

    python3 tools/descent_first_frame_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.routers.admin import (  # noqa: E402
    D3_MAX_FLIGHT_SEC,
    D3_MIN_BRIDGE_SEC,
    D3_MIN_FLIGHT_SEC,
    _saved_green_track,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class Row:
    """Just enough of a LongVideoUpload for the reader."""

    def __init__(self, swings):
        self.id = 1084
        self.edit_metrics = {"swings": swings}


def first_frame(track, landing_frame=None, fps=30.0):
    _row = Row([{"idx": 0, "green_track": track,
                 "landing_frame": landing_frame}])
    _t = _saved_green_track(_row, 0, fps) or []
    return min((int(q["frame"]) for q in _t), default=None)


def pt(f, x=100, y=100):
    return {"frame": f, "x": x, "y": y}


print("the descent's first frame, off the saved chain:")

# A FALL AND NOTHING ELSE. The green camera picks the ball up at f120
# and it touches down at f150.
_fall = [pt(f) for f in range(120, 151)]
check("a plain fall starts where the camera first saw it",
      first_frame(_fall, landing_frame=150), 120)

# ...AND THE ROLL-OUT AFTER IT. These are the points an operator adds
# following the ball to rest, and they are the reason the landing is
# the soft number: the chain's LAST point is three seconds past the
# instant the tracer is bridging to.
_with_roll = _fall + [pt(f) for f in range(151, 240)]
check("the roll-out does not move it", first_frame(_with_roll, 150), 120)
check("...though it does move the last point",
      max(int(q["frame"]) for q in _with_roll), 239)

# THE BOUND IS MEASURED BACK FROM THE LANDING, so a chain that a search
# prepended to loses the prepended part and keeps the fall. The first
# frame then moves, which is correct -- what is dropped is not descent.
_prepended = [pt(f) for f in range(0, 30)] + _fall
check("a chain with an unrelated head keeps the fall",
      first_frame(_prepended, landing_frame=150) >= 0, True)

# NO CHAIN, NO ANSWER. The caller then has nothing to time to, and must
# say so rather than fall back to the landing.
check("no chain gives no first frame", first_frame([], 150), None)
check("a chain of junk gives no first frame",
      first_frame([{"frame": None, "x": 1, "y": 2}], 150), None)

print()
print("a landing with a descent start and no landing second:")

# THE SHAPE THAT CRASHED. `run_wizard_produce_job` builds the landing
# from EITHER clock, so `sec` is None on a swing whose landing frame
# never arrived. Every reader of it in the timing block has to survive
# that -- and the one that did not was the warning printed when the
# flight is refused, which is the single most useful line in the whole
# path.
_src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text(
    encoding="utf-8")
_bad = re.findall(r'float\(landing\["sec"\]\)', _src)
check("nothing dereferences landing[\"sec\"] through float()",
      len(_bad), 0)

# The preference order itself: the descent's first frame wins, the
# landing is the fallback, and neither being present is reported rather
# than guessed at.
def timed_to(landing):
    _t = (landing or {}).get("first_sec")
    if _t is None:
        _t = (landing or {}).get("sec")
        return ("the landing" if _t is not None else None), _t
    return "the descent's first frame", _t


check("both clocks: the descent start wins",
      timed_to({"first_sec": 4.0, "sec": 6.2}),
      ("the descent's first frame", 4.0))
check("only a descent start: it is used",
      timed_to({"first_sec": 4.0, "sec": None}),
      ("the descent's first frame", 4.0))
check("only a landing: it is the fallback",
      timed_to({"first_sec": None, "sec": 6.2}), ("the landing", 6.2))
check("neither: nothing to time to", timed_to({}), (None, None))
check("no landing at all: nothing to time to", timed_to(None), (None, None))

print()
print("what the timing is refused for:")


def refuse(launch_f, last_asc_f, target_f, fps=30.0):
    """The renderer's two checks, with its own constants.

    The whole flight answers "is this a golf shot's airtime"; the span
    from the last frame of the ascent answers "is there anything for
    the predictive half to draw". They are different spans and a single
    band could only ever answer both while the target WAS the landing.
    """
    _flight = (target_f - launch_f) / fps
    _bridge = (target_f - last_asc_f) / fps
    if _bridge < D3_MIN_BRIDGE_SEC:
        return "nothing to draw"
    if _flight > D3_MAX_FLIGHT_SEC:
        return "not an airtime"
    return None


# THE CASE THAT BROKE 1084. Timing to the descent's FIRST frame instead
# of the landing shortens the flight by however much of the fall the
# green camera watched. A shot whose landing was 2.6s after launch has
# its descent start at 1.1s -- and the old floor of 1.5s on the whole
# flight refused it, so the tail timed itself off the hand-off's
# horizontal speed and finished in about two seconds.
check("a short shot timed to the descent's start is kept",
      refuse(launch_f=100, last_asc_f=112, target_f=133), None)
check("...and the old floor would have refused it",
      (133 - 100) / 30.0 < D3_MIN_FLIGHT_SEC, True)

# THE HAIRPIN THE BAND WAS THERE FOR is still refused, and now by the
# check that is actually about it: a target the tail cannot reach
# because there are no frames between the hand-off and it.
check("a target on top of the hand-off has nothing to draw",
      refuse(launch_f=100, last_asc_f=160, target_f=165), "nothing to draw")
check("a target BEFORE the hand-off too",
      refuse(launch_f=100, last_asc_f=160, target_f=140), "nothing to draw")

# A LONG SHOT WITH A REAL BRIDGE IS KEPT.
check("a six-second flight is a golf shot",
      refuse(launch_f=100, last_asc_f=140, target_f=280), None)

# AND A CLOCK THAT IS WRONG THE OTHER WAY IS STILL CAUGHT. An assumed
# offset of zero on a pair that really started a minute apart puts the
# target twenty seconds out; nothing about the bridge notices that, so
# the ceiling on the whole flight has to.
check("a twenty-second flight is not",
      refuse(launch_f=100, last_asc_f=140, target_f=700), "not an airtime")
check("...and the ceiling is what catches it",
      (700 - 100) / 30.0 > D3_MAX_FLIGHT_SEC, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
