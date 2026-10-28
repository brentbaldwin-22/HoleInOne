#!/usr/bin/env python3
"""A ball does not land in the sky, and nothing said so.

EVENT 1490'S GREEN. A dark branch across the top-right corner drew an
eleven-point chain that fell 322px, bent 0.39px against a limit of
1.0, leaned 4.1 degrees against a limit of 55, slowed by 0, and was
seen on 41% of the frames it crossed against a floor of 35%. It passed
every gate there is -- ACCEPTED -- and it "landed" at y=72 in a
1080-tall frame, roughly seven hundred pixels above the putting
surface.

THE EDGE GATE IS THE WRONG SHAPE FOR THIS. It is a 0.04-of-height
border band asking whether a landing is about to leave the picture. A
branch in the top corner is not about to leave the picture; it is
simply nowhere a ball can be. y=72 is 117px from the right edge and
29px clear of the top band, so it passed by a whisker.

    python3 tools/descent_sky_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.debug3 import (  # noqa: E402
    DESCENT_EDGE_MARGIN_FRAC, DESCENT_MIN_LAND_Y_FRAC, DESCENT_WHY_COLORS,
    GATE_VERDICT, resolve_gates,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


FRAME_H, FRAME_W = 1080, 1920
LAND = (1803, 72)          # what event 1490 called a landing


def on_edge(x, y, frac=DESCENT_EDGE_MARGIN_FRAC, h=FRAME_H, w=FRAME_W):
    """The existing gate, lifted exactly. Note both margins are a
    fraction of HEIGHT, including the one applied across the width."""
    m = frac * h
    return bool(y > h - m or y < m or (w and (x > w - m or x < m)))


def in_sky(y, frac=DESCENT_MIN_LAND_Y_FRAC, h=FRAME_H):
    """The new gate, lifted exactly."""
    sky = frac * h
    return bool(sky > 0 and y < sky)


print("why the edge gate let event 1490 through:")
check("the top band is 43px", int(DESCENT_EDGE_MARGIN_FRAC * FRAME_H), 43)
check("...and the landing was at y=72", LAND[1], 72)
check("so the edge gate says no problem", on_edge(*LAND), False)
check("even though it is 1008px above the bottom of the frame",
      FRAME_H - LAND[1], 1008)

print()
print("the sky gate refuses it:")
check("the sky line is at 270px", int(DESCENT_MIN_LAND_Y_FRAC * FRAME_H), 270)
check("y=72 IS SKY", in_sky(LAND[1]), True)

print()
print("and it keeps everything that lands on the ground:")
# A green camera is aimed at a green: the putting surface sits in the
# lower half of the picture on every camera on this course.
for _y in (300, 420, 540, 700, 860, 1000):
    check("  a landing at y=%4d is ground" % _y, in_sky(_y), False)
# THE BOUNDARY ITSELF. Exactly on the line is not above it.
check("exactly on the line is not sky", in_sky(270), False)
check("one pixel above it is", in_sky(269), True)

print()
print("the two gates do different jobs and neither replaces the other:")
# The edge gate still catches a landing walking out of the bottom or
# the side -- the windy-clip case it was written for, five chains
# "landing" at y=706-710 in a 720-tall frame.
check("a landing at the bottom edge is still refused by EDGE",
      on_edge(900, FRAME_H - 10), True)
check("...and is not sky", in_sky(FRAME_H - 10), False)
check("a landing in the top corner is sky, not edge",
      (in_sky(LAND[1]), on_edge(*LAND)), (True, False))

print()
print("it is tunable per hole, and can be turned off:")
_g = resolve_gates()
check("on by default", _g["descent_min_land_y_frac"], 0.25)
check("tunable up for a camera aimed down",
      resolve_gates({"descent_min_land_y_frac": 0.45})
      ["descent_min_land_y_frac"], 0.45)
# A CAMERA AIMED FLAT ENOUGH that the ground reaches the top of its
# picture has no sky to refuse, and must be able to say so.
check("0 turns it off entirely",
      in_sky(LAND[1], frac=resolve_gates({"descent_min_land_y_frac": 0.0})
             ["descent_min_land_y_frac"]), False)
check("and the ceiling is below the whole frame",
      resolve_gates({"descent_min_land_y_frac": 5.0})
      ["descent_min_land_y_frac"] <= 0.9, True)

print()
print("the table can read it: colour, verdict name, and an explanation:")
check("it has a colour", "sky" in DESCENT_WHY_COLORS, True)
check("...distinct from edge's",
      DESCENT_WHY_COLORS["sky"] != DESCENT_WHY_COLORS["edge"], True)
check("the gate maps to the verdict",
      GATE_VERDICT.get("descent_min_land_y_frac"), "sky")
_src = Path("backend/app/services/debug3.py").read_text()
check("and the gate list explains it", '{"key": "sky"' in _src, True)
# READ FROM THE HOLE'S TUNING, not the module default. The `points`
# gate fell into exactly this trap: tuning it appeared to work in one
# direction and did nothing in the other.
check("rebound from the resolved gates",
      'DESCENT_MIN_LAND_Y_FRAC = _G["descent_min_land_y_frac"]' in _src, True)
check("...before anything uses it",
      _src.index('DESCENT_MIN_LAND_Y_FRAC = _G')
      < _src.index("_sky = DESCENT_MIN_LAND_Y_FRAC * frame_h"), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
