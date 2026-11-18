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


def rep(impact=797, foot=824, rest=(669, 727)):
    return {
        "ai_launch": {"rest": {"x": rest[0], "y": rest[1]},
                      "measured_impact_frame": impact,
                      "points": [{"frame": impact, "x": rest[0],
                                  "y": rest[1]}], "n_points": 1},
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
check("  seeded from the strike", fake.seen["first"], 797)
check("  up to the chain", fake.seen["last"], 828)
check("  asking for a hole at least as big as the gap",
      fake.seen["max_hole"] >= 824 - 797)
check("  and reports the gap", out.get("gap_frames"), 27)

print()
print("what it finds goes on the front of the flight:")
found = [{"frame": f, "x": 672 + (f - 797) * 2, "y": 724 - (f - 797) * 2}
         for f in (800, 805, 810)]
r = rep()
out, _ = run(r, found)
check("three points found", out.get("n_found"), 3)
_p = r["ai_launch"]["points"]
check("  the ball is still first", int(_p[0]["frame"]), 797)
check("  then the climb, in order",
      [int(q["frame"]) for q in _p], [797, 800, 805, 810])

print()
print("ONLY what lies in the gap — not the chain it was seeded with:")
# fill_chain_holes is handed the whole chain so it can see both sides,
# and returns those points too. They must not be re-added.
r = rep()
out, _ = run(r, [{"frame": 824, "x": 700, "y": 600},
                 {"frame": 900, "x": 900, "y": 400},
                 {"frame": 797, "x": 669, "y": 727}])
check("the chain's own foot is not re-added", out.get("n_found"), 0)
check("  nor anything above it",
      [int(q["frame"]) for q in r["ai_launch"]["points"]], [797])

print()
print("and it declines rather than drawing between two things:")
r = rep(foot=798)
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
