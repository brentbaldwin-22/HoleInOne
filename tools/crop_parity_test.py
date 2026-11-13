#!/usr/bin/env python3
"""The two crop paths are one rule, and this is what holds them to it.

THERE WERE TWO. `swinganalysis.analyse` serves /verify-swing, the live
rig path; `tracer.swing_heat_check` serves the Test cut page, which is
where the operator checks that path. Both decided where to crop, and
they decided it separately.

So a fix landed on one and not the other, and the place it did not
land was the page used to verify the place it did. The same bug was
reported twice -- "we should never zoom in on someone not in the
zone", then "still grabbing this guy clearly not in the zone" -- and
between those two reports the rig path was already correct.

They were not even the same rule. Both padded a golfer by 0.69 of a
body width, 0.08 of a height below the feet and 0.10 above the head,
one as named constants and one as literals. The HEAD LINE differed
outright: tracer took any blob whose CENTRE fell inside the crop,
swinganalysis any box whose LEFT EDGE was within 40 px. Tracer's is
the one that survived -- 40 px means one thing on a golfer 90 px tall
and another on one 400 px tall.

    python3 tools/crop_parity_test.py
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
    from app.services import swingcrop as sc
    from app.services import swinganalysis as sa

    # ── NEITHER FILE MAY KEEP A COPY ────────────────────────────────
    #
    # The numbers, spelled out, are what drifted. If either file grows
    # its own again this fails before anybody has to notice a crop
    # looking wrong on one page and right on the other.
    for _rel in ("backend/app/services/tracer.py",
                 "backend/app/services/swinganalysis.py"):
        _src = (ROOT / _rel).read_text()
        _name = _rel.split("/")[-1]
        ok("%s keeps no pad constants of its own" % _name,
           ("SIDE_PAD_FRAC = " in _src or "* 0.69" in _src), False)
        ok("  nor its own below-feet pad" % (),
           "* 0.08)" in _src and "swingcrop" not in _src, False)
    _tr = (ROOT / "backend/app/services/tracer.py").read_text()
    ok("tracer calls the shared rule", "_sc.crop_rect(" in _tr, True)
    ok("  and the shared head line", "_sc.head_line(" in _tr, True)
    _sa = (ROOT / "backend/app/services/swinganalysis.py").read_text()
    ok("swinganalysis imports it too", "from .swingcrop import" in _sa, True)
    ok("  and defines no second crop_rect",
       "\ndef _crop_rect(" in _sa, False)

    # THE 40 px HEAD TEST IS GONE, not merely unused.
    ok("the flat 40 px head test is gone from both",
       ("- 40 <=" in _sa or "- 40 <=" in _tr), False)

    # ── THE RULE ITSELF, ON REAL GEOMETRY ───────────────────────────
    #
    # The 8 Nov golfer, in crop pixels: 94 wide, 208 tall, standing in
    # a zone whose bottom edge is y=234.
    W, H = 900, 420
    # Smaller y is HIGHER in the picture, so "taller" means a lower y.
    G = {"x": 80, "y": 60, "w": 94, "h": 208}      # feet at 268
    HEADS = [{"x": 80, "y": 60, "w": 94},          # him
             {"x": 600, "y": 20, "w": 90},         # a partner, far right
             {"x": 150, "y": 40, "w": 60}]         # taller, inside the crop
    _h, _f = sc.crop_rect(G, 268, HEADS, (W, H))
    ok("a golfer in a zone crops", (_h is not None and _f is not None),
       True)
    ok("  the heat runs to the top of the picture", _h[1], 0)
    # The highest head IN the crop is at y=60, and the pad above it is
    # a tenth of the golfer's 208 px height, so the frames start at 40.
    # The heat ignores all of that and runs to 0.
    ok("  the frames stop just above the highest head in it",
       _f[1], 40 - max(8, int(G["h"] * sc.ABOVE_HEAD_FRAC)))
    ok("    which is below where the heat starts", _f[1] > _h[1], True)
    ok("  both share the same sides and floor",
       (_h[0], _h[2], _h[3]), (_f[0], _f[2], _f[3]))
    print("     heat %r  frames %r" % (_h, _f))

    # THE HEAD LINE IS THE HIGHEST HEAD *IN* THE CROP. The partner at
    # x=600 is outside it and must not raise the roof; the taller
    # person at x=150 is inside it and must.
    # The taller man INSIDE the crop sets the line at 40, not the
    # golfer's own 60 -- and not the partner's 20, who is outside it.
    ok("a head inside the crop raises the roof",
       sc.head_line(HEADS, _h[0], _h[2], G["y"]), 40.0)
    ok("  one outside it does not",
       sc.head_line([{"x": 600, "y": 20, "w": 90}], _h[0], _h[2], G["y"]),
       float(G["y"]))
    ok("  and a head we cannot place errs upward, not downward",
       sc.head_line([{"y": 3}], _h[0], _h[2], G["y"]), 3.0)

    # ── BOTH PATHS ASK THE SAME QUESTION THE SAME WAY ───────────────
    #
    # swinganalysis re-exports under its old private names; those must
    # BE the shared functions, not lookalikes.
    ok("swinganalysis._crop_rect IS swingcrop.crop_rect",
       sa._crop_rect is sc.crop_rect, True)
    ok("  _usable is swingcrop.usable", sa._usable is sc.usable, True)
    ok("  _zoned_person is swingcrop.zoned_person",
       sa._zoned_person is sc.zoned_person, True)
    for _n in ("SIDE_PAD_FRAC", "BELOW_FEET_FRAC", "ABOVE_HEAD_FRAC",
               "MIN_CROP_PX", "ZONE_PAD_FRAC"):
        ok("  %s is the same number" % _n,
           getattr(sa, _n), getattr(sc, _n))

    # ── AN UNCROPPABLE GOLFER FALLS THROUGH, IN BOTH ───────────────
    #
    # `crop_rect` returns None when the picked golfer's box does not
    # land on the picture. Both callers must then drop to the ZONE
    # rect -- and in tracer that means the check cannot live inside
    # the branch it needs to escape, because a branch cannot hand over
    # to its own `elif` once entered.
    ok("an off-picture golfer gives no rect at all",
       sc.crop_rect({"x": 5000, "y": 5000, "w": 40, "h": 90}, None,
                    [], (W, H)), (None, None))
    _hoist = _tr.find("_g_hr = _g_fr = None")
    _use = _tr.find("_zone = (_g_hr[0], _g_hr[2], _g_hr[3])")
    _clear = _tr.find('out["golfer_crop"] = False')
    ok("tracer works the rects out before the branch that uses them",
       0 < _hoist < _clear < _use, True)
    ok("  and clears the pick when it cannot crop",
       "_front = None" in _tr[_clear - 200:_clear], True)
    ok("swinganalysis does the same, as 'could not tell'",
       "could not tell — the golfer picked at" in _sa, True)

    # ── AND THE ZONE RUNG GIVES A PAIR, NOT A STRIP OF SKY ──────────
    ZONES = [(395, 700, 190, 40), (460, 660, 120, 30)]
    _zh, _zf = sc.zone_rect(ZONES, 0, 0, 1456, 816)
    ok("with no heads to ask, the frames rect runs to the top",
       (_zh, _zf[1]), (_zf, 0))
    _zh2, _zf2 = sc.zone_rect(ZONES, 0, 0, 1456, 816,
                              heads=[{"x": 480, "y": 610, "w": 40}])
    ok("given a head, it stops above them instead",
       _zf2[1] > 0, True)
    ok("  and the heat still runs to the top", _zh2[1], 0)
    print("     zone frames: no heads %r, with a head %r" % (_zf, _zf2))

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
