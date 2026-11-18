#!/usr/bin/env python3
"""HOW FAR EITHER SIDE OF A TEE TIME TO LOOK FOR A SHOT.

A noon tee time has no business being shown 9am clips when the
operator is pairing hole 2. But the arithmetic that makes hole 2 tight
makes hole 17 wrong: pace error ACCUMULATES, every hole played being
another hole the group could have been quick or slow on, so a fixed
window would quietly hide the very clip being looked for. The spread
has to grow with the hole.

The property that matters most here is not tightness. It is that the
window NEVER SILENTLY LOSES A CLIP: no tee time means no window, and
the console shows everything rather than nothing.

    python3 tools/shot_window_test.py
"""

from datetime import datetime, timedelta
from pathlib import Path

# identify.py imports sqlalchemy, which is not installed here, and
# `shot_window` needs none of it. Read the model off the shipping
# source and exec it alone -- the same trick tools/tracer_foot_test.py
# uses on admin.py.
_SRC = (Path(__file__).resolve().parents[1]
        / "backend/app/services/identify.py").read_text()
_ns = {"timedelta": timedelta}
exec(
    _SRC[_SRC.index("PACE_NOMINAL_MIN = "):
         _SRC.index("def _player_row(")],
    _ns,
)
shot_window = _ns["shot_window"]
PACE_NOMINAL_MIN = _ns["PACE_NOMINAL_MIN"]
PACE_TOLERANCE_MIN = _ns["PACE_TOLERANCE_MIN"]
FIXED_SLACK_MIN = _ns["FIXED_SLACK_MIN"]

NOON = datetime(2026, 11, 18, 12, 0)
RIVERTOWNE = (2, 8, 14, 17)


def mins(a, b):
    return (b - a).total_seconds() / 60.0


def main() -> int:
    print(f"pace {PACE_NOMINAL_MIN}min/hole, tolerance "
          f"{PACE_TOLERANCE_MIN}min/hole, slack {FIXED_SLACK_MIN}min\n")
    print("noon tee time, Rivertowne's par 3s:")
    prev_width = -1
    for h in RIVERTOWNE:
        lo, hi = shot_window(NOON, h)
        width = mins(lo, hi)
        print(f"   hole {h:<3} {lo:%H:%M} - {hi:%H:%M}   "
              f"({width:.0f}m wide)")

        # The group cannot have reached hole h before it teed off.
        assert lo >= NOON - timedelta(minutes=FIXED_SLACK_MIN + 1), h
        # The window must contain the nominal arrival, or the model
        # disagrees with itself.
        nominal = NOON + timedelta(minutes=(h - 1) * PACE_NOMINAL_MIN)
        assert lo <= nominal <= hi, (h, nominal)
        # IT MUST WIDEN. A window that stayed the same size for hole 17
        # as for hole 2 is the bug this exists to avoid.
        assert width > prev_width, (
            f"hole {h} is no wider than the hole before it")
        prev_width = width

    # Hole 1 is played AT the tee time: no accumulated error at all.
    lo1, hi1 = shot_window(NOON, 1)
    assert mins(lo1, hi1) == 2 * FIXED_SLACK_MIN, mins(lo1, hi1)
    assert lo1 < NOON < hi1
    print(f"\n   hole 1   {lo1:%H:%M} - {hi1:%H:%M}   "
          f"(slack only -- no holes played yet)")

    # ---- the guarantee: no window beats a wrong window -------------
    # Every one of these must come back None, because the caller shows
    # EVERY clip when it does. Returning a window from a guess here is
    # how a clip goes missing.
    for bad, why in (
        ((None, 8), "no tee time"),
        ((NOON, None), "no hole"),
        ((NOON, 0), "hole 0"),
        ((NOON, -3), "negative hole"),
        ((NOON, "x"), "hole is not a number"),
    ):
        assert shot_window(*bad) is None, why
    print("   no tee time / no hole / bad hole -> None (show everything)")

    # ---- it has to actually narrow something ----------------------
    # An eleven-hour day of footage, and the point is to cut it down.
    day = 11 * 60
    lo2, hi2 = shot_window(NOON, 2)
    frac = mins(lo2, hi2) / day
    print(f"\n   hole 2 window is {frac:.0%} of an 11-hour day")
    assert frac < 0.15, f"{frac:.0%} of the day is not worth filtering for"

    # ...and at the far end it must still be a real reduction.
    lo17, hi17 = shot_window(NOON, 17)
    frac17 = mins(lo17, hi17) / day
    print(f"   hole 17 window is {frac17:.0%} of an 11-hour day")
    assert frac17 < 0.60, f"hole 17 at {frac17:.0%} barely narrows anything"

    # ---- two groups an hour apart must not overlap on hole 2 ------
    # If they did, the window would not be separating anybody.
    later = shot_window(NOON + timedelta(hours=2), 2)
    assert later[0] > hi2, "groups two hours apart share a hole-2 window"
    print("   groups 2h apart have disjoint hole-2 windows")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
