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
  2. a person the picker put IN a zone  -- not confirmed, still in a zone
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
        _zone_rect, _zoned_person, _crop_rect, _usable)

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

    # ── RUNG 2: A PERSON THE PICKER PUT IN A ZONE ───────────────────
    #
    # THE RULE, from the field, after this rung zoomed hard onto a man
    # walking past a golf cart on the far side of the hole: "we should
    # never zoom in on someone not in the zone."
    #
    # The first version took the person NEAREST a zone, and "nearest"
    # with no limit means anyone anywhere. A crisp picture of somebody
    # not swinging is worse than the whole frame, not better -- the
    # frame at least looks like a frame, while that looks like an
    # answer, and with `verify_drops` it deletes the clip.
    #
    # `considered` is the picker's own record of every person it looked
    # at and whether their feet were in a zone. Asking it rather than
    # re-deriving containment is the point: two notions of "in a zone"
    # that disagree is a camera cropping where its own rule says
    # nobody is standing.
    _cart = {"feet": [1067, 716], "box": [1049, 620, 36, 96],
             "zone": False, "floor": None}
    _teed = {"feet": [500, 710], "box": [480, 610, 40, 100],
             "zone": True, "floor": 740}
    _teed_far = {"feet": [505, 690], "box": [485, 590, 40, 100],
                 "zone": True, "floor": 740}

    ok("a man by the cart, outside every zone, is NEVER picked",
       _zoned_person([_cart]), None)
    ok("  not even when he is the only person in the frame",
       _zoned_person([_cart]), None)
    ok("  nor when he is nearer the camera than the golfer",
       _zoned_person([_teed, _cart]), _teed)
    ok("a person the picker placed in a zone is picked",
       _zoned_person([_teed]), _teed)
    ok("  and of two in zones, the one with the lowest feet",
       _zoned_person([_teed_far, _teed]), _teed)
    ok("  whichever order they arrive in",
       _zoned_person([_teed, _teed_far]), _teed)
    ok("nobody considered means nobody picked", _zoned_person([]), None)
    ok("  and junk in the list does not raise",
       _zoned_person([None, {}, {"zone": True}]), None)

    # It carries the zone floor the picker measured, so the crop stops
    # on the ground rather than at a waist.
    ok("the zone floor comes along", _zoned_person([_teed])["floor"], 740)

    # And that person's crop is a real zoom.
    _g = {"x": _teed["box"][0], "y": _teed["box"][1],
          "w": _teed["box"][2], "h": _teed["box"][3]}
    _h, _f = _crop_rect(_g, _teed["floor"], [{"y": _g["y"]}], (W, H))
    ok("the person crop is usable", (_h is not None and _f is not None),
       True)
    ok("  and far tighter than the frame", area(_f, W, H) < 10.0, True)
    print("     person crop covers %.1f%% of the frame" % area(_f, W, H))

    # ── THE LADDER IS WIRED IN THAT ORDER ───────────────────────────
    src = (ROOT / "backend/app/services/swinganalysis.py").read_text()
    _else = src.find("# ── NOBODY WAS PICKED. ZOOM ANYWAY.")
    _alt = src.find("_alt = _zoned_person(", _else)
    _zone = src.find("_zone_rect(zones, ox, oy, W, H)", _else)
    _frame = src.find("hrect = frect = (0, 0, W, H)", _else)
    ok("the in-zone person is tried before the zones",
       0 < _alt < _zone, True)
    ok("  and the zones before the whole frame", 0 < _zone < _frame, True)
    ok("  with the whole frame still there as the last rung",
       _frame > 0, True)
    ok("and the rung used is reported, not silent",
       'out["zoom_from"] = _from' in src, True)
    ok("  on the line the Test cut page already shows",
       'out["picked_front"] = "%s · zoomed to %s"' in src, True)
    # IN THE KEEP TUPLE, wherever in it. Pinning the exact text
    # '"zoom_from")' meant adding the next field after it broke this
    # check rather than the wiring it is about.
    _cam = (ROOT / "backend/app/routers/cameras.py").read_text()
    _keep = _cam[_cam.find("    keep = ("):]
    _keep = _keep[:_keep.find(")")]
    ok("  and carried to the rig", '"zoom_from"' in _keep, True)

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

    # ── THE REPORTED CASE, END TO END ───────────────────────────────
    #
    # Tee empty, one man walking past a golf cart on the far side of
    # the hole. The crop must land on the ZONES and must not contain
    # him. This is the case that shipped: the unit checks above all
    # passed on the broken version too, because the bug was in which
    # people were offered, not in what was done with them.
    with tempfile.TemporaryDirectory() as _d:
        CART = 1050

        def _away(dx=0):
            im = np.full((H, W, 3), 70, np.uint8)
            im[:420] = (150, 130, 110)
            cv2.rectangle(im, (CART + dx, 620), (CART + 36 + dx, 716),
                          (40, 40, 45), -1)
            return im

        _h2 = np.zeros((H, W, 3), np.uint8)
        cv2.rectangle(_h2, (CART, 615), (CART + 48, 716), (255, 120, 0), -1)
        _r2 = analyse([_away(i * 3) for i in range(6)], _h2,
                      {"main": {"x": CART, "y": 615, "w": 48, "h": 101,
                                "area": 4848}, "others": []},
                      crop=(0, 0, W, H), zones=ZONES,
                      out_dir=Path(_d), prefix="u", judge=False)
        ok("a man outside the zones does not get zoomed on",
           "person standing in a zone" in (_r2.get("zoom_from") or ""),
           False)
        ok("  the crop falls to the zones instead",
           "trigger zones" in (_r2.get("zoom_from") or ""), True)
        ok("  and says people WERE found, just not on the tee",
           "none were standing in one" in (_r2.get("zoom_from") or ""),
           True)
        _zr = _zone_rect(ZONES, 0, 0, W, H)
        ok("  and he is outside the crop entirely",
           _zr is not None and not (_zr[0] <= CART <= _zr[2]), True)
        print("     crop x %d..%d, the man at x=%d" % (_zr[0], _zr[2], CART))

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
