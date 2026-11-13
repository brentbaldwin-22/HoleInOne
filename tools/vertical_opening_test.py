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

    # A TRACK RUNNING PAST THE CUT USED TO KILL THE GREEN HALF. The
    # block sat behind `cut_dur > t_last`, t_last being the last DRAWN
    # tracer waypoint, and the tracer keeps being drawn along the aimed
    # continuation toward the landing -- so whenever that ran past the
    # cut the whole block was skipped and the crop held the tee's last
    # position across the scene change. gball was found, reported and
    # never framed. The tee track's post-cut points are dropped now,
    # because they are positions in a picture that is no longer on
    # screen, so t_last is always before the cut.
    late = [{"frame": int(t * FPS), "x": x * FW} for t, x in
            [(2.05, 0.55), (4.0, 0.62), (7.5, 0.70)]]
    p_late = _vertical_pan_path(
        late, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
        green_x=0.67, ball_green_x=0.381, total_dur=12.2,
        opening_x=TRACER_X, opening_t=TRACER_T,
    )
    opens = next(x for t, x in sorted(p_late) if t >= 7.03)
    print(f"\ntrack running to 7.5s (past the 7.03s cut): "
          f"green half opens at {opens:.3f}")
    assert abs(opens - 0.381) < 1e-9, (
        f"opens at {opens:.3f}: the green half is still being dropped")
    # And no tee coordinate leaked across the cut.
    assert all(t < 7.03 or x <= 0.68 for t, x in p_late), (
        "a tee position survived into the green half")

    # The same clip with the track ending early behaves identically --
    # the two cases are no longer distinguishable, which is the point.
    early = [{"frame": int(t * FPS), "x": x * FW} for t, x in
             [(2.05, 0.55), (4.0, 0.62), (6.0, 0.70)]]
    p_early = _vertical_pan_path(
        early, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
        green_x=0.67, ball_green_x=0.381, total_dur=12.2,
        opening_x=TRACER_X, opening_t=TRACER_T,
    )
    assert abs(next(x for t, x in sorted(p_early) if t >= 7.03)
               - 0.381) < 1e-9
    print("track ending at 6.0s: same, opens on the ball")

    # ---- the green half follows the stroke ---------------------
    # The ball is still coming DOWN when the picture cuts, so the
    # stroke is in a different place at the cut than where it lands.
    # Framing the landing leaves it off to one side for the whole
    # descent, which is what was on screen.
    GTRACK = [(7.05, 0.55), (7.40, 0.49), (7.80, 0.43), (8.20, 0.381)]
    p_follow = _vertical_pan_path(
        TRACK, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
        green_x=0.67, ball_green_x=0.381, total_dur=12.2,
        opening_x=TRACER_X, opening_t=TRACER_T, green_track=GTRACK,
    )

    def at(path, t):
        """Where the crop is told to be at time t."""
        pts = sorted(path)
        for i in range(len(pts) - 1):
            (t0, x0), (t1, x1) = pts[i], pts[i + 1]
            if t0 <= t <= t1:
                if t1 == t0:
                    return x1
                return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    print("\ngreen half following the stroke:")
    for t in (7.05, 7.40, 7.80, 8.20):
        want = dict(GTRACK)[t]
        got = at(p_follow, t)
        print(f"    t={t:.2f}  stroke at {want:.3f}  crop at {got:.3f}")
        assert abs(got - want) < 1e-6, (t, got, want)

    # The old behaviour: one jump to the landing, so at the cut the
    # stroke sits 0.17 of a frame off centre -- more than half the
    # 9:16 crop's half-width, i.e. visibly off to the side.
    p_jump = _vertical_pan_path(
        TRACK, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
        green_x=0.67, ball_green_x=0.381, total_dur=12.2,
        opening_x=TRACER_X, opening_t=TRACER_T, green_track=None,
    )
    off = abs(at(p_jump, 7.05) - 0.55)
    print(f"    without the track, at t=7.05 the stroke is "
          f"{off:.3f} off centre")
    assert off > 0.15, (
        "MUTATION DID NOT BITE: jumping to the landing centred it anyway")

    # ---- a track running past the cut must not kill the green ----
    # Previously `cut_dur > t_last` went false and the WHOLE green
    # block was skipped. The tee track's post-cut points are dropped
    # now, so the green half survives.
    late = [{"frame": int(t * FPS), "x": x * FW} for t, x in
            [(2.05, 0.55), (4.0, 0.62), (7.5, 0.70)]]
    p_late = _vertical_pan_path(
        late, None, FPS, FW, 7.03, golfer_x=None, impact_t=IMPACT,
        green_x=0.67, ball_green_x=0.381, total_dur=12.2,
        opening_x=TRACER_X, opening_t=TRACER_T, green_track=GTRACK,
    )
    assert abs(at(p_late, 7.05) - 0.55) < 1e-6, (
        "a track running past the cut still loses the green half")
    assert all(t < 7.03 or x <= 0.68 for t, x in p_late), (
        "a tee coordinate survived into the green half")
    print("    track running to 7.5s: green half survives, opens at 0.550")

    # ...and it still reaches the pin.
    assert abs(at(p_follow, 12.0) - 0.67) < 1e-6, "never reaches the pin"
    print("    still walks to the pin by the end")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
