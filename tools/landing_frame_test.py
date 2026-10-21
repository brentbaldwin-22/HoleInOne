#!/usr/bin/env python3
"""When did the ball get there? Walk back from where it stopped.

    "only search for landing spot after we find the resting spot. if we
     don't find resting spot, then don't search for landing spot. it
     should just be a process of backing up some frames until the ball
     starts moving on the green. it doesn't have to be super exact —
     bigger deal is landing frame so the timing of the tracer will be
     correct."

Which is the right order, and it makes the problem small. Knowing
WHERE the ball finished turns "track a ball falling through a windy
green" into "watch one patch of grass and see when it changed": a few
hundred reads of a 40-pixel box, no tracking, no linking, nothing to
lose. Without a resting place there is nothing to watch, so the walk
does not run at all.

Precision is not the point. A few frames either way moves the tracer's
last moments imperceptibly; a landing frame seconds out makes the
whole clip wrong.

    python3 tools/landing_frame_test.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import ai_tracer as A  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


W, H, FPS, N = 640, 360, 30, 600          # 20 seconds
REST = (400, 260)
ARRIVES = 7 * FPS                          # f210, 7.0s


def clip(path, arrives=ARRIVES, ball=REST, leaves=None):
    rng = np.random.default_rng(4)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         float(FPS), (W, H))
    for f in range(N):
        im = np.full((H, W, 3), 60, np.uint8)
        im[200:, :] = (58, 110, 66)
        im[200:, :] = np.clip(
            im[200:, :].astype(np.int16)
            + rng.integers(-7, 8, size=(H - 200, W, 1)), 0, 255
        ).astype(np.uint8)
        here = (arrives is not None and f >= arrives
                and (leaves is None or f < leaves))
        if here and ball:
            cv2.circle(im, ball, 4, (250, 250, 250), -1)
        vw.write(im)
    vw.release()


def test_it_finds_the_frame_the_ball_arrived():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        got = A.find_landing_frame(p, REST, rest_sec=14.0)
    check("a landing frame comes back", got["error"], None)
    # Within a few frames is the whole requirement.
    check("  close to where the ball appeared",
          abs(got["frame"] - ARRIVES) <= 5, True)
    check("  and in seconds too", abs(got["sec"] - 7.0) <= 0.2, True)
    check("  having actually walked", got["walked_frames"] > 100, True)


def test_a_ball_that_was_always_there_is_not_a_landing():
    """It arrived before the window, so this clip cannot say when."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p, arrives=0)
        got = A.find_landing_frame(p, REST, rest_sec=14.0)
    check("no frame is invented", got["frame"], None)
    check("  and it says why",
          "already there" in (got["error"] or ""), True)


def test_an_empty_patch_has_no_landing():
    """And this is how it goes wrong if nobody checks.

    Walking back from the finish, the first three absent frames in a
    patch that NEVER held a ball are the last three — so the naive
    walk calls that the arrival and reports a confident landing time
    for a resting place that is simply wrong. Worse than no answer.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p, arrives=None)
        got = A.find_landing_frame(p, REST, rest_sec=14.0)
    check("nothing arrived, nothing reported", got["frame"], None)
    check("  and it names the real problem",
          "resting place is wrong" in (got["error"] or ""), True)
    # ...and the same check must not reject a ball that IS there,
    # which is the whole normal case.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        check("control, a ball at the end still walks",
              A.find_landing_frame(p, REST, rest_sec=14.0)["frame"]
              is not None, True)


def test_a_ball_picked_up_before_the_window_ends():
    """The patch is empty at the end, and that is NOT a landing."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p, leaves=12 * FPS)
        got = A.find_landing_frame(p, REST, rest_sec=14.0)
    check("gone by the end, no landing claimed", got["frame"], None)


def test_it_will_not_walk_without_a_resting_place():
    """The order the operator asked for: rest first, landing second."""
    got = A.find_landing_frame("/nope.mp4", None, rest_sec=14.0)
    check("no resting place, no walk", got["frame"], None)
    check("  and it says so",
          got["error"], "no resting place to walk back from")
    check("  without having opened anything", got["walked_frames"], 0)


def test_the_window_bounds_the_walk():
    """A short window cannot reach a landing that predates it."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        near = A.find_landing_frame(p, REST, rest_sec=14.0,
                                    max_walk_sec=2.0)
        far = A.find_landing_frame(p, REST, rest_sec=14.0,
                                   max_walk_sec=12.0)
    check("two seconds back cannot see f210", near["frame"], None)
    # CONTROL: the same clip with room to walk does find it, so the
    # None above is the window and not the detector.
    check("control, twelve seconds can", abs(far["frame"] - ARRIVES) <= 5,
          True)


def test_a_resting_place_off_the_frame_is_refused():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        got = A.find_landing_frame(p, (W + 500, H + 500), rest_sec=14.0)
    check("off the frame, no answer", got["frame"], None)


for fn in (test_it_finds_the_frame_the_ball_arrived,
           test_a_ball_that_was_always_there_is_not_a_landing,
           test_an_empty_patch_has_no_landing,
           test_a_ball_picked_up_before_the_window_ends,
           test_it_will_not_walk_without_a_resting_place,
           test_the_window_bounds_the_walk,
           test_a_resting_place_off_the_frame_is_refused):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
