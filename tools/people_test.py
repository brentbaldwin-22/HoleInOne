"""The person detector's geometry, without a model or a GPU.

WHY THIS EXISTS. backend/app/services/people.py exists because three
attempts to read a golfer's feet off a motion blob all failed, for a
reason no statistic fixes: a golfer at address is not moving their
legs. Its own geometry -- which windows cover the region, how a
window's boxes map back into frame pixels, which duplicates are
suppressed -- is arithmetic, and arithmetic should be tested rather
than squinted at in a screenshot.

Nothing here loads the ONNX. The model's output is the one part that
cannot be checked without it, and the part that has never been
wrong; everything around it has.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.people import (                       # noqa: E402
    MAX_PERSON_WIDTH_OVER_HEIGHT, WINDOW_STEP_FRAC, _covered, _windows,
)

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok' if ok else 'FAIL'}    {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def main() -> int:
    # ---- the windows ----------------------------------------------
    # A REGION ALREADY SQUARISH IS ONE WINDOW. Splitting it would
    # cost three forward passes to see what one pass already sees.
    check("a square region is one window", _windows(600, 600), [(0, 600)])
    check("  and so is a tall one", _windows(400, 900), [(0, 400)])
    # A WIDE BAND IS COVERED EDGE TO EDGE. The last window is pinned
    # to the right edge, so nothing is left unlooked-at.
    ws = _windows(1920, 600)
    check("a wide band is split", len(ws) > 1, True)
    check("  starting at the left edge", ws[0][0], 0)
    check("  and reaching the right edge", ws[-1][0] + ws[-1][1], 1920)
    check("  in windows as wide as the band is tall",
          all(side == 600 for _, side in ws), True)
    # OVERLAP IS THE POINT: a golfer on a seam has to be whole in the
    # next window along, so the step is less than a full window.
    step = ws[1][0] - ws[0][0]
    check("  that overlap", step < ws[0][1], True)
    check("  by the stated fraction", step, int(600 * WINDOW_STEP_FRAC))
    # EVERY COLUMN IS SEEN BY SOMETHING.
    seen = set()
    for x, side in ws:
        seen.update(range(x, x + side))
    check("  covering every column", len(seen), 1920)

    # ---- duplicate suppression ------------------------------------
    # The 8 Nov case: overlapping windows read one golfer twice, a
    # full body in one and shoulders-down in the other. The small box
    # is 70%+ inside the large one but scores under the 0.5 IoU that
    # NMS acts on, so both survived and reached the picker as two
    # golfers a few pixels apart.
    big = [530, 284, 139, 422]
    inner = [535, 330, 64, 382]
    # 0.98, not 1.00: the inner box pokes a few pixels below the
    # outer one. Still far over the 0.70 the suppressor acts on,
    # which is why that bar is well clear of 1.0.
    check("a nested box is mostly covered", _covered(inner, big) > 0.70,
          True)
    check("  while the big one is not covered by it",
          _covered(big, inner) < 0.70, True)
    # TWO REAL GOLFERS STANDING CLOSE ARE NOT DUPLICATES.
    a = [530, 284, 139, 422]
    b = [700, 290, 130, 410]
    check("two separate players are not suppressed",
          _covered(b, a) < 0.70, True)
    # Touching but distinct: shoulders overlapping, bodies apart.
    c = [640, 284, 139, 422]
    check("  nor are two who overlap at the shoulder",
          _covered(c, a) < 0.70, True)

    # ---- the shape bound ------------------------------------------
    # A sanity bound, not a pose classifier: every pose anybody
    # adopts on a tee has to pass.
    for name, bw, bh, want in (
        ("standing", 90, 400, True),
        ("bent double over a tee peg", 150, 160, True),
        ("mid-follow-through", 200, 330, True),
        ("a bunker edge", 600, 120, False),
        ("a tree canopy", 400, 150, False),
    ):
        check("%s passes the shape bound" % name,
              (bw / bh) <= MAX_PERSON_WIDTH_OVER_HEIGHT, want)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
