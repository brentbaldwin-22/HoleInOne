#!/usr/bin/env python3
"""Where the green is searched for the ball coming down.

THE WINDOW IS PLACED FROM THE ASCENT, and which end of the ascent it
is placed from is the whole question. `impact_frame` is the chain run
BACKWARDS through a fitted deceleration to a tee line it never quite
reaches; the chain's LAST point is a detection. An extrapolation half
a second out drags the window half a second with it, and the window is
five seconds wide.

AND THE WIDTH IS THE RISK. The ascent ends roughly half a second after
impact, so five seconds from there closes around impact+5.5s. The one
flight on this course timed off wall clocks on both cameras took
6.37s. A window that tight would have ended before the ball landed --
which is why the lag is now measured on every match, and why the run
says so when a lag lands within half a second of the edge.

    python3 tools/descent_window_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.debug3 import resolve_gates  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %-16s %s" % (label, repr(got)[:16],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


# The placement, lifted exactly as _ascent_descents applies it.
def window(last_frame, fps, delta, win_sec):
    lo = float(last_frame) / fps - delta
    return (round(lo, 2), round(lo + win_sec, 2))


print("the window opens where the ball was last seen:")
# 60fps tee, ball last seen at f330 = 5.50s, green started 0.0s after.
check("same clock", window(330, 60.0, 0.0, 5.0), (5.5, 10.5))
# THE OFFSET IS THE WHOLE PLACEMENT. A green that started twelve
# seconds late puts the same instant twelve seconds earlier in its own
# file, and getting this backwards searches the wrong end of the clip.
check("green started 12s after the tee",
      window(330, 60.0, 12.0, 5.0), (-6.5, -1.5))
check("green started 12s BEFORE the tee",
      window(330, 60.0, -12.0, 5.0), (17.5, 22.5))

print()
print("the width is a gate, not a constant:")
check("wider", window(330, 60.0, 0.0, 8.0), (5.5, 13.5))
_g = resolve_gates()
check("anchored on the ascent's end by default",
      _g["descent_from_ascent_end"], 1)
check("five seconds by default", _g["descent_window_sec"], 5.0)
check("and it is tunable up", resolve_gates(
    {"descent_window_sec": 9.0})["descent_window_sec"], 9.0)
# A WIDTH OF ZERO IS A SEARCH OF NOTHING, which would read exactly
# like a green with no ball on it.
check("...but never to nothing", resolve_gates(
    {"descent_window_sec": 0.0})["descent_window_sec"] >= 1.0, True)

print()
print("THE RISK THIS CARRIES, stated as arithmetic:")
# Impact at 4.0s, the chain ends half a second later, the ball is in
# the air the 6.37s that was actually measured.
_impact, _chain_end, _flight = 4.0, 4.5, 6.37
_lo, _hi = window(_chain_end * 60, 60.0, 0.0, 5.0)
_lands = _impact + _flight
check("a 6.37s flight lands at", round(_lands, 2), 10.37)
check("...and a 5s window from the ascent end closes at", _hi, 9.5)
check("...so it would be MISSED", _lands > _hi, True)
check("a 7s window holds it", _lands <= window(
    _chain_end * 60, 60.0, 0.0, 7.0)[1], True)


# The warning, lifted as the run raises it.
def too_tight(lags, win_sec, from_end=True):
    return bool(from_end and lags and max(lags) > win_sec - 0.5)


print()
print("and the run says so before a landing is lost:")
check("a lag inside the window is quiet", too_tight([3.1, 3.4], 5.0), False)
check("one within half a second of the edge warns",
      too_tight([3.1, 4.7], 5.0), True)
check("one past it warns", too_tight([5.6], 5.0), True)
check("nothing measured, nothing claimed", too_tight([], 5.0), False)
# THE WARNING IS ABOUT THIS WINDOW. On the old placement the number
# means something else and the threshold is not its threshold.
check("silent on the old anchor", too_tight([4.7], 5.0, from_end=False),
      False)

print()
print("the lag is measured against the same anchor the window used:")


def lag(landing_sec, last_frame, fps, delta):
    return round(landing_sec - (float(last_frame) / fps - delta), 2)


check("a ball down 4.2s after it was last seen",
      lag(9.7, 330, 60.0, 0.0), 4.2)
# IF THE DELTA IS WRONG THE LAG IS WRONG BY THE SAME AMOUNT, which is
# what makes a population of these readable: a drifting lag across a
# round is a clock problem, not a flight that changed.
check("...and the offset moves it exactly as much",
      lag(9.7, 330, 60.0, 1.0) - lag(9.7, 330, 60.0, 0.0), 1.0)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
