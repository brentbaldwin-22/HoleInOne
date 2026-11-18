#!/usr/bin/env python3
"""The flight between the strike and the chain must not be a hole.

THE SYMPTOM, from a produce that got everything else right: the ball
placed correctly at f797, then the tracer's next point at f824. Five
points -- f797, 824, 825, 827, 832 -- with twenty-seven frames of
nothing in between, crossed in one straight jump that reads as a
delay. Plotted by hand the same flight gives six points between f792
and f798. They are there; nothing had looked.

Nothing had looked because the band is floored at the skyline and the
ball does not cross it until f800. The merge that exists for exactly
this -- "the flight points are the part of the climb the band never
sees" -- can only place points it is GIVEN, and stage 1 gives it one:
the ball.

`fill_chain_holes` already does this job for holes inside a chain:
take the two points either side, crop to the segment between them
plus a pad, run a two-frame pass over just those frames. The ball at
rest and the chain's foot ARE two points either side of a hole. It
had never been asked.

    python3 tools/climb_fill_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def rep(impact=792, foot=824, rest=(669, 727), seen=789):
    # seen = the frame the ball was OBSERVED still at rest; impact is
    # the midpoint between it and the first frame the ball was gone,
    # so impact is always >= seen and may sit after the ball left.
    _ai = {"rest": {"x": rest[0], "y": rest[1]},
           "measured_impact_frame": impact,
           "points": [{"frame": impact, "x": rest[0],
                       "y": rest[1]}], "n_points": 1}
    if seen is not None:
        _ai["measured_impact_seen"] = seen
    return {
        "ai_launch": _ai,
        "ascents": [{"points": [{"frame": foot + i, "x": 700 + i * 20,
                                 "y": 600 - i * 18} for i in range(5)]}],
    }


# The fill re-reads the video; stub it so the test is about the
# PLUMBING -- which frames are asked for, and what is kept.
class _Fake:
    def __init__(self, give):
        self.give = give
        self.seen = None

    def __call__(self, path, pts, max_hole, sens=3, budget=None):
        self.seen = {"max_hole": max_hole, "n_seed": len(pts),
                     "first": int(pts[0]["frame"]),
                     "last": int(pts[-1]["frame"])}
        return list(pts) + list(self.give)


def run(r, give, **kw):
    import app.services.debug3 as d3
    _was = d3.fill_chain_holes
    fake = _Fake(give)
    d3.fill_chain_holes = fake
    try:
        return admin._fill_climb_to_ball(r, "/x.mp4", {"sens": 3}, 0,
                                         60.0, **kw), fake
    finally:
        d3.fill_chain_holes = _was


print("it asks about the gap between the ball and the chain:")
r = rep()
out, fake = run(r, [])
check("it ran", out.get("ran"), True)
# ── THE FLOOR IS THE ONE BOUND WRONG IN NEITHER DIRECTION ──────────
#
# Both of the obvious floors have now been tried on this swing and
# both produced a wrong path:
#
#   the strike estimate   f792 -- a MIDPOINT, so by construction it
#                         can sit after the ball left; flooring here
#                         excludes the earliest flight frames, which
#                         is the eight-frame error this exists to fix
#   40 frames flat        f784 -- reaches into the DOWNSWING, where
#                         the ball has not moved and the club is the
#                         only thing crossing it; this returned
#                         twenty points "all over the place"
#
# The frame the ball was OBSERVED still at rest is neither: the ball
# cannot have left before it, and the club has not arrived yet.
check("seeded at the frame the ball was last seen at rest",
      fake.seen["first"], 789)
check("  not at the strike midpoint, which can overshoot",
      fake.seen["first"] <= 792)
check("  and not back in the downswing",
      fake.seen["first"] > 824 - 40)
check("  and it says which floor it used",
      "last seen at rest" in (out.get("look_floor_from") or ""))
check("  up to the chain", fake.seen["last"], 828)
check("  asking for a hole at least as big as the gap",
      fake.seen["max_hole"] >= 824 - 792)
check("  and reports the gap", out.get("gap_frames"), 32)

print()
print("40 frames is a ceiling, not the floor:")
# Far-apart chain: the ceiling binds and the window is 40 frames.
r2 = rep(impact=792, foot=840, seen=700)
out2, fake2 = run(r2, [])
check("a distant chain is capped at 40 frames back",
      fake2.seen["first"], 840 - 40)

print()
print("and with no observed rest frame it falls back to the strike:")
r3 = rep(seen=None)
out3, fake3 = run(r3, [])
check("floored at the estimate", fake3.seen["first"], 792)
check("  and says that is what it had",
      "never seen at rest" in (out3.get("look_floor_from") or ""))

print()
print("what it finds goes on the front of the flight:")
found = [{"frame": f, "x": 672 + (f - 792) * 2, "y": 724 - (f - 792) * 2}
         for f in (800, 805, 810)]
r = rep()
out, _ = run(r, found)
check("three points found", out.get("n_found"), 3)
_p = r["ai_launch"]["points"]
check("  the ball is still first", int(_p[0]["frame"]), 792)
check("  then the climb, in order",
      [int(q["frame"]) for q in _p], [792, 800, 805, 810])

print()
print("ONLY what lies in the gap — not the chain it was seeded with:")
# fill_chain_holes is handed the whole chain so it can see both sides,
# and returns those points too. They must not be re-added.
r = rep()
out, _ = run(r, [{"frame": 824, "x": 700, "y": 600},
                 {"frame": 900, "x": 900, "y": 400},
                 {"frame": 784, "x": 669, "y": 727},
                 {"frame": 790, "x": 672, "y": 724}])
check("the chain's own foot is not re-added", out.get("n_found"), 1)
check("  nor anything above it, nor the seed itself",
      [int(q["frame"]) for q in r["ai_launch"]["points"]], [792, 790])
# BEFORE THE MIDPOINT IS FINE -- that is the whole point, the ball had
# already gone by then. f790 is after the ball was last seen at rest
# (f789), so it is flight and it is kept.
check("  a frame between the last rest and the midpoint is kept",
      790 in [int(q["frame"]) for q in r["ai_launch"]["points"]])
# BUT THE DOWNSWING IS NOT. f784 is before the ball moved, so whatever
# a two-frame pass saw there was the club.
check("  and a frame from before the ball moved is refused",
      784 in [int(q["frame"]) for q in r["ai_launch"]["points"]], False)

print()
print("and it declines rather than drawing between two things:")
r = rep(foot=793)
out, _ = run(r, [])
check("chain already at the strike -> nothing to fill",
      out.get("ran"), False)
check("  and says so", "no climb to fill" in (out.get("why") or ""))

r = rep(foot=797 + 200)
out, _ = run(r, [])
check("a gap of seconds -> refuses", out.get("ran"), False)
check("  rather than joining two different flights",
      "one flight" in (out.get("why") or ""))

r = rep()
r["ai_launch"]["rest"] = None
r["ai_launch"]["measured_impact_frame"] = None
out, _ = run(r, [])
check("no measured ball -> nothing to climb from", out.get("ran"), False)

r = rep()
r["ascents"] = []
out, _ = run(r, [])
check("no chain -> nothing to climb to", out.get("ran"), False)

print()
print("the produce runs it before the merge that places the points:")
src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
_i = src.index('rep["climb_fill"]')
_j = src.index('rep["ai_launch_merge"]')
check("fill first, merge second", _i < _j)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the climb below the band is looked for")
