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
# EVERY _GREEN_* CONSTANT, READ FROM SOURCE. Two of them used to be
# typed in here as literals, and when the hold changed from 1.7s to
# 3.5s this file went on asserting against 1.7 -- passing, while
# describing a pan that no longer existed. A test that carries its own
# copy of the number under test is not testing it.
_HDR = "\n".join(
    f"{name} = {value}"
    for name, value in re.findall(
        r"^(_GREEN_[A-Z_]+)\s*=\s*([0-9.]+)", _SRC, re.M,
    )
) + "\n"
assert "_GREEN_BALL_HOLD_SEC" in _HDR, "the green constants moved"
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

    # ---- the green half, in three beats -------------------------
    # Clip 1684: cut 8.03s, dur 13.8s, gball 0.323, pin 0.679(marked).
    # Land on the ball, hold on it, walk to the flag, stay there.
    HOLD = _ns["_GREEN_BALL_HOLD_SEC"]
    PAN = _ns["_GREEN_PIN_PAN_SEC"]
    G_CUT, G_DUR, GBALL, G_PIN = 8.03, 13.8, 0.323, 0.679

    def green(total_dur=G_DUR, ball=GBALL, pin=G_PIN):
        return _vertical_pan_path(
            TRACK, None, FPS, FW, G_CUT, golfer_x=None, impact_t=IMPACT,
            green_x=pin, ball_green_x=ball, total_dur=total_dur,
            opening_x=TRACER_X, opening_t=TRACER_T,
        )

    def at(path, t):
        pts = sorted(path)
        for i in range(len(pts) - 1):
            (t0, x0), (t1, x1) = pts[i], pts[i + 1]
            if t0 <= t <= t1:
                return x1 if t1 == t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    p = green()
    print(f"\ngreen half (cut {G_CUT}s, dur {G_DUR}s, "
          f"hold {HOLD}s, pan {PAN}s):")
    for t, label in ((G_CUT, "at the cut"),
                     (G_CUT + HOLD - 0.1, "end of the hold"),
                     (G_CUT + HOLD + PAN, "end of the pan"),
                     (G_DUR - 0.1, "last frame")):
        print(f"    t={t:5.2f}  {label:16s} x={at(p, t):.3f}")

    assert abs(at(p, G_CUT) - GBALL) < 1e-9, "does not open on the ball"
    assert abs(at(p, G_CUT + HOLD - 0.1) - GBALL) < 1e-9, "left the ball early"
    assert abs(at(p, G_CUT + HOLD + PAN) - G_PIN) < 1e-9, "never reaches the pin"
    assert abs(at(p, G_DUR - 0.1) - G_PIN) < 1e-9, "did not hold the pin"
    # It must MOVE through the middle, not jump.
    mid = at(p, G_CUT + HOLD + PAN / 2)
    assert GBALL < mid < G_PIN, f"the walk teleported: midpoint {mid:.3f}"
    print(f"    midway through the pan: x={mid:.3f} (between the two)")

    # THE BUG, NAMED. Opening on the ball used to sit behind a room
    # check, and room is 0.0 when the duration is unknown -- so a clip
    # whose duration failed to probe went straight to the pin and never
    # showed where the shot finished. Clip 1684 read `gball 0.323 ·
    # open@cut 0.679(1pt)`.
    p_nodur = green(total_dur=None)
    assert abs(at(p_nodur, G_CUT) - GBALL) < 1e-9, (
        "an unknown duration still loses the ball framing")
    assert abs(at(p_nodur, G_CUT + HOLD + PAN) - G_PIN) < 1e-9
    print("    duration unknown: still opens on the ball, still reaches the pin")

    # A SHORT GREEN HALF SHRINKS THE BEATS, it does not skip one.
    p_short = green(total_dur=G_CUT + 2.0)
    assert abs(at(p_short, G_CUT) - GBALL) < 1e-9, (
        "a short green half dropped the ball framing")
    moved = [t for t, x in sorted(p_short) if t > G_CUT and abs(x - G_PIN) < 1e-9]
    assert moved and moved[0] - G_CUT <= 2.0 + 1e-6, (
        "the squeezed walk does not finish inside the clip")
    print(f"    2s of green: ball at the cut, pin by "
          f"{moved[0] - G_CUT:.2f}s in")

    # No pin: hold the ball rather than inventing somewhere to go.
    p_nopin = green(pin=None)
    assert abs(at(p_nopin, G_DUR - 0.1) - GBALL) < 1e-9, "wandered off the ball"
    # No ball: the flag, as before.
    p_noball = green(ball=None)
    assert abs(at(p_noball, G_CUT) - G_PIN) < 1e-9, "no ball should open on the pin"
    print("    no pin -> holds the ball; no ball -> opens on the pin")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
