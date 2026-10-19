#!/usr/bin/env python3
"""Does the sweep say when its per-frame cap is what lost the ball?

THE QUESTION THAT KEPT BEING ASKED BY HAND. detect_movers_by_diff keeps
the biggest `per_frame` blobs and drops the rest. That is the right
order — inside a ball-sized window the ball is the big one — but it is
extremal, and a band with a treeline in it has plenty of things bigger
than a ball at 180 yards.

When the cap binds on most frames, a missing ball is not being refused
by any of the eight chain gates: it is never emitted, and nothing in
the gate editor can reach it. Those two cases produce the SAME report —
"0 ball(s) seen leaving" — and need opposite work, which is how
Rivertowne 8 cost an afternoon of gate theories.

So the detector counts it and the sweep says so.

    python3 tools/sweep_cap_test.py
"""
from __future__ import annotations

import sys
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


def synth(path, frames, blobs_per_frame, size=6):
    """A clip where N things move every frame, so the cap is a number we
    chose rather than one we hope for."""
    import numpy as np
    import cv2

    W, H = 640, 360
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         30.0, (W, H))
    for f in range(frames):
        im = np.zeros((H, W, 3), dtype="uint8")
        for b in range(blobs_per_frame):
            # Each blob moves every frame, so every one is a detection.
            x = 20 + (b * 37 + f * 5) % (W - 40)
            y = 20 + (b * 23) % (H - 40)
            cv2.rectangle(im, (x, y), (x + size, y + size),
                          (255, 255, 255), -1)
        vw.write(im)
    vw.release()


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        quiet = Path(td) / "quiet.mp4"
        busy = Path(td) / "busy.mp4"
        synth(quiet, 40, 3)
        synth(busy, 40, 25)

        # ---- the cap does not bind on a quiet band ------------------
        st = {}
        d3.detect_movers_by_diff(quiet, 0, 39, sens=3, per_frame=10,
                                 three_frame=True, stats=st)
        check("a quiet band never hits the cap", st.get("capped", 0), 0)
        check("  and still counts the frames it looked at",
              st.get("frames", 0) > 0, True)
        check("  reporting the worst frame it saw",
              0 < st.get("max_hits", 0) <= 10, True)

        # ---- and it does on a busy one ------------------------------
        st = {}
        d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=10,
                                 three_frame=True, stats=st)
        check("a busy band hits the cap on every frame",
              st.get("capped", 0) == st.get("frames", 0)
              and st.get("frames", 0) > 0, True)
        check("  and says how much it threw away",
              st.get("dropped", 0) > 0, True)
        check("  and how bad the worst frame was",
              st.get("max_hits", 0) > 10, True)

        # ---- raising the cap is what makes it stop binding ----------
        st = {}
        d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=40,
                                 three_frame=True, stats=st)
        check("raising the cap past the traffic stops it binding",
              st.get("capped", 0), 0)

        # ---- stats is optional; nothing breaks without it -----------
        got = d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=10,
                                       three_frame=True)
        check("the detector still works when nobody asks for stats",
              len(got) > 0, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
