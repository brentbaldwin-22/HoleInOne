#!/usr/bin/env python3
"""A tracer that corners at the top is measured, not argued about.

Round after round of "the apex is too sharp" was a screenshot and a
guess, because no number anywhere said how sharp any given arc was.
Two guards already run on every tail and neither catches this:
`_turns_back_on_itself` wants >200 degrees of TOTAL turn (a loop), and
`_tail_refused` wants an apex off the top of the picture. A clean
90-degree corner in mid-frame passes both.

AND THE CAUSE IS IN THE MODEL'S OWN ALGEBRA. `_ballistic_tail` defines
the across-axis to start at zero speed, so all sideways motion comes
from `qv = dv/S^2`; at the along-apex the along-speed is zero by
construction, the whole screen speed is |dv|/S, and

    R = dv^2 / (2*v*S)

So the corner is set by how far the landing sits ACROSS the launch ray.
On a camera looking down the line that is small, and the arc turns in
no distance at all. This file pins that relationship against the real
model, so a change that flattens or worsens it cannot go unnoticed.

    python3 tools/apex_sharpness_test.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import ai_tracer as T  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


W, H, FPS = 1920, 1080, 60.0
UX, UY = 0.75, -0.66            # up and to the right
NX, NY = -UY, UX


def track(v, sec=0.8, n=16):
    span = sec * FPS
    return [(int(round(1000 + span * j / (n - 1.0))),
             660 + UX * v * (span * j / (n - 1.0)),
             510 + UY * v * (span * j / (n - 1.0))) for j in range(n)]


def drawn(dv, v=9.0, du=300.0, S=150):
    """The tail as the renderer would actually choose it, or None.

    REALISTIC NUMBERS MATTER HERE. A first pass at this used a hand-off
    speed of 18px/frame, which over a 200-frame bridge throws the apex
    a couple of thousand pixels above a 1080-tall frame -- every tail
    was refused and the grid measured nothing at all. A low shot that
    stays in the picture moves a few pixels a frame.
    """
    pts = track(v)
    x_a, y_a = pts[-1][1], pts[-1][2]
    tx, ty = x_a + UX * du + NX * dv, y_a + UY * du + NY * dv
    lf = pts[-1][0] + S
    for nm, fn, allowed in (("curved", T._curved_tail, 4 <= S <= 90),
                            ("ballistic", T._ballistic_tail, True),
                            ("arc", T._arc_tail, True)):
        if not allowed:
            continue
        t = fn(pts, (tx, ty), FPS, W, H, land_frame=lf)
        if (t and not T._tail_refused(t, W, H, nm)
                and not T._turns_back_on_itself(t)):
            return nm, t
    return None, None


print("\n-- a straight line does not turn")
flat = [(i, 100.0 + 5 * i, 400.0) for i in range(60)]
check("a straight tail measures 0 degrees",
      T.apex_sharpness(flat)["deg"], 0.0)

print("\n-- a right angle measures a right angle")
corner = ([(i, 100.0 + 5 * i, 400.0) for i in range(30)]
          + [(30 + i, 245.0, 400.0 + 5 * i) for i in range(30)])
_d = T.apex_sharpness(corner)["deg"]
check("a 90-degree corner reads ~90", 89.0 <= _d <= 91.0, True)
check("  and it says WHERE", T.apex_sharpness(corner)["xy"] is not None,
      True)

print("\n-- the corner sharpens as the landing nears the launch ray")
# THE RELATIONSHIP, against the real model and on tails that really
# reach the screen. dv is the only thing changing.
series = []
for dv in (3, 20, 60, 150):
    kind, t = drawn(dv)
    series.append((dv, kind, T.apex_sharpness(t)["deg"] if t else None))
for dv, kind, deg in series:
    print("     dv=%-4d %-10s %s deg" % (dv, kind, deg))
check("every one of them draws",
      all(d is not None for _, _, d in series), True)
check("a landing on the launch ray corners hard", series[0][2] >= 70.0,
      True)
check("  150px across is gentle", series[-1][2] < 25.0, True)
check("  and it falls away monotonically",
      [d for _, _, d in series] == sorted((d for _, _, d in series),
                                          reverse=True), True)
# THE CLOSED FORM, checked rather than asserted: R = dv^2/(2 v S).
_R = lambda dv: dv * dv / (2 * 9.0 * 150)      # noqa: E731
check("the apex radius at dv=3 is sub-pixel", _R(3) < 1.0, True)
check("  and at dv=150 it is tens of pixels", _R(150) > 8.0, True)

print("\n-- the existing guards do NOT catch it (which is why this exists)")
_kind, _t = drawn(3)
check("the hard-cornered tail is the one that got drawn", _t is not None,
      True)
check("  it is not rejected as a loop", T._turns_back_on_itself(_t), False)
check("  nor refused for its apex", T._tail_refused(_t, W, H, _kind), False)
check("  yet it corners past 70 degrees",
      T.apex_sharpness(_t)["deg"] >= 70.0, True)

print("\n-- the curvature-matched model is no escape at small dv")
# `_curved_tail` was added to cure the ballistic hairpin, and at a
# landing near the launch ray it is WORSE -- it folds back on itself.
# Worth pinning: a future fix that only swaps models does not fix this.
_c = T._curved_tail(track(6.0), None, FPS, W, H) if False else None
pts = track(6.0)
x_a, y_a = pts[-1][1], pts[-1][2]
_c = T._curved_tail(pts, (x_a + UX * 300.0 + NX * 3,
                          y_a + UY * 300.0 + NY * 3),
                    FPS, W, H, land_frame=pts[-1][0] + 90)
check("curved at dv=3 is not gentler",
      _c is not None and T.apex_sharpness(_c)["deg"] > 70.0, True)

print("\n-- it never raises on what a renderer really hands it")
for name, bad in (("None", None), ("empty", []),
                  ("one point", [(1, 2.0, 3.0)]),
                  ("junk", [("a", "b", "c")])):
    check("%s gives no reading, no crash" % name,
          T.apex_sharpness(bad)["deg"], None)

print("\n-- it travels with the clip")
src = (ROOT / "backend" / "app" / "services"
       / "ai_tracer.py").read_text(encoding="utf-8")
check("both tail records carry it",
      src.count('"sharpness": apex_sharpness(_tail),'), 2)
adm = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("produce logs it", "tracer turns %.0f\u00b0 at its" in adm, True)
check("  and puts it on the report",
      '"sharpness": ((_rv or {}).get("tail")' in adm, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
