#!/usr/bin/env python3
"""No golfer picked is not a reason to judge the whole golf course.

THE FIELD REPORT, off a Test cut:

    crop: the person detector — nobody's feet are in a trigger zone
    (1 blob(s) looked at)
    feet (1067,716) · 36x96 · not in a zone · seen 0s
    X not (majority) · conf low

and the picture the judge was shown is a wide course panorama with a
golfer about ninety pixels tall somewhere in it. Of course it said
not-a-swing. "I thought we were zooming in on the golfer" -- we were,
but only down the branch where somebody's feet landed in a zone.

WHY IT MATTERS NOW. With `verify_drops` on the rig, not-a-swing
DELETES the clip. A crop that is merely wrong costs an upload; no
crop at all costs the swing.

THE LADDER, best guess first:
  1. the golfer standing in a zone      -- unchanged
  2. the person nearest a zone          -- not confirmed, still a person
  3. the trigger zones themselves       -- nobody detected at all
  4. the whole frame                    -- nothing else left

    python3 tools/zoom_fallback_test.py
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


def area(rect, W, H):
    """What fraction of the picture this rect covers, as a percent."""
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    return round(100.0 * (x1 - x0) * (y1 - y0) / float(W * H), 1)


def main():
    from app.services.swinganalysis import (
        _zone_rect, _nearest_person, _crop_rect, _usable)

    # The frame from the report: a wide course picture, two tee zones
    # low and right of centre.
    W, H = 1456, 816
    ZONES = [(395, 700, 190, 40), (460, 660, 120, 30)]

    # ── RUNG 3: THE ZONES ───────────────────────────────────────────
    _z = _zone_rect(ZONES, 0, 0, W, H)
    ok("the zone crop exists", _z is not None, True)
    ok("  and is a real zoom, not the frame", area(_z, W, H) < 25.0, True)
    ok("  spanning the zones' width plus padding",
       _z[0] < 395 and _z[2] > 585, True)
    ok("  running to the top of the picture, where the ball goes",
       _z[1], 0)
    ok("  and stopping just under the zones", _z[3] > 740 and _z[3] < H,
       True)
    ok("  staying inside the frame",
       _z[0] >= 0 and _z[2] <= W and _z[3] <= H, True)
    print("     zone crop covers %.1f%% of the frame" % area(_z, W, H))

    # No zones drawn at all: there is nothing to fall back to.
    ok("no zones means no zone crop", _zone_rect([], 0, 0, W, H), None)
    ok("  and junk in the zones does not raise",
       _zone_rect([("x", None, 3, 4)], 0, 0, W, H), None)

    # ── RUNG 2: THE NEAREST PERSON ──────────────────────────────────
    #
    # The exact person from the report: feet (1067,716), 36x96, well
    # right of both zones.
    _p = {"box": [1049, 620, 36, 96], "feet": [1067, 716]}
    ok("a lone person is picked however far from a zone",
       _nearest_person([_p], ZONES), _p)

    _near = {"box": [480, 610, 40, 100], "feet": [500, 710]}
    ok("  and the one nearest a zone wins",
       _nearest_person([_p, _near], ZONES), _near)
    ok("  whichever order they arrive in",
       _nearest_person([_near, _p], ZONES), _near)

    # With no zones to measure against, the lowest feet are the best
    # guess at whoever is closest to the camera.
    _high = {"box": [200, 300, 20, 50], "feet": [210, 350]}
    ok("with no zones drawn the lowest feet win",
       _nearest_person([_high, _p], []), _p)
    ok("nobody detected means nobody picked",
       _nearest_person([], ZONES), None)

    # And that person's crop is a real zoom.
    _g = {"x": _p["box"][0], "y": _p["box"][1],
          "w": _p["box"][2], "h": _p["box"][3]}
    _h, _f = _crop_rect(_g, None, [{"y": _g["y"]}], (W, H))
    ok("the person crop is usable", (_h is not None and _f is not None),
       True)
    ok("  and far tighter than the frame", area(_f, W, H) < 5.0, True)
    print("     person crop covers %.1f%% of the frame" % area(_f, W, H))

    # ── THE LADDER IS WIRED IN THAT ORDER ───────────────────────────
    src = (ROOT / "backend/app/services/swinganalysis.py").read_text()
    _else = src.find("# ── NOBODY WAS PICKED. ZOOM ANYWAY.")
    _alt = src.find("_alt = _nearest_person(", _else)
    _zone = src.find("_zone_rect(zones, ox, oy, W, H)", _else)
    _frame = src.find("hrect = frect = (0, 0, W, H)", _else)
    ok("the person is tried before the zones", 0 < _alt < _zone, True)
    ok("  and the zones before the whole frame", 0 < _zone < _frame, True)
    ok("  with the whole frame still there as the last rung",
       _frame > 0, True)
    ok("and the rung used is reported, not silent",
       'out["zoom_from"] = _from' in src, True)
    ok("  on the line the Test cut page already shows",
       'out["picked_front"] = "%s · zoomed to %s"' in src, True)
    ok("  and carried to the rig",
       '"zoom_from")' in
       (ROOT / "backend/app/routers/cameras.py").read_text(), True)

    # ── A SMALL CROP IS SHOWN BIG ENOUGH TO READ ────────────────────
    #
    # The tiles used only ever to be SHRUNK. A fallback crop around a
    # distant figure is ~120px across, and a 120px tile with a 20px
    # caption burned into it wastes the zoom that produced it.
    from app.services.swinganalysis import FRAME_TILE_MIN_PX
    ok("the tile floor is applied before the caption is drawn",
       0 < src.find("FRAME_TILE_MIN_PX / float(max(th, tw))")
       < src.find('lbl = "%+.2fs" % off'), True)
    ok("  and only ever upscales, never past the ceiling",
       "elif 0 < max(th, tw) < FRAME_TILE_MIN_PX:" in src, True)

    # ── AND IT REALLY COMES OUT THAT WAY ────────────────────────────
    #
    # Not source-reading: build a wide course frame with a figure in
    # the zone, run the real `analyse`, and measure the picture it
    # wrote. This is the only test in the suite that executes the
    # analysis end to end, which is how the last two bugs here shipped.
    import tempfile
    import numpy as np
    import cv2
    from app.services.swinganalysis import analyse

    def _frame(dx=0):
        im = np.full((H, W, 3), 70, np.uint8)
        im[:420] = (150, 130, 110)
        cv2.rectangle(im, (470 + dx, 610), (505 + dx, 706), (40, 40, 45), -1)
        return im

    _heat = np.zeros((H, W, 3), np.uint8)
    cv2.rectangle(_heat, (470, 600), (510, 706), (255, 120, 0), -1)
    with tempfile.TemporaryDirectory() as _d:
        _r = analyse([_frame(i) for i in range(6)], _heat,
                     {"main": {"x": 470, "y": 600, "w": 40, "h": 106,
                               "area": 4240}, "others": []},
                     crop=(0, 0, W, H), zones=ZONES,
                     out_dir=Path(_d), prefix="t", judge=False)
        ok("analyse reports which rung it zoomed to",
           bool(_r.get("zoom_from")), True)
        ok("  without claiming a golfer was confirmed",
           _r.get("golfer_crop"), False)
        ok("  and says so on the Test cut crop line",
           "zoomed to" in (_r.get("picked_front") or ""), True)
        ok("  and no longer claims there was no crop",
           "no zoom crop on this one" in (_r.get("ball_why") or ""), False)
        _im = cv2.imread(str(Path(_d) / (_r.get("image") or "")))
        ok("the heat picture was actually written", _im is not None, True)
        if _im is not None:
            _hh, _ww = _im.shape[:2]
            ok("  and is narrower than the frame it came from",
               _ww < W, True)
            print("     heat picture is %dx%d, from a %dx%d frame"
                  % (_ww, _hh, W, H))

    # ── A PICKED GOLFER IS UNTOUCHED ────────────────────────────────
    #
    # Rung 1 still runs through the same `_crop_rect`, and an
    # unusable rect there is still "could not tell" rather than a
    # verdict on a picture nobody chose.
    ok("a golfer in a zone still crops to the golfer",
       _crop_rect({"x": 460, "y": 600, "w": 50, "h": 110}, 700,
                  [{"y": 600}], (W, H))[1] is not None, True)
    ok("  and an off-picture golfer is still unusable",
       _usable(2000, 2000, 2100, 2100, W, H), None)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
