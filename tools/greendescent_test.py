#!/usr/bin/env python3
"""The descent, walked backwards from a ball we already measured.

WHY BACKWARDS, AND WHY THIS IS TESTABLE AT ALL. The old search
segmented every frame of a window of green footage, built chains out
of everything that moved, and tried to pick the ball out of them with
gates -- on a green ringed with brush, where the wind moves more of
the frame than the ball does. There was no ground truth to test it
against because the whole problem was identifying the subject.

Walking back from a MEASURED resting place turns it into a tracking
problem with a known start, which has an answer you can check. So the
fixture plants a ball on a known parabola, bounces it once, rolls it
to a known pixel, and the test asks for that parabola back.

THREE FAILURES THIS LOCKS DOWN, each of which cost a debugging round:

  * velocity measured from the running position rather than the last
    point actually SEEN. After a miss the running position has
    drifted, so the next hit reads that drift as motion, the next
    prediction overshoots, and the walk settles into a limit cycle
    that finds the ball every fifth frame. 26 found and 66
    extrapolated, on a fixture where the ball is plainly visible in
    every frame.

  * the detector's area bounds applied in the ZOOMED zone's pixels.
    At 6x a 50px ball is 1800px of area and dies on a ceiling written
    for 400 -- inside the detector, before the caller can see it.
    Zero detections on twenty-two frames of a stationary ball.

  * the top-hat kernel left at 15px while the crop was scaled up.
    Once the ball is wider than the kernel the opening keeps its own
    interior and the top-hat returns a thin rim, which fragments into
    specks and fails roundness. One blob at 2x, twenty at 3x.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_fails = []

W, H, FPS, N = 1920, 1080, 60, 300
ENTER = 110          # the frame the ball enters, falling
LAND = 58            # frames after ENTER that it first touches down
ROLL = LAND + 14     # ...and finishes its one bounce
REST = (1689, 811)
REST_FRAME = 260


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def truth(t):
    """Where the ball is `t` frames after it entered."""
    return 1300 + 2.2 * t, 120 + 0.26 * t * t


def build(path):
    """A green clip: sky, treeline, striped turf, a waving branch.

    The branch is the control. It is the only other thing in the
    picture that moves, it moves every frame, and the walk must never
    once consider it -- not because a gate rejects it, but because it
    is never inside the window.
    """
    rng = np.random.default_rng(5)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    seen = {}
    for f in range(N):
        img = np.full((H, W, 3), 60, np.uint8)
        img[:330] = (175, 182, 192)
        img[330:600] = (42, 58, 44)
        for y in range(600, H):
            b = 80 + 7 * ((y // 26) % 2)
            img[y, :] = (b - 12, b + 10, b - 6)
        img[600:] = cv2.add(
            img[600:], rng.integers(0, 7, (H - 600, W, 3), dtype=np.uint8))
        bx = 500 + int(26 * np.sin(f / 4.0))
        cv2.line(img, (bx, 380), (bx + 50, 430), (150, 160, 150), 3)
        t = f - ENTER
        xy = None
        if t < 0:
            pass
        elif t < LAND:
            xy = truth(t)
        elif t < ROLL:
            u = t - LAND
            xy = (truth(LAND)[0] + 6.0 * u,
                  truth(LAND)[1] - 16.0 * u + 1.6 * u * u)
        else:
            u = min(1.0, (t - ROLL) / 26.0)
            x0 = truth(LAND)[0] + 6.0 * 14
            y0 = truth(LAND)[1]
            xy = (x0 + (REST[0] - x0) * u, y0 + (REST[1] - y0) * u)
        if xy:
            seen[f] = xy
            cv2.circle(img, (int(xy[0]), int(xy[1])), 4, (240, 242, 244), -1)
        vw.write(img)
    vw.release()
    return seen


def main():
    from app.services.greendescent import track_descent_back

    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "green.mp4"
        seen = build(clip)
        r = track_descent_back(clip, REST, REST_FRAME,
                               out_dir=d, stem="t")

        ok("the walk finds a descent", r["ok"], True)
        # IT REACHES THE FRAME THE BALL ENTERED. Anything later means
        # it lost the ball on the way up and stopped early, which is
        # the failure every bug above produced.
        ok("  and walks all the way back to the entry frame",
           r["first_frame"], ENTER)
        ok("  with a point for every frame between", r["n_points"],
           REST_FRAME - ENTER + 1)
        ok("  and barely any of them extrapolated",
           r["n_missed"] <= 3, True)
        ok("  falling, not rising", r["fall_px"] > 400, True)

        # ── THE PARABOLA COMES BACK ─────────────────────────────────
        err = []
        for q in r["points"]:
            t = q["frame"] - ENTER
            if 0 <= t < LAND and q["found"]:
                tx, ty = truth(t)
                err.append(((q["x"] - tx) ** 2 + (q["y"] - ty) ** 2) ** 0.5)
        ok("every in-flight frame is tracked", len(err), LAND)
        ok("  to within two pixels of where the ball was drawn",
           max(err) < 2.0, True)

        # ── THE PITCH MARK IS THE FIRST CONTACT, NOT THE LAST ───────
        # The difference is a whole bounce. "The last frame it was
        # falling fast" is the end of the bounce, several feet from
        # where the ball actually pitched, and what everything
        # downstream calls the landing is the first contact.
        tx, ty = truth(LAND)
        ok("the pitch mark is the first contact's frame",
           abs(r["pitch_frame"] - (ENTER + LAND)) <= 1, True)
        ok("  and its pixel, within two",
           (abs(r["pitch_xy"][0] - tx) <= 2,
            abs(r["pitch_xy"][1] - ty) <= 2), (True, True))

        # ── THE BRANCH IS NEVER CONSIDERED ──────────────────────────
        # Not rejected by a gate: never inside the window. That is
        # the whole argument for walking backwards from a known ball
        # rather than searching the green for an unknown one.
        ok("nothing is tracked anywhere near the waving branch",
           any(q["x"] < 700 and q["y"] < 500 for q in r["points"]), False)

        # ── AND THE PICTURES EXIST ──────────────────────────────────
        ok("the chain picture is drawn", bool(r["image"]), True)
        ok("  and a handful of the search windows",
           1 <= len(r["windows"]) <= 8, True)
        ok("  each at the zoom it was searched at",
           all(w["zoom"] >= 1.0 for w in r["windows"]), True)

        # ── A LONG REST BEFORE THE WALK EVEN STARTS ─────────────────
        #
        # The walk begins at the frame the ball was MEASURED at, and
        # that is wherever the green window ladder found it -- eight,
        # fourteen, twenty seconds after the strike. The ball has
        # been lying still for most of that. With one budget for the
        # whole walk, the budget was spent on the rest: a real clip
        # reported "241 points, falling 0px over 240 frames" and
        # stopped, having watched a stationary ball the entire way
        # and never reached the descent at all.
        #
        # A stationary frame is nearly free -- one small window, the
        # ball exactly where it was -- so the rest gets its own
        # generous allowance and the four seconds that matter start
        # counting from the frame the ball is first seen to move.
        r_late = track_descent_back(clip, REST, 299)
        ok("a walk started long after the ball settled still reaches it",
           r_late["first_frame"], ENTER)
        ok("  and says when it first saw it move",
           r_late.get("moved_at_frame") is not None, True)
        ok("  and finds the same pitch mark",
           abs(r_late["pitch_frame"] - (ENTER + LAND)) <= 1, True)
        ok("  and the same fall", r_late["fall_px"], r["fall_px"])

        # ── NO BALL AT REST, NO DESCENT ─────────────────────────────
        # The contract. The old behaviour -- hunt the green and attach
        # whatever best survived the gates -- is what produced
        # landings on clips where the shot missed the green.
        r2 = track_descent_back(clip, None, REST_FRAME)
        ok("no resting place means no walk", r2["ok"], False)
        ok("  and it says so", "no ball at rest" in (r2["why"] or ""), True)

        # ── A RESTING PLACE WITH NOTHING BEHIND IT ──────────────────
        # Pointed at bare turf, the walk must come back empty rather
        # than wander off following noise.
        r3 = track_descent_back(clip, (300, 950), 60)
        ok("a spot with no ball on it yields no chain",
           r3["n_points"] <= 3, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
