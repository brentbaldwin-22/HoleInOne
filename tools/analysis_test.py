"""The server-side analysis, end to end, without a model or a rig.

WHAT THIS IS GUARDING. The judgement used to live twice -- once in
pi-agent/agent/swingheat.py and once in tracer.swing_heat_check --
held in step by hand, and over one week the crop geometry, the frame
offsets, the foot measurement and the tile grid each drifted at least
once. swinganalysis exists so there is one copy. This checks that the
one copy works from the shape a rig actually posts, and that the two
constants still shared across the wire have not moved apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "pi-agent"))

import cv2                                            # noqa: E402
import numpy as np                                    # noqa: E402

from app.services.swinganalysis import analyse        # noqa: E402
from app.services.tracer import (                      # noqa: E402
    AUDIO2_OFFSETS_SEC, grid_of_tiles,
)

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok' if ok else 'FAIL'}    {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def scene(swing=True):
    """Six frames of a tee: two people, one of them moving."""
    W, H = 900, 420
    frames = []
    for i, off in enumerate(AUDIO2_OFFSETS_SEC):
        f = np.full((H, W, 3), (60, 90, 60), np.uint8)       # turf
        f[:150, :] = (180, 170, 160)                          # sky
        # The watcher, still, on the left.
        cv2.rectangle(f, (200, 150), (240, 330), (70, 70, 90), -1)
        # The hitter on the right; his arms swing if this is a swing.
        cv2.rectangle(f, (600, 150), (640, 330), (70, 70, 90), -1)
        if swing:
            ax = 600 + int(60 * np.sin(i))
            cv2.rectangle(f, (min(ax, 600), 170), (max(ax, 640), 200),
                          (90, 90, 120), -1)
        # A ball on the turf, gone after the strike when it is a swing.
        if not swing or i < 3:
            cv2.circle(f, (700, 332), 3, (245, 245, 245), -1)
        frames.append(f)
    heat = frames[0].copy()
    return frames, heat


def main() -> int:
    # ---- the constants that cross the wire -------------------------
    # The rig cuts the frames and the server labels them. If these
    # ever disagree the model is told the tiles are at times they are
    # not, which is the exact failure the single-copy move is for.
    from agent import swingheat                        # noqa: E402
    check("the rig and the server agree on the six offsets",
          tuple(swingheat.AUDIO2_OFFSETS_SEC), tuple(AUDIO2_OFFSETS_SEC))

    # ---- the grid --------------------------------------------------
    g = grid_of_tiles([np.zeros((50, 40, 3), np.uint8)] * 6)
    check("six tiles make a 3x2 grid", (g.shape[1], g.shape[0]), (120, 100))
    g4 = grid_of_tiles([np.zeros((50, 40, 3), np.uint8)] * 4)
    check("  and four still make 2x2", (g4.shape[1], g4.shape[0]), (80, 100))

    # ---- the pipeline, from what a rig posts -----------------------
    frames, heat = scene(swing=True)
    ZONES = [(560, 820, 700, 90)]          # frame pixels
    CROP = (400, 600, 900, 420)            # where the crop sits
    GEO = {"main": {"x": 190, "y": 140, "w": 60, "h": 200, "area": 9000,
                    "base": (220, 340), "foot": (220, 340)},
           "others": [{"x": 590, "y": 140, "w": 100, "h": 200,
                       "area": 18000, "base": (640, 340),
                       "foot": (640, 340)}]}
    import tempfile
    d = tempfile.mkdtemp()
    r = analyse(frames, heat, GEO, crop=CROP, zones=ZONES,
                offsets=AUDIO2_OFFSETS_SEC, out_dir=d, prefix="t",
                judge=False)
    check("it survives a scene with no model to ask",
          r["reason"] is None or "failed" not in str(r["reason"]), True)
    check("  and writes a heat picture", bool(r["image"]), True)
    check("  and a frames picture", bool(r["frames_image"]), True)
    check("  and the operator's copies of both",
          bool(r["image_zones"]) and bool(r["frames_image_zones"]), True)
    if r["frames_image"]:
        t = cv2.imread(str(Path(d) / r["frames_image"]))
        check("  with six tiles in it", t.shape[1] > t.shape[0], True)

    # EVERYTHING IT RETURNS IS IN ORIGINAL-FRAME PIXELS. The crop is
    # the shared space on the wire and the conversion happens once,
    # inside analyse; a caller that has to undo it has been handed a
    # bug to find later.
    for c in (r["front_candidates"] or []):
        check("a reported foot point is in frame pixels, not crop",
              c["feet"][0] >= CROP[0] and c["feet"][1] >= CROP[1], True)
        break
    if r["ball_spot"]:
        check("  and so is the ball's spot",
              r["ball_spot"][0] >= CROP[0], True)

    # ---- a frame the clip could not reach --------------------------
    short = [None] + frames[1:]
    r2 = analyse(short, heat, GEO, crop=CROP, zones=ZONES,
                 offsets=AUDIO2_OFFSETS_SEC, out_dir=d, prefix="s",
                 judge=False)
    check("a missing frame does not sink it",
          "failed" not in str(r2.get("reason") or ""), True)

    # ---- nothing at all --------------------------------------------
    r3 = analyse([], None, {}, crop=CROP, zones=ZONES, judge=False)
    check("no pictures is reported, not raised",
          r3["reason"], "no pictures to judge")
    check("  and the verdict stays unknown", r3["is_swing"], None)

    # ── THE MARKS GO ON WHATEVER THERE IS TO MARK ───────────────────
    #
    # `draw_zones` refused outright whenever `zones` was empty, which
    # made the ball marker and the foot line unreachable for a caller
    # that has a measured ball and no trigger zones -- the produce
    # report's six-frame strip being exactly that, and the one
    # picture where the ball's presence frame by frame IS the
    # finding. It drew nothing and said nothing: the picture came out
    # looking right, just without the ring.
    #
    # Two failures in one line, and the second only appeared after
    # the first was fixed: relaxing the guard left the zone loop
    # iterating None, which raises into the outer except, is
    # swallowed, and returns None exactly as before.
    from app.services.tracer import draw_zones          # noqa: E402

    _img = np.full((200, 200, 3), 60, np.uint8)
    _out = draw_zones(_img, None, ball={"spot": [100, 100]})
    check("a ball with no zones is still drawn", _out is not None, True)
    check("  and something really changed in the pixels",
          bool((_out != _img).any()) if _out is not None else False, True)
    _z = draw_zones(_img, [(10, 10, 50, 50)], ball={"spot": [100, 100]})
    check("zones and a ball together still work", _z is not None, True)
    check("nothing to draw is still nothing",
          draw_zones(_img, None), None)
    check("  including an empty list and no ball",
          draw_zones(_img, [], ball=None, feet=None), None)
    # A BALL OUTSIDE THE CROP IS NOT A DRAWING. Same rule as the
    # zones: "nothing could be drawn" and "here it is" have to be
    # distinguishable by the caller.
    check("a ball far outside the crop draws nothing",
          draw_zones(_img, None, ball={"spot": [9000, 9000]}), None)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
