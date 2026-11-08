#!/usr/bin/env python3
"""The green finder shows its work, and the gates still bite.

WHY THIS EXISTS. "1 arrival(s), 0 departure(s)" is a result, and a
result is not something anybody can make more accurate. The finder now
reports a funnel -- components found, ball-shaped, persistent, kept --
and every candidate it turned down with the gate it died on. The point
of the test is that those numbers are MEASURED rather than narrated:
a reject row has to appear for a thing that really was rejected, and
for the real reason.

Four shapes are planted in a synthetic after-plate, one for each gate:

    a ball       2-8px, round, bright       -> kept
    a speck      1px                        -> too small
    a shirt      30x30                       -> too big
    a mow stripe 60x3                        -> not round
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    from app.services.greenrest import _blobs, MAX_ASPECT

    H, W = 240, 320
    before = np.full((H, W), 80, np.uint8)
    after = before.copy()
    # a ball: 3x3 of bright, area 9, aspect 1.0
    after[100:103, 100:103] = 200
    # a speck: one pixel, area 1 -- under MIN_AREA of 2
    after[40, 40] = 200
    # a shirt: 30x30, area 900 -- over MAX_AREA of 400
    after[150:180, 150:180] = 200
    # a mowing stripe: 60 long, 3 high -- 20:1, well over MAX_ASPECT
    after[200:203, 20:80] = 200

    diff = after.astype(np.int16) - before.astype(np.int16)
    pos = (diff >= 18).astype(np.uint8) * 255
    mask = np.full((H, W), 255, np.uint8)

    rejects: list = []
    counts: dict = {}
    kept = _blobs(pos, diff, mask, rejects, counts)

    ok("all four shapes are seen as components", counts["components"], 4)
    ok("one of them is ball-shaped", counts["ball_shaped"], 1)
    ok("  and it is the ball, at its centre",
       (kept[0]["x"], kept[0]["y"]), (101, 101))
    ok("  with its real area", kept[0]["area"], 9)

    ok("the speck is turned down for size", counts.get("too_small"), 1)
    ok("the shirt is turned down for size", counts.get("too_big"), 1)
    ok("the stripe is turned down for shape", counts.get("not_round"), 1)
    ok("three rejects come back with reasons", len(rejects), 3)

    _by = {r["why"].split(",")[0].split(" —")[0]: r for r in rejects}
    ok("  and a reason names the measurement and the limit",
       any("1px" in r["why"] and "2px" in r["why"] for r in rejects), True)
    ok("  the stripe's reason names its aspect",
       any(str(MAX_ASPECT) in r["why"] and "20" in r["why"]
           for r in rejects), True)
    ok("  every reject carries a position to draw",
       all(("x" in r and "y" in r) for r in rejects), True)

    # ── AND NOTHING IS COLLECTED WHEN NOBODY ASKS ───────────────────
    # The produce path calls this for the answer alone, on every
    # candidate of every swing. It should not pay for the bookkeeping.
    kept2 = _blobs(pos, diff, mask)
    ok("the answer is the same without the bookkeeping",
       len(kept2), len(kept))

    # ── A BALL-SHAPED THING THAT IS NOT BRIGHT ENOUGH ───────────────
    # The first gate is the threshold, and a candidate that never
    # crosses it is not a reject -- it is never a component at all.
    # That is the failure the funnel has to distinguish, because the
    # fix is the exposure match, not a gate.
    dim = before.copy()
    dim[100:103, 100:103] = 90  # 10 levels, under the threshold of 18
    diff2 = dim.astype(np.int16) - before.astype(np.int16)
    pos2 = (diff2 >= 18).astype(np.uint8) * 255
    c2: dict = {}
    r2: list = []
    _blobs(pos2, diff2, mask, r2, c2)
    ok("a candidate under the threshold is not a component",
       c2.get("components", 0), 0)
    ok("  and so is not reported as a reject", len(r2), 0)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
