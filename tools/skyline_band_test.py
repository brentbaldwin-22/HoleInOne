#!/usr/bin/env python3
"""Does the ascent band stop where somebody drew the line?

THE FLOOR IS THE WHOLE BALLGAME. The sweep keeps ten blobs a frame,
largest first, over the full width of the band. Everything inside it
competes: a tree edge lit by a pixel of camera shake is a far bigger
blob than a ball at 180 yards.

The floor used to be computed — "a golfer's height above the hitting
area" — which is a PROXY for "above the noise", measured off one
reference frame where the hitting area sat at 0.79 of frame height. On
Rivertowne 8 (19 Oct) the proxy lands 150px inside the treeline. A
flight that a scan of the same footage found on nearly every frame came
through the sweep as FOUR points, density 0.5, dying at f1558 with
eight in-band frames still to go.

So the floor is now a line a person drags onto a real frame. This
covers the rule, not the drawing: what the band is with a skyline, what
it is without one, and the two ways a bad line could quietly ruin a
search instead of being refused.

    python3 tools/skyline_band_test.py
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


H = 1080
GROUND = {"y": 0.79}          # a hitting area drawn on the grass


def main() -> int:
    # ---- the fallback is untouched -----------------------------------
    # A camera nobody has drawn a line on must behave exactly as it did,
    # or this change is a silent re-tune of every other hole.
    check("no skyline: the computed guess still answers",
          d3._ascent_band(H, GROUND)[0],
          int(0.79 * H - d3.ASCENT_HEAD_ROOM_FRAC * H))
    check("  and None is the same as not passing it",
          d3._ascent_band(H, GROUND, None), d3._ascent_band(H, GROUND))

    # ---- a drawn line wins -------------------------------------------
    check("a skyline sets the floor outright", d3._ascent_band(H, GROUND, 0.30)[0],
          int(0.30 * H))
    check("  it does not move the tee line with it",
          round(d3._ascent_band(H, GROUND, 0.30)[1], 1),
          round(d3._ascent_band(H, GROUND)[1], 1))
    # THE TEE LINE IS NOT THE BAND. The backrun gate and the hitting-area
    # gate both measure against it, so a skyline that dragged it would
    # change what "pointed back outside the hitting area" means.

    # ---- the Rivertowne 8 numbers, which is the point of all this ----
    # The proxy put the floor at 496 of 1080 — inside the trees. A line
    # drawn just above them is around 0.30.
    check("the proxy searches down into the treeline",
          d3._ascent_band(H, GROUND)[0] > int(0.30 * H), True)
    check("  and the drawn line cuts that by a third",
          int(0.30 * H) < d3._ascent_band(H, GROUND)[0], True)

    # ---- two ways a bad line could ruin a search quietly -------------
    # Below the tee: the band would carry the golfer, the club and the
    # shadows, which is the one thing it exists to exclude.
    _b, _tee = d3._ascent_band(H, {"y": 0.50}, 0.90)
    check("a skyline below the tee line is clamped to it", _b, int(_tee))
    # Out of range: treated as absent rather than as 0, because a band
    # of zero height finds nothing and says nothing about why.
    for bad in (0.0, 1.0, -0.2, 1.5):
        check(f"  {bad} is ignored, not obeyed",
              d3._ascent_band(H, GROUND, bad), d3._ascent_band(H, GROUND))

    # ---- and the floor still cannot go under the minimum -------------
    check("a line at the very top still leaves a searchable band",
          d3._ascent_band(H, GROUND, 0.01)[0], 40)

    # ---- the thin-band guard still fires on the drawn line ------------
    # A skyline is the likeliest new way to make the band too thin for
    # the rise gate, so the two have to see the same number.
    _band, _tee = d3._ascent_band(H, GROUND, 0.10)
    check("a skyline drawn too low leaves a band the rise gate can't use",
          d3._band_too_thin(_band, H, d3.ASCENT_MIN_RISE_FRAC, _tee)
          is None, False)
    _band, _tee = d3._ascent_band(H, GROUND, 0.30)
    check("  and a sensible one says nothing (control)",
          d3._band_too_thin(_band, H, d3.ASCENT_MIN_RISE_FRAC, _tee), None)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
