#!/usr/bin/env python3
"""The heat picture and the frames are not the same picture.

EVENT #1505, from the field:

    AUDIO x6.9 · JUDGE: COULD NOT TELL — the golfer picked at
    1168x558 sits off the 881x998 picture being analysed, so there
    was no crop to judge. The clip is kept and uploaded unjudged.

The golfer was found CORRECTLY. The position was read in the raw
frames, which the rig crops to the trigger zone, and then used
against the heat composite, which the rig crops TWICE -- the zone,
then a tighter rect round the golfer -- and upscales when the result
is small. Two different regions at two different scales, treated as
one coordinate space.

The rig knew: it recorded the second rect as `meta["zoom"]`. But only
`meta["geo"]` is posted to /verify-swing, so the number stayed on the
Pi. It now rides inside `geo` as `zoom` and `zoom_px`.

    python3 tools/heat_space_test.py
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
    from app.services.swinganalysis import _heat_map_from, _head_boxes

    ox, oy, W, H = 400, 100, 881, 998
    GEO = {"zoom": [1500, 600, 440, 499], "zoom_px": [881, 998]}

    # ── WITH THE RIG'S RECT, THE MAP IS EXACT ───────────────────────
    zx, zy, sx, sy = _heat_map_from(GEO, ox, oy, W, H)
    ok("the origin is the heat's own rect, not the frames' crop",
       (zx, zy), (1500.0, 600.0))
    ok("  and the scale is the upscale the rig applied",
       (round(sx, 3), round(sy, 3)), (2.002, 2.0))

    # The golfer at frame (1600, 700) lands inside the heat picture.
    gx, gy = (1600 - zx) * sx, (700 - zy) * sy
    ok("a golfer in the heat's rect maps inside the picture",
       (0 <= gx < W and 0 <= gy < H), True)
    print("     frame (1600,700) -> heat (%.0f,%.0f) of %dx%d" % (gx, gy, W, H))

    # ── AND AN OLDER RIG IS UNCHANGED ───────────────────────────────
    #
    # A rig and a backend are updated separately and in either order.
    # Without the rect this must behave exactly as it always did --
    # the crop offset and no scaling -- so a rig that has not been
    # updated is no worse off than before.
    ok("no rect means the crop offset and no scaling",
       _heat_map_from({}, ox, oy, W, H), (400.0, 100.0, 1.0, 1.0))
    ok("  and a malformed one is ignored rather than trusted",
       _heat_map_from({"zoom": [1, 2]}, ox, oy, W, H),
       (400.0, 100.0, 1.0, 1.0))
    ok("  as is a zero-sized one",
       _heat_map_from({"zoom": [1, 2, 0, 9]}, ox, oy, W, H),
       (400.0, 100.0, 1.0, 1.0))

    # ── THE HEADS GO THROUGH THE SAME MAP ───────────────────────────
    #
    # They decide where the frames crop stops, so a head left in frame
    # pixels puts the roof in the wrong place on every picture.
    _p = [{"box": [1600, 700, 90, 200]}]
    _h = _head_boxes(_p, zx, zy, sx, sy)
    ok("a head is mapped too, not left in frame pixels",
       (round(_h[0]["x"]), round(_h[0]["y"])), (round(gx), round(gy)))
    ok("  and its width is scaled with it", round(_h[0]["w"]), round(90 * sx))

    # ── THE RIG SENDS IT INSIDE geo, WHICH IS WHAT TRAVELS ──────────
    _sh = (ROOT / "pi-agent/agent/swingheat.py").read_text()
    ok("the rig puts the rect inside geo", '_geo["zoom"]' in _sh, True)
    ok("  with the size the picture ended up", '_geo["zoom_px"]' in _sh, True)
    ok("  after the upscale, not before",
       _sh.index("img = cv2.resize(") < _sh.index('_geo["zoom_px"]'), True)
    # meta["zoom"] stays for the rig's own debug pictures; what matters
    # is that geo carries it now.
    ok("  and meta still has it for the rig's own use",
       'meta["zoom"]' in _sh, True)

    # ── AND THE BACKEND SAYS WHICH MAP IT USED ──────────────────────
    _sa = (ROOT / "backend/app/services/swinganalysis.py").read_text()
    ok("the analysis reports which map it used",
       'out["heat_map_from"]' in _sa, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
