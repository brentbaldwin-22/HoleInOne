#!/usr/bin/env python3
"""Does the per-frame cut keep the ball, or the biggest thing near it?

THE RULE INVERTS WITH THE SEARCH AREA. The detector keeps the
`per_frame` LARGEST blobs. Inside a ball-sized window the ball is the
big one, which is where that came from. Across a band with a treeline
in it, a leaf edge lit by a pixel of camera shake is hundreds of pixels
of blob and a ball at 180 yards is a few — so the cut keeps the trees
and throws the ball away, and the bigger the region the more certain
that is.

That is the whole reason the operator's own scan finds this flight on
thirty frames at 2x, where the crop excludes the trees and the ball IS
the big one, and loses it at 1x. Raising the cap does not help: it
admits more noise above the ball, not the ball.

So: rank by NEARNESS to the ball's own size instead.

    python3 tools/prefer_area_test.py
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


W, H, N = 960, 540, 24
BX, Y0, VY = 700, 500.0, -18.0
BALL_R = 3          # a few pixels, like a ball at range
TREE_R = 14         # a leaf edge, far bigger


def clip(path, n_trees, tree_y=(380, 520)):
    import numpy as np
    import cv2

    rng = np.random.default_rng(5)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    trees = [(int(rng.uniform(40, W - 40)),
              int(rng.uniform(*tree_y))) for _ in range(n_trees)]
    truth = []
    for f in range(N):
        im = np.full((H, W, 3), 115, dtype="uint8")
        # Every "tree" moves a couple of pixels a frame: big blobs, and
        # there are many more of them than the cut can keep.
        for i, (tx, ty) in enumerate(trees):
            dx = 2 if (f + i) % 2 else -2
            cv2.circle(im, (tx + dx, ty), TREE_R, (70, 90, 60), -1)
        y = Y0 + VY * f
        if 5 < y < H - 5:
            cv2.circle(im, (BX, int(y)), BALL_R, (248, 248, 248), -1)
            truth.append((f, BX, int(y)))
        vw.write(im)
    vw.release()
    return truth


def ball_seen(path, per_frame, prefer_area, truth):
    dets = d3.detect_movers_by_diff(path, 0, N - 1, sens=3,
                                    per_frame=per_frame, three_frame=True,
                                    prefer_area=prefer_area)
    by_f = {}
    for d in dets:
        by_f.setdefault(d["frame"], []).append(d)
    return sum(1 for f, x, y in truth
               if any(abs(d["x"] - x) <= 10 and abs(d["y"] - y) <= 10
                      for d in by_f.get(f, [])))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        # ---- a quiet band: largest-first is fine ---------------------
        quiet = Path(td) / "quiet.mp4"
        truth = clip(quiet, 2)
        check("with nothing to compete, largest-first keeps the ball",
              ball_seen(quiet, 10, 0, truth) >= len(truth) - 2, True)

        # ---- a treeline: largest-first loses it ---------------------
        # MORE TREES THAN ANY CAP, deliberately. With fewer blobs than
        # the cap there is no cut happening and the comparison below
        # measures nothing — which is how the first version of this
        # test "proved" that raising the cap works.
        busy = Path(td) / "busy.mp4"
        truth = clip(busy, 70)
        _big = ball_seen(busy, 10, 0, truth)
        check("with a treeline, largest-first throws the ball away",
              _big <= len(truth) // 3, True)

        # THE POINT: ranking by ball-likeness gets it back, at the SAME
        # cap. Nothing extra is admitted; only the order changes.
        #
        # 60, not 30: the ball measures ~101px of blob here, not the
        # ~28 of its drawn circle, because the three-frame AND and the
        # dilate both inflate it. Anything of that order works — the
        # ranking only needs to put the ball nearer the target than a
        # 300px tree edge is.
        _near = ball_seen(busy, 10, 60, truth)
        check("  ranking by the ball's own size keeps it",
              _near >= len(truth) * 0.75, True)
        check("  at the same per-frame cap (control)", _big < _near, True)

        # ---- and raising the cap is NOT the fix ---------------------
        # It admits more noise ABOVE the ball rather than the ball. Four
        # times the cap, still ranking by size, against ranking by
        # likeness at the original cap.
        check("four times the cap, ranked by size, still loses it",
              ball_seen(busy, 40, 0, truth) < _near, True)

        # ---- 0 is the old behaviour exactly -------------------------
        check("prefer_area=0 is largest-first (control)",
              ball_seen(busy, 10, 0, truth), _big)

        # ---- END TO END, through the sweep ---------------------------
        # A mutation that resolved the gate and then passed 0 to the
        # detector survived every check above, because all of them read
        # the report. The third time that hole has appeared; the only
        # proof is a sweep that finds the ball with it and not without.
        def chain_near_ball(out):
            best = 0
            for z in out.get("considered") or []:
                pts = z.get("points") or []
                if pts and all(abs(q["x"] - BX) < 25 for q in pts):
                    best = max(best, len(pts))
            return best

        # TREES INSIDE THE BAND. The fixture above puts them below it,
        # where the sweep never looks — which is correct behaviour and
        # tests nothing: a clean band needs no ranking at all, and both
        # settings returned the identical 13 detections.
        inband = Path(td) / "inband.mp4"
        clip(inband, 70, tree_y=(120, 300))
        _off = d3.sweep_ascents(inband, 60.0, roi={"y": 0.95},
                                gates={"ascent_prefer_area": 0,
                                       "ascent_per_frame": 10})
        _on = d3.sweep_ascents(inband, 60.0, roi={"y": 0.95},
                               gates={"ascent_prefer_area": 60,
                                      "ascent_per_frame": 10})
        check("the sweep builds a longer chain on the ball with it on",
              chain_near_ball(_on) > chain_near_ball(_off), True)
        check("  and reports the value it ran with",
              (_off.get("prefer_area"), _on.get("prefer_area")), (0.0, 60.0))

    # ---- and it is a gate, defaulting OFF ---------------------------
    # Default 0 so no hole changes behaviour until somebody sets it
    # from that hole's own footage.
    check("the target area is tunable and off by default",
          d3.GATE_DEFAULTS["ascent_prefer_area"], 0)
    check("  and can be set per hole",
          d3.resolve_gates({"ascent_prefer_area": 30})["ascent_prefer_area"],
          30)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
