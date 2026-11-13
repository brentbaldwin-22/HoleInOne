#!/usr/bin/env python3
"""DOES THE 9:16 OPEN ON THE TRACER, AND STAY THERE?

The complaint: "it starts in the middle of the screen and then pans to
the tracer number". The pan path DID open on the tracer -- and then the
CONTACT GATE reopened at `impact_t`, so the first tracked point (the
ball detector's guess at that same instant) pulled the camera straight
back off it. On clip 1430 that was 0.601 -> 0.550 in a single frame,
2.03s in, which reads as an opening that drifts.

`_tracer_start_x` already returned the tracer's timestamp and nothing
used it. The gate now holds until THAT, so the flight starts where the
tracer starts and the opening framing survives impact.

admin.py cannot be imported here (fastapi is absent, and py3.9 chokes on
deps.py's `str | None` at def time), so the function is read off the
shipping source and exec'd alone -- the same trick the other tools use.

    python3 tools/vertical_opening_test.py
"""

import pathlib
import re

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "backend/app/routers/admin.py").read_text()
_HDR = (
    "_GREEN_BALL_HOLD_SEC = 1.7\n"
    "_GREEN_PIN_PAN_SEC = 2.0\n"
    "_GREEN_END_HOLD_SEC = "
    + re.search(r"_GREEN_END_HOLD_SEC\s*=\s*([0-9.]+)", _SRC).group(1)
    + "\n"
)
_ns: dict = {}
exec(
    _HDR + _SRC[_SRC.index("def _vertical_pan_path("):
                _SRC.index("def _auto_delete_upload(")],
    _ns,
)
_vertical_pan_path = _ns["_vertical_pan_path"]

# Clip 1430 as the console reports it.
FW, FPS = 1920, 30.0
TRACER_X, TRACER_T = 0.601, 2.10
CUT, DUR, PIN, IMPACT = 6.37, 11.53, 0.265, 2.0

# A tracked flight whose first point disagrees with the tracer -- which
# is the whole problem: both claim to be the ball at contact.
TRACK = [{"frame": int(t * FPS), "x": x * FW} for t, x in
         [(2.05, 0.55), (2.4, 0.58), (2.9, 0.62), (3.6, 0.66), (4.5, 0.70)]]


def path_of(**kw):
    base = dict(golfer_x=None, impact_t=IMPACT, green_x=PIN,
                ball_green_x=None, total_dur=DUR, opening_x=TRACER_X)
    base.update(kw)
    return _vertical_pan_path(TRACK, None, FPS, FW, CUT, **base)


def first_move(path, x0, before):
    """When the camera first leaves `x0`, ignoring the green half."""
    moved = [t for t, x in path if abs(x - x0) > 1e-9 and t < before]
    return min(moved) if moved else None


def main() -> int:
    fixed = path_of(opening_t=TRACER_T)
    print("opening framing, tracer timestamp known")
    for t, x in fixed[:4]:
        print(f"    t={t:6.2f}  x={x:.3f}")

    assert abs(fixed[0][1] - TRACER_X) < 1e-9, (
        f"opens at {fixed[0][1]:.3f}, not the tracer {TRACER_X}")
    moved_at = first_move(fixed, TRACER_X, CUT)
    assert moved_at is None or moved_at >= TRACER_T - 0.06, (
        f"camera moved at {moved_at:.2f}s, before the tracer at {TRACER_T}")
    drift = max((abs(x - TRACER_X) for t, x in fixed if t <= CUT - 0.1),
                default=0.0)
    assert drift < 0.11, f"drifted {drift:.3f} off the opening before the cut"
    print(f"    opens on the tracer; first move {moved_at:.2f}s "
          f"(tracer {TRACER_T}); drift {drift:.3f}")

    # MUTATION: withhold the timestamp -- the pre-fix code path. If the
    # assertions above still pass, they are not testing the fix.
    old = path_of(opening_t=None)
    moved_old = first_move(old, TRACER_X, CUT)
    assert moved_old is not None and moved_old < TRACER_T, (
        "MUTATION DID NOT BITE: the old path kept the opening too")
    print(f"    mutation: without it the camera leaves at {moved_old:.2f}s "
          f"-> {old[2][1]:.3f}, before the tracer exists")

    # NO TRACER, NO CHANGE. The gate must still be impact_t alone, or
    # this fix has quietly moved every clip the scan cannot read.
    plain = _vertical_pan_path(
        TRACK, None, FPS, FW, CUT, golfer_x=None, impact_t=IMPACT,
        green_x=PIN, ball_green_x=None, total_dur=DUR,
        opening_x=None, opening_t=None,
    )
    got = [(round(t, 2), round(x, 3)) for t, x in plain[:3]]
    want = [(0.0, 0.58), (1.98, 0.58), (2.03, 0.55)]
    assert got == want, f"regression with no tracer: {got} != {want}"
    print(f"    no tracer: unchanged {got[0]} -> {got[2]}")

    # ---- the green half lands on the ball ----------------------
    # Clip 1444: cut 7.03s, composite 12.2s, gball 0.381, pin 0.67.
    # The green half must OPEN on the ball before it walks to the pin.
    def green_at_cut(total_dur):
        p = _vertical_pan_path(
            TRACK, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
            green_x=0.67, ball_green_x=0.381, total_dur=total_dur,
            opening_x=TRACER_X, opening_t=TRACER_T,
        )
        # The first waypoint at or after the cut is what the render
        # teleports to when the picture changes.
        return next(x for t, x in p if t >= 7.03)

    at_cut = green_at_cut(12.2)
    print(f"\ngreen half, composite 12.2s: opens at {at_cut:.3f}")
    assert abs(at_cut - 0.381) < 1e-9, (
        f"opens at {at_cut:.3f}, not the ball at 0.381")

    # THE BUG, NAMED. Produce probed seg_path -- the TEE segment -- so
    # total_dur came back as the tee's ~7s for a 12.2s clip. The green
    # half then measured as roughly zero, "not enough room to move in"
    # fired, and it snapped to the pin at the cut. gball was found and
    # never framed.
    tee_only = green_at_cut(7.03)
    print(f"green half, tee duration 7.03s: opens at {tee_only:.3f} "
          f"(the pin) -- the bug")
    assert abs(tee_only - 0.67) < 1e-9, (
        "MUTATION DID NOT BITE: the wrong duration framed the ball anyway")

    # The short-green fallback is deliberate and must survive: with
    # genuinely no room, the pin alone beats a move that never lands.
    assert abs(green_at_cut(9.0) - 0.67) < 1e-9, (
        "a 2s green half should still go straight to the pin")
    print("short green half (2.0s): still straight to the pin, as designed")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
