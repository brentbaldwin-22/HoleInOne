#!/usr/bin/env python3
"""A coned hole that actually finds a chain must not die on the report.

THE CRASH THIS PREVENTS took the whole sweep down:

    _coned = _cone_is_set(...)
    if _coned:
        ...                       # cone is the gate
    else:
        _tilt = ...               # ONLY assigned here
        if _tilt > ASCENT_MAX_TILT_DEG:
            ...
    ...
    "tilt_deg": round(_tilt, 1),  # read ALWAYS

`tilt_deg` goes into every chain entry for the report, and it was
computed only on the branch where tilt is also the gate. So on a hole
that had set a cone, the first chain to reach the entry raised
UnboundLocalError -- and the `except` around the whole sweep turned
that into "sweep failed" with no detections, no chains and no
pictures. An entire search lost to a report field.

It stayed hidden because a coned hole that found NOTHING never got far
enough to crash. It read as a quiet, working search right up to the
moment it worked, and then it vanished -- reported as "i changed to 1
and the picture went away entirely".

So the test is: a cone, and a chain, together.

    python3 tools/cone_chain_entry_test.py
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


W, H = 960, 720
SKY = 0.70          # band floor low enough to leave a usable band


def clip(p, n=90):
    """A ball climbing steeply through the band, plus enough speckle
    that the tracker has something to chain."""
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 60.0,
                         (W, H))
    rng = np.random.default_rng(11)
    for i in range(n):
        fr = np.full((H, W, 3), 40, dtype=np.uint8)
        for _ in range(12):
            x = int(rng.integers(0, W))
            y = int(rng.integers(0, int(H * SKY)))
            cv2.circle(fr, (x, y), 2, (190, 190, 190), -1)
        if 10 <= i < 70:
            t = (i - 10) * 7.0
            r = math.radians(20.0)
            x = int(400 + math.sin(r) * t)
            y = int(H * SKY - 10 - math.cos(r) * t)
            if 0 <= x < W and 0 <= y < H:
                cv2.circle(fr, (x, y), 3, (255, 255, 255), -1)
        vw.write(fr)
    vw.release()


def run(cone):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.mp4"
        clip(p)
        g = {"ascent_min_rise_frac": 0.05, "ascent_min_points": 4}
        if cone:
            g["ascent_cone_from_deg"], g["ascent_cone_to_deg"] = cone
        return d3.sweep_ascents(p, 60.0, roi={"y": SKY},
                                gates=g) or {}


print("with NO cone (tilt is the gate) chains are weighed:")
off = run(None)
check("the sweep survived", off.get("ok"))
check("  it weighed chains", len(off.get("considered") or []) > 0)
check("  and every entry carries tilt_deg",
      all("tilt_deg" in z for z in (off.get("considered") or [])))

print()
print("with a cone set (the cone is the gate) it must still survive:")
on = run((-40.0, 40.0))
check("the sweep survived", on.get("ok"))
check("  no 'sweep failed' reason",
      "sweep failed" not in str(on.get("reason") or ""))
check("  it weighed chains", len(on.get("considered") or []) > 0)
# THE FIELD THAT CRASHED IT. Measured on both branches now, because
# the report reads it on both.
check("  and every entry STILL carries tilt_deg",
      all("tilt_deg" in z for z in (on.get("considered") or [])))
check("  tilt_deg is a real measurement, not a placeholder",
      all(isinstance(z.get("tilt_deg"), (int, float))
          for z in (on.get("considered") or [])))

print()
print("and the cone is what refuses, not tilt:")
# A cone pointing away from the flight must refuse it BY THE CONE.
away = run((60.0, 80.0))
check("the sweep survived", away.get("ok"))
_whys = [w for z in (away.get("considered") or []) for w in (z.get("why") or [])]
check("  something was refused by the cone", "cone" in _whys)
check("  and nothing by tilt, which a cone replaces",
      "tilt" not in _whys)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — a coned hole can find a chain and live")
