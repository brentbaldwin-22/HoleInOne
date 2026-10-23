#!/usr/bin/env python3
"""Does the ascent sweep keep its chain on the ball's own line?

THE TRACKER'S GATE IS A RADIUS, and a radius is equally happy in every
direction. That is right while a track is being acquired and wrong once
it has a heading: a struck ball does not turn. A speck 15px off to the
side, nearer the prediction than the real next point, gets taken — and
then the velocity estimate is wrong, every prediction after it is aimed
at nothing, and the chain dies a few frames later.

The descent search has passed a corridor since a ball walked into a
bush and jumped 355px across the picture. The ascent sweep passed none,
which also turned OFF the ordering that depends on it: established
tracks then pick by LENGTH, handing an ambiguous detection to whatever
has been running longest rather than to whatever is actually
predicting.

Measured on #768 (19 Oct): the detector offers the flight on ~20 frames
and the chain keeps 4, its own points unevenly spaced — the signature
of a link taken off the line early.

    python3 tools/ascent_corridor_test.py
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


BX, BY, VX, VY, F0, NF = 1360, 470, 3.0, -22.0, 20, 26


def flight():
    """A ball, detected on every frame of its climb."""
    return [{"frame": F0 + i, "x": int(BX + VX * i), "y": int(BY + VY * i),
             "area": 100} for i in range(NF)]


def wanderer(off_px=22):
    """A LONG-RUNNING JITTERER beside the flight — a leaf edge lit by
    camera shake, which is what the band is full of.

    It is the ORDERING this exists to test, not the radius. Established
    tracks associate first, and without a corridor that order is by
    LENGTH: this thing has been running since long before the ball
    entered the band, so it picks first, and its gate (a radius, equally
    happy in every direction) reaches the ball's detections and takes
    them. The ball's own track is then starved of its own points.

    With a corridor, order is by PREDICTABILITY: a chain that has run
    straight has an axis and picks before one that wanders. This thing
    never establishes an axis, so it stops outranking the flight.
    """
    out = []
    for f in range(F0 - 40, F0 + NF):
        i = f - F0
        # Hugs the flight's path, a couple of pixels of jitter a frame.
        _x = BX + VX * max(0, i) + off_px + (3 if f % 2 else -3)
        _y = BY + VY * max(0, i) + (3 if f % 3 else -3)
        out.append({"frame": f, "x": int(_x), "y": int(_y), "area": 100})
    return out


def ball_chain(tracks):
    """Longest chain whose points all lie on the ball's own line."""
    best = 0
    for t in tracks:
        pts = t["points"]
        if pts and all(abs(q["x"] - (BX + VX * (q["frame"] - F0))) <= 8
                       for q in pts):
            best = max(best, len(pts))
    return best


def build(dets, corridor):
    return d3.build_tracks(dets, 14.0, min_len=4, max_gap=15,
                           max_span=60, corridor_px=corridor)


def main() -> int:
    # ---- a clean flight needs no help --------------------------------
    clean = flight()
    check("a clean flight chains end to end without a corridor",
          ball_chain(build(clean, 0)), NF)
    check("  and with one (control)", ball_chain(build(clean, 10.0)), NF)

    # ---- specks beside the line: the radius takes them ----------------
    dets = sorted(clean + wanderer(), key=lambda d: d["frame"])
    _no = ball_chain(build(dets, 0))
    _yes = ball_chain(build(dets, 10.0))
    check("a long jitterer beside the flight steals its detections",
          _no < NF, True)
    check("  a corridor gives the ball its own points back", _yes, NF)
    check("  which is strictly better (control)", _yes > _no, True)

    # AND IT IS THE ORDERING, NOT THE WIDTH, that rescues this one —
    # the first version of this test asserted the opposite and was
    # wrong. Any corridor at all switches established tracks from
    # picking by LENGTH to picking by PREDICTABILITY, and the jitterer
    # never establishes an axis to be predictable with. Four times the
    # width works just as well here.
    check("any corridor is enough, because the fix here is the ordering",
          ball_chain(build(dets, 80.0)), NF)

    # WHAT THE WIDTH DOES, tested on its own: refuse a detection far
    # ACROSS the line when the ball is missing for a frame or two and
    # the prediction has nothing better to take.
    _holed = [d for d in clean if not (10 <= d["frame"] - F0 <= 12)]
    _decoy = {"frame": F0 + 11,
              "x": int(BX + VX * 11 + 45), "y": int(BY + VY * 11),
              "area": 100}
    _gapped = sorted(_holed + [_decoy], key=lambda d: d["frame"])
    check("a narrow corridor refuses a decoy 45px off the line",
          ball_chain(build(_gapped, 10.0)) >= NF - 3, True)
    check("  a wide one takes it and loses the chain (control)",
          ball_chain(build(_gapped, 200.0)) < NF - 3, True)

    # ---- AND THE SWEEP ACTUALLY PASSES IT ----------------------------
    # FOUR gates in a row have now been added where the unit test
    # passed and a mutation that resolved the value, then handed the
    # detector something else, survived. Reading the number back out of
    # the report proves only that it was resolved.
    #
    # A spy on the call, rather than a fixture tuned until the output
    # happens to differ: what is being tested is wiring, and a fixture
    # that has to be coaxed into showing a difference is testing the
    # fixture. (The first attempt here built a video whose jitterer sat
    # still while the ball climbed away from it, so nothing was stolen
    # and both runs returned the identical 33-point chain.)
    import tempfile

    import cv2  # type: ignore
    import numpy as np

    seen: list = []
    _real = d3.build_tracks

    def _spy(*a, **kw):
        seen.append(kw.get("corridor_px"))
        return _real(*a, **kw)

    W, H, N = 640, 360, 20
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "w.mp4"
        vw = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"),
                             60.0, (W, H))
        for f in range(N):
            im = np.full((H, W, 3), 118, dtype="uint8")
            cv2.circle(im, (300, 300 - 8 * f), 4, (245, 245, 245), -1)
            vw.write(im)
        vw.release()
        d3.build_tracks = _spy
        try:
            for want in (0.0, 10.0, 25.0):
                d3.sweep_ascents(clip, 60.0, roi={"y": 0.95},
                                 gates={"ascent_corridor_px": want})
        finally:
            d3.build_tracks = _real
    check("the gate's value reaches build_tracks, not just the report",
          seen, [0.0, 10.0, 25.0])

    # ---- and the sweep passes one -----------------------------------
    check("the corridor is a gate", d3.GATE_DEFAULTS["ascent_corridor_px"],
          d3.ASCENT_CORRIDOR_PX)
    check("  matching the descent's measured value",
          d3.ASCENT_CORRIDOR_PX, d3.DESCENT_CORRIDOR_PX)
    check("  and can be turned off per hole",
          d3.resolve_gates({"ascent_corridor_px": 0})["ascent_corridor_px"],
          0.0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
