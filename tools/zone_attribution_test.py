#!/usr/bin/env python3
"""WHICH tee a dwell fired on, when the strips overlap.

THE OPERATOR, 13 Nov: "when we have a dwell trigger, can we identify
what trigger zone was triggered. it should be the lowest trigger zone
where there was movement (if multiple)."

The first half was already true: `_dwell_detail` records `zone` and
`zone_label` on every dwell, manual and capture trigger.

The second half was not. It asked `tee_roi.which`, which returns the
FIRST box in the list holding the point -- the right answer for
`contains` ("is anybody on a tee at all") and an arbitrary one for
"which tee", because the list is in the order the boxes were drawn.

AND THEY OVERLAP IN PRACTICE. A tee deck is a few strips across the
same turf, their edges meet or cross, and a golfer near a boundary is
genuinely inside two. Drawing order is not a reason to prefer either.

LOWEST ON SCREEN MEANS NEAREST on this camera: it stands behind the
hitting area looking down the hole, so the back tee is closest to it
and sits lowest in frame while the forward tees recede up the
picture.

    python3 tools/zone_attribution_test.py
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
    from app.services.tee_roi import lowest_at, which, contains

    # Drawn forward-first, which is the natural order working back
    # from the green -- and therefore the WORST order for `which`.
    Z = {"boxes": [{"x": 400, "y": 440, "w": 320, "h": 50, "label": "forward"},
                   {"x": 380, "y": 470, "w": 360, "h": 50, "label": "middle"},
                   {"x": 360, "y": 500, "w": 400, "h": 110, "label": "back"}],
         "frame": {"w": 1920, "h": 1080}}

    ok("a golfer clearly on the forward tee is on it",
       lowest_at((500, 450), Z), 0)
    ok("  and one clearly on the back tee is on that",
       lowest_at((500, 580), Z), 2)

    # ── THE CASE THAT CHANGED ───────────────────────────────────────
    ok("on a boundary, the LOWER tee wins", lowest_at((500, 480), Z), 1)
    ok("  where drawing order used to", which((500, 480), Z), 0)
    ok("and again a strip down", lowest_at((500, 510), Z), 2)
    ok("  where drawing order used to", which((500, 510), Z), 1)

    # ── AND THE BOTTOM EDGE IS THE MEASURE, NOT THE TOP ─────────────
    #
    # A tall strip that starts high but reaches furthest down is the
    # one whose turf is nearest, so it wins over a short strip sitting
    # above it.
    T = {"boxes": [{"x": 0, "y": 500, "w": 100, "h": 20},    # short, low top
                   {"x": 0, "y": 480, "w": 100, "h": 60}],   # taller, lower
         "frame": {"w": 1920, "h": 1080}}
    ok("the strip reaching furthest down wins", lowest_at((50, 510), T), 1)

    # An exact tie keeps the first: the only case where drawing order
    # should settle anything.
    E = {"boxes": [{"x": 0, "y": 480, "w": 100, "h": 40},
                   {"x": 0, "y": 490, "w": 100, "h": 30}],
         "frame": {"w": 1920, "h": 1080}}
    ok("an exact tie on the bottom edge keeps the first",
       lowest_at((50, 500), E), 0)

    # ── AND NOTHING ELSE MOVED ──────────────────────────────────────
    #
    # `contains` decides whether to record at all and must be
    # untouched: any match is still a match, whichever index it is.
    ok("off every tee is still nobody", lowest_at((100, 100), Z), None)
    ok("  and contains agrees", contains((100, 100), Z), False)
    ok("on a tee is still somebody", contains((500, 480), Z), True)
    ok("no zones drawn is not a zone", lowest_at((5, 5), {"boxes": []}), None)
    ok("  and junk does not raise",
       lowest_at((5, 5), {"boxes": [{"x": "a"}]}), None)

    # ── THE DWELL RECORDS IT ────────────────────────────────────────
    _tee = (ROOT / "pi-agent/agent/tee.py").read_text()
    ok("the dwell detail carries the zone", '"zone": z,' in _tee, True)
    ok("  with the operator's own label for it",
       '"zone_label": self._zone_name(roi, z)' in _tee, True)
    ok("  and attribution asks for the lowest",
       "tee_roi.lowest_at(pt, roi)" in _tee, True)
    ok("  not the first drawn", "tee_roi.which(pt, roi)" in _tee, False)

    # ── BOTH COPIES OF tee_roi STAY ONE FILE ────────────────────────
    _a = (ROOT / "pi-agent/agent/tee_roi.py").read_text()
    _b = (ROOT / "backend/app/services/tee_roi.py").read_text()
    ok("the rig has it too", "def lowest_at(" in _a, True)
    ok("  and the backend", "def lowest_at(" in _b, True)
    ok("  identically",
       _a[_a.index("def lowest_at("):_a.index("def contains(")]
       == _b[_b.index("def lowest_at("):_b.index("def contains(")], True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
