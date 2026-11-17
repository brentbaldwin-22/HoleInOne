#!/usr/bin/env python3
"""Searching the cone below the skyline, and only when asked.

THE BAND IS FLOORED AT THE SKYLINE because below it everything moves:
a tee full of people, a club, a divot, a shadow, carts, branches. That
floor is also why the climb from the tee INTO the band is never
observed -- it is recovered afterwards by running the chain backwards
down its own heading, which is a fit, not a measurement.

A cone changes the arithmetic. It is anchored on the ball and narrow,
so "below the skyline AND inside the cone" is a far smaller piece of
grass than "below the skyline": the golfers stand AT the apex and the
wedge opens away from them.

Two things have to hold, and the second matters more than the first.

  1. With the gate on, the wedge under the skyline is searched and
     only what is really in the wedge is kept -- the rectangle is the
     wedge's bounding box and is mostly not the wedge.

  2. WITH IT OFF, NOTHING CHANGES AT ALL. This is a new search area in
     the part of the frame the band exists to avoid, and a gate that
     leaks when off would quietly put tracers on branches for every
     hole that has ever had a cone drawn.

    python3 tools/cone_below_skyline_test.py
"""
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


W, H = 960, 540
SKY = 200          # the band's floor, well above the apex
APEX = (300, 480)  # the ball, down on the tee line


def clip(path, n=60):
    """A speck climbing up-and-right from the apex, BELOW the skyline
    the whole way -- i.e. exactly the piece of flight the band cannot
    see."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for i in range(n):
        fr = np.full((H, W, 3), 30, dtype=np.uint8)
        # a static bright treeline, so the band has something that is
        # NOT moving and the diff ignores it
        cv2.rectangle(fr, (0, SKY + 10), (W, SKY + 60), (70, 70, 70), -1)
        if 10 <= i < 40:
            t = (i - 10) * 6.0
            r = math.radians(61.0)
            x = int(APEX[0] + math.sin(r) * t)
            y = int(APEX[1] - math.cos(r) * t)
            if 0 <= x < W and SKY < y < H:
                cv2.circle(fr, (x, y), 4, (255, 255, 255), -1)
        vw.write(fr)
    vw.release()


def run(gate, apex=APEX, cone=(45.0, 77.0)):
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "c.mp4"
        clip(src)
        return d3.sweep_ascents(
            src, 60.0,
            roi={"y": float(SKY) / H},
            cone_origin=apex,
            gates={"ascent_cone_from_deg": cone[0],
                   "ascent_cone_to_deg": cone[1],
                   "ascent_cone_below_skyline": gate}) or {}


print("with the gate OFF nothing below the skyline is searched:")
off = run(0)
check("the pass did not run", (off.get("cone_below") or {}).get("ran"),
      None)
check("cone_below is reported as absent, not missing",
      "cone_below" in off)

print()
print("with the gate ON the wedge under the skyline is searched:")
on = run(1)
cb = on.get("cone_below") or {}
check("the pass ran", cb.get("ran"))
check("  it searched below the band's floor",
      cb.get("rect") and cb["rect"][1] >= 1)
check("  down to the ball",
      cb.get("rect") and cb["rect"][1] + cb["rect"][3] == APEX[1])
check("  and it found the speck",
      (cb.get("n_in_cone") or 0) > 0)
check("  which the band alone did not",
      (on.get("n_dets") or 0) > (off.get("n_dets") or 0))

print()
print("only what is really in the wedge is kept:")
check("the bounding box held at least as much as the wedge",
      (cb.get("n_found") or 0) >= (cb.get("n_in_cone") or 0))
# A cone pointing the OTHER way must keep none of it.
away = run(1, cone=(-77.0, -45.0))
cba = away.get("cone_below") or {}
check("a cone pointing the other way keeps nothing",
      cba.get("n_in_cone"), 0)
check("  even though the box still found things",
      (cba.get("n_found") or 0) > 0)

print()
print("and it declines cleanly when it cannot run:")
check("no apex -> no pass",
      (run(1, apex=None).get("cone_below") or {}).get("ran"), None)
high = run(1, apex=(300, 10))
check("a ball above the skyline -> says so",
      (high.get("cone_below") or {}).get("ran"), False)
check("  with a reason",
      "nothing below it" in ((high.get("cone_below") or {})
                             .get("why") or ""))

print()
print("the gate is off by default:")
check("default is 0", d3.GATE_DEFAULTS["ascent_cone_below_skyline"], 0)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the wedge is searched only when asked")
