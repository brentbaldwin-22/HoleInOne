#!/usr/bin/env python3
"""Can a two-frame diff lose its ghost without a third frame?

A difference against the previous frame lights a mover TWICE: where it
is, and where it was. For a ball that is two detections a frame, and
the tracker takes the nearer one — the ghost, at distance zero. It then
measures the ball's velocity as zero, and the next real position is
outside a gate sized for something standing still, so the chain dies at
two points.

Three-frame differencing solves that by ANDing the diffs either side.
But the AND also asks the ball to clear threshold in BOTH diffs at the
same pixels, which is strictly harder than either alone — and on
Rivertowne 8 that is the difference between a scan finding the flight
on thirty frames and the sweep finding it on four.

The ghost is removable without the third frame, because it sits exactly
on the PREVIOUS frame's real detection. That is what this tests, and
the thing that must not break with it: a ball moving 25-50px a frame
has to come through untouched.

    python3 tools/ghost_suppress_test.py
"""
from __future__ import annotations

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


W, H, N = 640, 360, 24
VY = -14.0          # a ball, well clear of any ghost radius
Y0 = 330.0
BX = 400


def clip(path, jitter_px=0):
    """A ball climbing, plus a 'tree' that moves `jitter_px` a frame."""
    import numpy as np
    import cv2

    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    truth = []
    for f in range(N):
        im = np.full((H, W, 3), 110, dtype="uint8")
        y = Y0 + VY * f
        if 5 < y < H - 5:
            cv2.circle(im, (BX, int(y)), 5, (245, 245, 245), -1)
            truth.append((f, BX, int(y)))
        if jitter_px:
            cv2.circle(im, (120 + (f % 2) * jitter_px, 200), 9,
                       (60, 80, 50), -1)
        vw.write(im)
    vw.release()
    return truth


def near_ball(dets, truth, tol=12):
    by_f = {}
    for d in dets:
        by_f.setdefault(d["frame"], []).append(d)
    return sum(1 for f, x, y in truth
               if any(abs(d["x"] - x) <= tol and abs(d["y"] - y) <= tol
                      for d in by_f.get(f, [])))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ball.mp4"
        truth = clip(p, jitter_px=2)

        two = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=20,
                                       three_frame=False)
        two_ng = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=20,
                                          three_frame=False, ghost_px=4)
        three = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=20,
                                         three_frame=True)

        # ---- the ball survives all three ----------------------------
        n_true = len(truth)
        check("two-frame sees the ball", near_ball(two, truth) >= n_true - 2,
              True)
        check("  ghost suppression does not cost it",
              near_ball(two_ng, truth) >= n_true - 2, True)
        check("  nor does three-frame (control)",
              near_ball(three, truth) >= n_true - 2, True)

        # ---- but the ghosts go --------------------------------------
        # Two-frame lights the ball twice a frame; dropping what sits on
        # the previous frame's detection has to remove roughly half.
        check("two-frame returns more than three-frame, as it must",
              len(two) > len(three), True)
        check("  and suppression cuts that back",
              len(two_ng) < len(two), True)

        # THE TREE, which moves 2px a frame, is what suppression is
        # also meant to take — a leaf edge lit by camera shake sits
        # within a pixel or two of where it was, every frame.
        def near_tree(dets):
            return sum(1 for d in dets if abs(d["x"] - 121) < 25
                       and abs(d["y"] - 200) < 25)
        check("a 2px-a-frame jitterer is largely removed",
              near_tree(two_ng) < near_tree(two) / 2, True)

        # ---- and a FAST ball is never a ghost, whatever the radius ---
        # The guard that matters: an ascent crosses 25-50px a frame, so
        # no plausible ghost radius can reach it.
        p2 = Path(td) / "fast.mp4"
        truth2 = clip(p2, jitter_px=0)
        wide = d3.detect_movers_by_diff(p2, 0, N - 1, sens=3, per_frame=20,
                                        three_frame=False, ghost_px=12)
        check("a ball at 14px/frame survives a 12px ghost radius",
              near_ball(wide, truth2) >= len(truth2) - 2, True)

        # ---- off by default -----------------------------------------
        check("ghost_px=0 changes nothing (control)",
              len(d3.detect_movers_by_diff(p, 0, N - 1, sens=3,
                                           per_frame=20,
                                           three_frame=False,
                                           ghost_px=0)), len(two))

    # ---- and it is a gate, only read with two-frame -----------------
    check("the radius is tunable", d3.GATE_DEFAULTS["ascent_ghost_px"], 4)
    check("  and clamps at the rail",
          d3.resolve_gates({"ascent_ghost_px": 999})["ascent_ghost_px"],
          d3.GATE_SPEC["ascent_ghost_px"][2])

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
