#!/usr/bin/env python3
"""The green crop, and the grid that survives it.

WHY THIS EXISTS. The model is now shown a crop of the putting surface
rather than the whole frame, and the grid over it is labelled in the
ORIGINAL frame's coordinates so the answer comes back in frame pixels
with nothing to map afterwards. That is the one place an off-by-a-crop
would not look like a bug: it would be a plausible position on the
wrong part of the green, and the snap would happily find some bright
thing near it.

So the crop geometry is checked against a frame shaped like a real
tee-to-green view -- sky, treeline, turf -- and the grid is checked by
READING IT BACK: the pixel at a known original coordinate has to be the
pixel the crop puts there.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def frame():
    """1920x1080, green across the bottom third, ball at 1689,811."""
    H, W = 1080, 1920
    f = np.full((H, W, 3), 60, np.uint8)
    f[:380] = 150        # sky
    f[380:620] = 48      # treeline
    f[620:] = 86         # turf
    cv2.circle(f, (1689, 811), 4, (240, 240, 240), -1)
    return f


GREEN = [[40, 700], [1880, 660], [1880, 980], [60, 1010]]


def main():
    from app.services.ai_tracer import (
        _crop_to_green, _with_grid, CROP_MAX_UPSCALE,
        CROP_TARGET_LONG_EDGE)

    f = frame()
    view, x0, y0, sc = _crop_to_green(f, GREEN)

    ok("the crop starts above the green's top edge", y0 < 660, True)
    ok("  and keeps the whole green's width",
       x0 <= 40 and view.shape[1] / sc + x0 >= 1880, True)
    ok("  and throws the sky away", y0 > 380, True)
    ok("  scaled up, but not past the long-edge target",
       view.shape[1] <= CROP_TARGET_LONG_EDGE, True)
    ok("  and not past the interpolation cap", sc <= CROP_MAX_UPSCALE, True)

    # THE BALL IS BIGGER IN THE PICTURE THAT GETS SENT. That is the
    # whole point: same pixels, more of the model's attention on them.
    ok("the ball is scaled up with everything else", sc > 1.0, True)

    # ── READ THE GRID BACK ──────────────────────────────────────────
    # The ball is at 1689,811 in the frame. In the crop it must be at
    # ((1689-x0)*sc, (811-y0)*sc) -- and that pixel must be bright.
    bx = int(round((1689 - x0) * sc))
    by = int(round((811 - y0) * sc))
    ok("the ball lands where the crop maths says it does",
       bool(view[by, bx].mean() > 180), True)

    # And a gridline LABELLED 1600 must be drawn at the crop pixel
    # that 1600 maps to, not at 1600.
    g = _with_grid(view, 100, origin=(x0, y0), scale=sc)
    gx = int(round((1600 - x0) * sc))
    _col = g[:, gx]
    _yellow = int(((_col[:, 0] < 90) & (_col[:, 1] > 180)
                   & (_col[:, 2] > 180)).sum())
    ok("the line labelled 1600 is drawn where 1600 maps to",
       _yellow > view.shape[0] * 0.5, True)
    # ...AND THERE ARE AS MANY LINES AS THE ORIGINAL SPAN HAS, not as
    # many as the crop's own width has. That is the discriminator: a
    # grid drawn in the crop's coordinates would put a line every 100
    # CROP pixels, which over a 2560-wide view is 25 lines where the
    # 1920px original span it covers has 19. Checking one line's
    # position cannot tell the two apart, because a crop scaled by
    # 4/3 maps plenty of its own multiples of 100 onto the original's.
    _cols = [c for c in range(g.shape[1])
             if ((g[:, c, 0] < 90) & (g[:, c, 1] > 180)
                 & (g[:, c, 2] > 180)).sum() > view.shape[0] * 0.5]
    _span = view.shape[1] / sc
    _want = len([v for v in range(100, int(x0 + _span), 100) if v > x0])
    # COUNTED AS RUNS, not as columns: a line is drawn `u` pixels wide
    # and anti-aliased, so one line is five or six adjacent columns.
    _lines = sum(1 for i, c in enumerate(_cols)
                 if i == 0 or c - _cols[i - 1] > 1)
    ok("  and there is one line per 100 ORIGINAL px, not per 100 crop px",
       _lines, _want)

    # ── THE NO-OP CASES, which must stay no-ops ─────────────────────
    v2, a2, b2, c2 = _crop_to_green(f, None)
    ok("no outline leaves the frame alone",
       (v2.shape == f.shape, a2, b2, c2), (True, 0, 0, 1.0))
    v3, a3, b3, c3 = _crop_to_green(f, [[0, 0], [1920, 0],
                                        [1920, 1080], [0, 1080]])
    ok("an outline covering the frame leaves it alone",
       (v3.shape == f.shape, a3, b3, c3), (True, 0, 0, 1.0))
    v4, a4, b4, c4 = _crop_to_green(f, [[10, 10], [14, 10], [14, 14]])
    ok("a nonsense outline leaves it alone, rather than raising",
       (v4.shape == f.shape, a4, b4, c4), (True, 0, 0, 1.0))

    # ── AND A POLYGON DRAWN AT ANOTHER RESOLUTION ───────────────────
    # The editor works on whatever the live view serves, which is
    # often not the capture's size. A crop that ignores that is a crop
    # of the wrong part of the picture.
    half = [[x // 2, y // 2] for x, y in GREEN]
    v5, x5, y5, s5 = _crop_to_green(f, half, drawn_at=[960, 540])
    ok("a polygon drawn at half size lands on the same crop",
       (abs(x5 - x0) <= 2, abs(y5 - y0) <= 2), (True, True))

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
