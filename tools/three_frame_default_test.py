#!/usr/bin/env python3
"""The sweep diffs against one neighbour, not two.

MEASURED, ON ONE CLIP, WITH ONE GATE CHANGED. Hole 8, 20 Oct:

    three_frame=1     3 chains weighed,  0 accepted
    three_frame=0    18 chains weighed,  2 accepted

and the chain that had been five points and 78px of rise -- refused
for `rise` while passing every other gate it was judged on -- came
back as twenty-two points and 504px of rise, accepted.

WHY. The AND asks the ball to clear threshold in the diff against the
frame BEFORE and the frame AFTER, at the same pixels. That is a
strictly harder test than either diff alone, and a ball near the top
of its climb is faint: it is further away, smaller, and its light is
smeared across the pixels it crossed during the exposure. The AND was
refusing it for being dim.

It was there to kill the "where it was" ghost, which otherwise
measures a ball's velocity as zero -- on a synthetic ascent, eight
frames of perfect detections yielded ZERO tracks. That job now belongs
to ascent_ghost_px, which drops a detection sitting where the last one
was. Cheaper, and it cannot refuse a ball for being faint.

    python3 tools/three_frame_default_test.py
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def test_the_default_is_two_frame():
    check("the gate ships off", d3.GATE_SPEC["ascent_three_frame"][0], 0)
    # AND THE GHOST HANDLING IS ON, because it is now the only thing
    # doing that job rather than a fallback for when the AND is off.
    check("and the ghost drop is on to cover it",
          d3.GATE_SPEC["ascent_ghost_px"][0] > 0, True)


def test_the_sweep_actually_runs_two_frame():
    """The gate is read and passed, not merely declared.

    This exact hole has opened five times in this module -- a value
    resolved from the gates and then not handed to the thing it
    governs -- so it is spied rather than assumed.
    """
    import numpy as np
    import cv2

    seen: dict = {}
    _real = d3.detect_movers_by_diff

    def spy(*a, **kw):
        seen.setdefault("three_frame", kw.get("three_frame"))
        seen.setdefault("ghost_px", kw.get("ghost_px"))
        return _real(*a, **kw)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.mp4"
        vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"),
                             60.0, (640, 360))
        for f in range(20):
            im = np.full((360, 640, 3), 118, dtype="uint8")
            cv2.circle(im, (300, 300 - 8 * f), 4, (245, 245, 245), -1)
            vw.write(im)
        vw.release()
        d3.detect_movers_by_diff = spy
        try:
            out = d3.sweep_ascents(p, 60.0, roi={"y": 0.95})
        finally:
            d3.detect_movers_by_diff = _real
    check("the detector is asked for two-frame", seen.get("three_frame"),
          False)
    check("and the ghost drop is passed with it",
          (seen.get("ghost_px") or 0) > 0, True)
    check("and the sweep reports what it used", out.get("three_frame"),
          False)


def test_the_ghost_drop_is_only_live_without_the_and():
    """Belt and braces are not worn together.

    Three-frame differencing has no ghosts to remove, so the sweep
    passes 0 when it is on. Turning the AND back on must not leave the
    ghost radius eating real detections as well.
    """
    import numpy as np
    import cv2

    seen: dict = {}
    _real = d3.detect_movers_by_diff

    def spy(*a, **kw):
        seen.setdefault("ghost_px", kw.get("ghost_px"))
        return _real(*a, **kw)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.mp4"
        vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"),
                             60.0, (640, 360))
        for f in range(20):
            im = np.full((360, 640, 3), 118, dtype="uint8")
            cv2.circle(im, (300, 300 - 8 * f), 4, (245, 245, 245), -1)
            vw.write(im)
        vw.release()
        d3.detect_movers_by_diff = spy
        try:
            d3.sweep_ascents(p, 60.0, roi={"y": 0.95},
                             gates={"ascent_three_frame": 1})
        finally:
            d3.detect_movers_by_diff = _real
    check("with the AND back on, no ghost radius", seen.get("ghost_px"), 0.0)


def test_a_faint_ball_survives_two_frame_and_not_the_and():
    """The mechanism, on a ball at the contrast a real one reaches.

    Not a tie-break: at this contrast the AND loses most of the flight
    and a single diff keeps nearly all of it.
    """
    import numpy as np
    import cv2

    # CONTRAST SIX, SMEARED OVER TWELVE PIXELS, against three levels
    # of noise. Measured, not picked: at contrast 8 the AND still gets
    # 32 frames of 46 and the difference is an argument about
    # percentages. At 6 it gets ONE, and a single diff gets 29. That is
    # the regime a ball is in by the top of its climb -- faint,
    # smeared, and near enough to threshold that needing to clear it
    # TWICE is the whole difference.
    W, H, N = 640, 400, 46
    BX, Y0, STEP, SMEAR = 300, 360.0, 7.0, 12
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        rng = np.random.default_rng(7)
        vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"),
                             60.0, (W, H))
        for f in range(N):
            im = np.full((H, W, 3), 110, dtype="int16")
            im += rng.normal(0, 3, (H, W, 3)).astype("int16")
            im = np.clip(im, 0, 255).astype("uint8")
            y = Y0 - STEP * f
            if 6 < y < H - 6:
                cv2.line(im, (BX, int(y)), (BX, int(y + SMEAR)),
                         (116, 116, 116), 2)
            vw.write(im)
        vw.release()
        _and = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                        three_frame=True, merge_px=20)
        _one = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                        three_frame=False, ghost_px=4,
                                        merge_px=20)

    def near(ds):
        return {int(d["frame"]) for d in ds if abs(d["x"] - BX) < 25}

    _a, _o = len(near(_and)), len(near(_one))
    check("one diff keeps the flight", _o >= 25, True)
    check("the AND keeps almost none of it", _a <= 5, True)
    # CONTROL: the ball really is there on nearly every frame, so this
    # is the AND refusing it rather than the fixture being empty.
    check("control, the ball is in frame throughout",
          sum(1 for f in range(N) if 6 < Y0 - STEP * f < H - 6) >= 40,
          True)


for fn in (test_the_default_is_two_frame,
           test_the_sweep_actually_runs_two_frame,
           test_the_ghost_drop_is_only_live_without_the_and,
           test_a_faint_ball_survives_two_frame_and_not_the_and):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
