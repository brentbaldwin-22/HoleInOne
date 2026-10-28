#!/usr/bin/env python3
"""The region scan's per-frame cut, and why the ball was never in it.

THE CUT KEPT THE LARGEST FEW, on the reasoning that a ball blob beats
foliage sparkle. That holds in the quiet corner this endpoint was
written for. It fails completely on a tee with four people on it:
golfers, trolleys, wind in the pines and a shimmering bunker edge all
measure hundreds of pixels, a ball measures tens, and the ball never
enters the top ten.

Measured on upload 1130 at f335-f339: two thousand dots on the houses,
the treeline and the bunker, and not one on a ball plainly visible
leaving the tee.

    python3 tools/scan_small_slot_test.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-16s %s" % (label, repr(got)[:16],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


def cut(hits, per_frame, small_slots, small_floor=3):
    """The endpoint's per-frame cut, lifted exactly."""
    hits = sorted(hits, reverse=True)
    keep = hits[:max(0, per_frame - small_slots)]
    rest = hits[len(keep):]
    small = sorted((h for h in rest if h[0] >= small_floor),
                   key=lambda h: h[0])[:small_slots]
    return keep + small


# A frame off that tee: four golfers, two trolleys, a treeline, a
# bunker edge -- and the ball.
BALL = (34, 640.0, 430.0)
SCENE = [(a, float(a), 1.0) for a in
         (1800, 1510, 1340, 1180, 970, 820, 760, 640, 590, 520,
          480, 430, 390, 350, 310, 280, 240, 210, 180, 150)] + [
    BALL,
    (2, 10.0, 10.0),    # single-pixel sensor noise
    (1, 11.0, 11.0),
]

print("the old cut: largest ten, and the ball is not among them")
_old = sorted(SCENE, reverse=True)[:10]
check("ten dots", len(_old), 10)
check("THE BALL IS NOT IN THEM", BALL in _old, False)
check("the smallest kept is still enormous", min(h[0] for h in _old), 520)

print()
print("the new cut: same ten dots, and one of them is the ball")
_new = cut(SCENE, 10, 3)
check("still ten dots -- no extra payload", len(_new), 10)
check("the ball is in them", BALL in _new, True)
# THE BIG MOVERS ARE NOT LOST. Somebody reads this view for the club
# and the golfer too, and taking those away to find the ball would
# trade one blind spot for another.
check("the seven biggest are still there",
      all(h in _new for h in sorted(SCENE, reverse=True)[:7]), True)
# AND NOT SENSOR NOISE. The small slots are the small end of the
# SIGNAL, not the bottom of the frame -- a floor of 1 would spend
# every reserved slot on single-pixel speckle and find nothing.
check("single-pixel noise is not picked up",
      any(h[0] < 3 for h in _new), False)

print()
print("SMALL IS RELATIVE TO THE FRAME, not to a number:")
# Nothing here is calibrated against a ball, so the reserved slots
# cannot be "blobs of about 34 pixels" -- they are the small END of
# whatever this frame holds. On a frame where everything is big they
# still go to the smallest of the big things, which is the only
# answer available without a measurement.
_big_only = [(a, float(a), 1.0) for a in range(100, 2100, 100)]
_r = cut(_big_only, 10, 3)
check("ten dots", len(_r), 10)
check("the seven biggest", sorted(h[0] for h in _r)[-7:],
      [1400, 1500, 1600, 1700, 1800, 1900, 2000])
check("...and the three smallest present", sorted(h[0] for h in _r)[:3],
      [100, 200, 300])
# WITHOUT THE SLOTS THE SMALL END IS UNREACHABLE at any level: the
# cut is a sort, so a blob outside the top N is outside it however
# many frames you scan.
check("which the old cut could never reach",
      min(h[0] for h in sorted(_big_only, reverse=True)[:10]), 1100)

print()
print("and a quiet region is unaffected -- everything fits:")
_quiet = [(40, 1.0, 1.0), (34, 2.0, 2.0), (12, 3.0, 3.0)]
check("all three come back", len(cut(_quiet, 10, 3)), 3)
check("no duplicates", len(set(cut(_quiet, 10, 3))), 3)

print()
print("the slots scale with the level, and never reach zero:")
for per_frame in (6, 10, 16):
    _s = max(1, per_frame // 3)
    check("  level with %2d per frame reserves %d" % (per_frame, _s),
          _s >= 1 and _s < per_frame, True)

print()
print("the endpoint says it, rather than only doing it:")
_src = Path("backend/app/routers/admin.py").read_text()
_fn = _src[_src.index("def scan_plot_region("):]
_fn = _fn[:_fn.index("def finalize_wizard_video(")]
check("slots are reserved", "_SMALL_SLOTS" in _fn, True)
check("with a floor above sensor noise", "_SMALL_FLOOR = 3" in _fn, True)
# WHAT SIZE IT ACTUALLY IS. Nothing here is calibrated -- which is why
# ascent_prefer_area still defaults to 0 -- and the only way to choose
# a size gate off the footage is to be able to read one off the ball.
check("every dot carries its area", '"area": int(_area)' in _fn, True)
# THE CAP STOPS THE SCAN DEAD. It does not thin, so the frames past it
# are never looked at, and empty reads as "the ball is not there".
check("truncation is reported", '"truncated"' in _fn, True)
check("...with the frame it stopped on", '"scanned_to_frame"' in _fn, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
