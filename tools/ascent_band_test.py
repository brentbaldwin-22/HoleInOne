#!/usr/bin/env python3
"""Does the sweep say so when its band cannot pass anything?

THE ARITHMETIC NOBODY CHECKS. The search band is the tee line less a
golfer's height; the rise gate is a fraction of the WHOLE frame. Two
numbers with no relation to each other, so nothing stops the second
being larger than the first -- and when it is, every chain fails `rise`
however good the swing.

Seen on hole 8 (19 Oct): no hitting area was set for the day, so the
ROI fell back to the tee camera's PERSON trigger zones, whose top edge
is at a standing golfer's head (0.46 of frame) rather than on the
ground (0.79). The band code then subtracted a golfer's height from a
box that already started above the golfer -- headroom twice -- leaving
a 143px band under a 142px rise gate. The report said "1 chain, failed
rise", which reads as a tuning problem, and an hour went into the swing
before anyone measured the box.

    python3 tools/ascent_band_test.py
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


# THE REAL FORMULA, not a copy of it. This test asserts that a band is
# too thin for its gate, which says nothing at all if the band it
# measures is one the test worked out for itself.
def band_for(roi_y_frac: float, frame_h: int = 1080) -> tuple[int, float]:
    return d3._ascent_band(frame_h, {"y": roi_y_frac})


def main() -> int:
    H = 1080
    RISE = d3.ASCENT_MIN_RISE_FRAC

    # ---- hole 8, the real numbers -----------------------------------
    band, tee = band_for(0.463, H)
    check("a person-box ROI gives the band that was measured off the "
          "overlay", band, 143)
    warn = d3._band_too_thin(band, H, RISE, tee)
    check("  and the sweep calls it out", warn is not None, True)
    check("  naming the band", "143px" in (warn or ""), True)
    check("  and the rise it cannot reach",
          f"{int(RISE * H)}px" in (warn or ""), True)
    check("  and the fix, which is the box and not the swing",
          "hitting area" in (warn or "").lower(), True)
    check("  without ever blaming the swing (control)",
          "swing" in (warn or "").lower()
          and "however good the swing" in (warn or ""), True)

    # ---- a hitting area drawn on the ground -------------------------
    band, tee = band_for(0.79, H)
    check("a ground ROI leaves a band worth searching", band, 496)
    check("  and nothing is said about it",
          d3._band_too_thin(band, H, RISE, tee), None)

    # ---- the boundary, from both sides ------------------------------
    # NOT `rise > band`. A chain never spans the band edge to edge: the
    # ball is first seen some way in and last seen short of the far
    # side. Hole 8's band was 143px under a 142.56px gate -- not
    # mathematically impossible, and a strict comparison would have sat
    # silent through the one run that needed it. The bar is the share of
    # the band a real chain can reach.
    need = RISE * H
    _just_enough = int(need / d3._BAND_USABLE_FRAC) + 1
    check("a band a real chain could use says nothing",
          d3._band_too_thin(_just_enough, H, RISE, 900.0), None)
    check("  one pixel thinner than that warns",
          d3._band_too_thin(_just_enough - 2, H, RISE, 900.0) is None,
          False)
    check("  and a band the gate exactly fills — the hole 8 case — warns",
          d3._band_too_thin(int(need) + 1, H, RISE, 900.0) is None, False)
    check("  a zero-height band is not divided by (control)",
          d3._band_too_thin(0, H, RISE, 900.0), None)

    # ---- the gate is TUNED, so the warning has to follow it ---------
    # An operator may answer this by lowering the bar instead of
    # redrawing the box. A warning fixed to the module default would
    # then keep firing on a hole that is now fine.
    check("lowering the hole's rise gate clears the warning",
          d3._band_too_thin(143, H, 0.10, 500.0), None)
    check("  and raising it brings it back (control)",
          d3._band_too_thin(143, H, 0.20, 500.0) is None, False)

    # ---- the band is a strip of SKY, not everything under the line --
    # Without the headroom the band runs down to the tee itself, and
    # the sweep spends its ten-blobs-a-frame budget on the group, the
    # club and the shadows -- which is what the strip exists to avoid.
    band, _ = band_for(0.79, H)
    check("the band stops a golfer's height above the tee",
          band, int(0.79 * H - d3.ASCENT_HEAD_ROOM_FRAC * H))
    check("  which is well clear of the tee line (control)",
          band < 0.79 * H - 100, True)
    _fb_band, _fb_tee = d3._ascent_band(H, None)
    check("no hitting area at all falls back to a usable band",
          d3._band_too_thin(_fb_band, H, RISE, _fb_tee), None)

    # ---- and it survives a frame size it was not written for --------
    band, tee = band_for(0.463, 720)
    check("the same bad box at 720p is caught too",
          d3._band_too_thin(band, 720, RISE, tee) is not None, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
