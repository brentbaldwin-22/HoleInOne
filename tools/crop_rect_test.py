#!/usr/bin/env python3
"""A golfer outside the crop must not cost the verdict.

THE FIELD FAILURE, #1467, four audio triggers, all four lost:

    JUDGE: COULD NOT TELL — ANALYSIS FAILED: OpenCV(5.0.0)
    .../loadsave.cpp:1174: error: (-215:Assertion failed)
    !_img.empty() in function 'imwrite' @ 4.63s

THE CHAIN. People are detected in the WHOLE frame and their boxes are
then shifted into crop pixels, so a person standing outside the crop
arrives at `_crop_rect` with coordinates off either end. There,
`x0 = max(0, x - pad)` lands at or past W while `x1 = min(W, ...)` is
W -- or `bottom` lands at or below zero -- and the rect is empty.

An empty rect makes an empty crop. `cv2.imwrite` does not return False
for an empty image, it RAISES; the call sat inside analyse()'s outer
catch, so the strike came back "could not tell" with an OpenCV
assertion where the reason should be. The line immediately after the
crop already guarded for exactly this (`min(judged.shape[:2]) > 0`),
so the shape was known to be reachable -- just not followed through to
the write.

    python3 tools/crop_rect_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    import numpy as np
    from app.services.swinganalysis import _crop_rect, _usable, _write

    W, H = 640, 480

    def _crops(rect):
        """Does slicing an image with this rect give anything?"""
        x0, y0, x1, y1 = rect
        im = np.zeros((H, W, 3), np.uint8)
        return im[y0:y1, x0:x1].size > 0

    # ── THE ORDINARY CASE IS UNCHANGED ──────────────────────────────
    g = {"x": 260, "y": 150, "w": 90, "h": 220}
    _h, _f = _crop_rect(g, 370, [{"y": 150}], (W, H))
    ok("a golfer in the middle crops normally", (_crops(_h), _crops(_f)),
       (True, True))
    ok("  the heat runs to the top of the picture", _h[1], 0)
    ok("  and both stay inside the frame",
       (_h[2] <= W and _h[3] <= H and _f[2] <= W and _f[3] <= H), True)

    # ── AND EVERY WAY A BOX CAN LEAVE THE PICTURE ───────────────────
    #
    # These are the cases that raised. Each one must still produce a
    # rect something can be cropped with.
    for _label, _g, _floor in (
            ("off the right edge", {"x": 700, "y": 150, "w": 90, "h": 220},
             370),
            ("off the left edge", {"x": -300, "y": 150, "w": 90, "h": 220},
             370),
            ("above the picture", {"x": 260, "y": -400, "w": 90, "h": 220},
             None),
            ("below the picture", {"x": 260, "y": 900, "w": 90, "h": 220},
             None),
            ("zero width", {"x": 260, "y": 150, "w": 0, "h": 220}, 370),
            ("zero height", {"x": 260, "y": 150, "w": 90, "h": 0}, 370),
            ("far off in both", {"x": -900, "y": -900, "w": 10, "h": 10},
             None)):
        _hr, _fr = _crop_rect(_g, _floor, [{"y": _g["y"]}], (W, H))
        ok("a golfer %s still gives a usable crop" % _label,
           (_crops(_hr), _crops(_fr)), (True, True))

    # ── THE FALLBACK IS THE WHOLE PICTURE, NOT A SLIVER ─────────────
    #
    # A two-pixel crop is worse than useless: there is nothing for a
    # model to look at and nothing for a detector to find. The crop
    # exists to put the golfer in the middle, and failing that,
    # judging the whole frame is what the no-golfer branch already
    # does.
    ok("a rect with no overlap falls back to the whole picture",
       _usable(900, 900, 910, 910, W, H), (0, 0, W, H))
    ok("  as does an inverted one", _usable(400, 300, 100, 100, W, H),
       (0, 0, W, H))
    ok("  and a sane one is left alone",
       _usable(10, 20, 300, 400, W, H), (10, 20, 300, 400))

    # ── AND THE WRITER NEVER RAISES ─────────────────────────────────
    #
    # cv2.imwrite returns False for an unwritable PATH and RAISES for
    # an empty IMAGE, which is the worst of both and the reason one
    # bad crop could take down a whole verdict.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ok("writing an empty image says no rather than raising",
           _write(Path(d) / "a.jpg", np.zeros((0, 0, 3), np.uint8)), False)
        ok("  as does writing nothing at all",
           _write(Path(d) / "b.jpg", None), False)
        ok("  and a real picture is written",
           _write(Path(d) / "c.jpg", np.zeros((40, 40, 3), np.uint8)), True)
        ok("    and really is on disk", (Path(d) / "c.jpg").exists(), True)
        # AN UNWRITABLE PATH IS ALSO JUST False, not an exception that
        # climbs into the verdict.
        ok("  an unwritable path is a no, not a crash",
           _write(Path(d) / "no-such-dir" / "d.jpg",
                  np.zeros((40, 40, 3), np.uint8)), False)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
