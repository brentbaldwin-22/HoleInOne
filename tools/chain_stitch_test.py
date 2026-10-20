#!/usr/bin/env python3
"""One flight, one chain: does the sweep stop reporting it four times?

THE REAL CHAINS FROM #768, read off the operator's own table. Four
chains of six to eight points, every one of them accepted, every one
traced back to the tee as its own swing within 0.15s of the others --
of ONE ball, leaving once.

They lie within 4px of a single line over 389px of rise. They come
apart because at every frame the tracker chooses between the leading
and the trailing end of the same smear, and two chains that choose
differently diverge by a step and never come back. The table shows it
outright: the same coordinates carry different frame numbers in
different chains --

    (1362,491)  is f1546 in #2 and #4, f1547 in #3
    (1366,440)  is f1549 in #2 and #3, f1548 in #4

and y walks backwards between adjacent frames: f1554 (1378,313) is
ABOVE f1555 (1375,326).

    python3 tools/chain_stitch_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def chain(pts):
    ps = [{"frame": f, "x": x, "y": y, "area": 100} for f, x, y in pts]
    return {"points": ps,
            "span_px": ((ps[-1]["x"] - ps[0]["x"]) ** 2
                        + (ps[-1]["y"] - ps[0]["y"]) ** 2) ** 0.5,
            "rise_px": float(ps[0]["y"] - ps[-1]["y"])}


# Verbatim from the table: #1, #2, #3, #4.
C1 = chain([(1555, 1375, 326), (1558, 1384, 245), (1559, 1385, 235),
            (1560, 1386, 215), (1563, 1387, 202), (1566, 1395, 138),
            (1569, 1398, 108), (1570, 1400, 102)])
C2 = chain([(1546, 1362, 491), (1549, 1366, 440), (1550, 1368, 416),
            (1555, 1375, 326), (1557, 1379, 289), (1558, 1379, 277),
            (1561, 1386, 215), (1563, 1391, 176)])
C3 = chain([(1547, 1362, 491), (1548, 1364, 465), (1549, 1366, 440),
            (1550, 1368, 416), (1551, 1370, 393), (1552, 1372, 362),
            (1554, 1378, 313)])
C4 = chain([(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
            (1552, 1372, 362), (1556, 1380, 285), (1558, 1384, 245)])
FIELD = [C1, C2, C3, C4]


def test_the_four_become_one():
    out = d3.stitch_ascent_chains(FIELD)
    check("one chain, not four", len(out), 1)
    pts = out[0]["points"]
    check("it starts where the flight did", pts[0]["frame"], 1546)
    check("and ends where it did", pts[-1]["frame"], 1570)
    check("the whole rise, not a quarter of it", out[0]["rise_px"], 389.0)
    # CONTROL: the best single piece had 315px of it and no more.
    check("control, the best piece alone",
          max(c["rise_px"] for c in FIELD), 315.0)
    check("and it is longer than any piece",
          len(pts) > max(len(c["points"]) for c in FIELD), True)
    check("it says how many it was made of", out[0]["stitched_from"], 4)
    # THE WHOLE CHAIN, point for point. This is real footage with a
    # known answer, so there is no reason to assert anything weaker --
    # and the length alone cannot tell the leading end of each smear
    # from the trailing one, which is the choice that broke it.
    check("every point of it",
          [(q["frame"], q["y"]) for q in pts],
          [(1546, 491), (1547, 465), (1548, 441), (1549, 440),
           (1550, 416), (1551, 393), (1552, 362), (1554, 313),
           (1556, 285), (1558, 245), (1559, 235), (1560, 215),
           (1563, 176), (1566, 138), (1569, 108), (1570, 102)])


def test_the_joined_chain_is_a_flight():
    """Not just a pile of the four point lists."""
    pts = d3.stitch_ascent_chains(FIELD)[0]["points"]
    frames = [q["frame"] for q in pts]
    check("one point a frame at most", len(set(frames)), len(frames))
    check("in order", frames == sorted(frames), True)
    check("never steps back down",
          all(pts[i + 1]["y"] < pts[i]["y"] for i in range(len(pts) - 1)),
          True)
    # CONTROL: the union it was chosen from does all three wrong.
    union = [q for c in FIELD for q in c["points"]]
    _u = sorted(union, key=lambda q: q["frame"])
    check("control, the union repeats frames",
          len({q["frame"] for q in _u}) < len(_u), True)
    check("control, and walks backwards",
          any(_u[i + 1]["y"] > _u[i]["y"] for i in range(len(_u) - 1)),
          True)


def test_a_second_swing_is_not_joined_to_the_first():
    """The one thing this must never do.

    A second ball down the same line is a real second swing. What
    separates it from a piece of the first is TIME -- seconds, not a
    third of one.
    """
    later = chain([(f + 600, x, y)
                   for f, x, y in [(1546, 1362, 491), (1547, 1364, 465),
                                   (1548, 1366, 441), (1549, 1366, 440),
                                   (1550, 1368, 416), (1551, 1370, 393)]])
    out = d3.stitch_ascent_chains([C3, later])
    check("ten seconds later is a different swing", len(out), 2)
    # CONTROL: the same points without the ten seconds DO join, so the
    # test above is about the gap and not about the geometry.
    out2 = d3.stitch_ascent_chains([C3, chain(
        [(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
         (1549, 1366, 440), (1550, 1368, 416), (1551, 1370, 393)])])
    check("control, the same ball does", len(out2), 1)


def test_a_different_line_is_not_joined():
    """A chain that crosses this one is another object."""
    across = chain([(1548, 1200, 470), (1550, 1240, 455),
                    (1552, 1280, 440), (1554, 1320, 425),
                    (1556, 1360, 410), (1558, 1400, 395)])
    out = d3.stitch_ascent_chains([C3, across])
    check("two objects stay two", len(out), 2)


FLIGHT = chain([(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
                (1549, 1366, 440), (1550, 1368, 416), (1551, 1370, 393),
                (1552, 1372, 362), (1554, 1378, 313), (1556, 1380, 285),
                (1558, 1384, 245), (1559, 1385, 235), (1560, 1386, 215),
                (1563, 1387, 202), (1566, 1395, 138), (1569, 1398, 108),
                (1570, 1400, 102)])
# Four points crossing the flight sideways, at the flight's own x. A
# bird, a wobbling branch, half a golfer's club.
CROSS = chain([(1555, 1372, 300), (1556, 1378, 302),
               (1557, 1384, 304), (1558, 1390, 306)])


def test_something_crossing_the_flight_is_not_part_of_it():
    """Which is the whole reason the test runs BOTH ways.

    A line is infinite and a short chain's is fitted to almost no
    evidence, so a thing that merely CROSSES the flight sits within
    tolerance of the flight's own axis -- 0.9 to 11.4px, every point of
    it. Only the other direction catches it: the flight is up to 197px
    off the crossing chain's axis, because that axis runs sideways.
    """
    _fa = d3._chain_axis(FLIGHT["points"])
    _ca = d3._chain_axis(CROSS["points"])
    check("the crosser is on the flight's line",
          all(d3._off_axis(_fa, q) <= 12.0 for q in CROSS["points"]), True)
    check("but the flight is nowhere near its line",
          all(d3._off_axis(_ca, q) <= 12.0 for q in FLIGHT["points"]), False)
    check("so they are not joined",
          len(d3.stitch_ascent_chains([FLIGHT, CROSS])), 2)
    check("and the flight is not shortened",
          max(len(c["points"])
              for c in d3.stitch_ascent_chains([FLIGHT, CROSS])), 16)


def test_a_mostly_on_the_line_stray_is_not_joined():
    """The OTHER half of the both-ways test.

    A three-point stray with two of its points on the flight and one
    22px off to the side has an axis the whole flight sits within 9px
    of -- so asking "are the long chain's points on the short one's
    line" says yes. Only the other direction notices that the stray
    itself reaches 24px off the flight.
    """
    stray = chain([(1551, 1370, 393), (1557, 1403, 300),
                   (1563, 1387, 202)])
    _sa = d3._chain_axis(stray["points"])
    _fa = d3._chain_axis(FLIGHT["points"])
    check("the flight is on the stray's line",
          all(d3._off_axis(_sa, q) <= 12.0 for q in FLIGHT["points"]), True)
    check("but the stray is not on the flight's",
          all(d3._off_axis(_fa, q) <= 12.0 for q in stray["points"]), False)
    check("so they are not joined",
          len(d3.stitch_ascent_chains([FLIGHT, stray])), 2)


def test_one_stray_point_does_not_swing_the_line():
    """Which is why the axis is a fit and not the two end points.

    A chain's last point is the one most likely to be wrong -- it is
    where a flight is faintest and where the tracker reaches furthest.
    A line drawn through the ends hangs entirely on it; a least-squares
    line through all of them barely moves.
    """
    good = [(1546, 1362, 491), (1548, 1366, 441), (1550, 1368, 416),
            (1552, 1372, 362), (1554, 1378, 313), (1556, 1380, 285),
            (1558, 1384, 245), (1560, 1386, 215)]
    import math as _m
    # The same chain with its last point pulled 25px sideways.
    bent = chain(good[:-1] + [(1560, 1411, 215)])["points"]
    strayed = d3._chain_axis(bent)
    # Every one of the honest points is still on the line fitted to
    # them, 9.7px off at worst, so this chain still joins its own
    # continuation.
    check("the fit holds the line",
          all(d3._off_axis(strayed, q) <= 12.0
              for q in chain(good)["points"]), True)
    # CONTROL: the line through the two END points is dragged 2.2
    # degrees over by that one stray, which over 276px of rise puts
    # the far end of the chain 14.7px out -- outside tolerance, and
    # the chain stops recognising itself.
    _cx = sum(q["x"] for q in bent) / len(bent)
    _cy = sum(q["y"] for q in bent) / len(bent)
    _th = _m.atan2(float(bent[-1]["y"] - bent[0]["y"]),
                   float(bent[-1]["x"] - bent[0]["x"]))
    ends = (_cx, _cy, _m.cos(_th), _m.sin(_th))
    check("control, an endpoint line does not",
          all(d3._off_axis(ends, q) <= 12.0
              for q in chain(good)["points"]), False)


def test_the_answer_does_not_depend_on_the_order_in():
    """Two passes feed this, in no particular order.

    The tracker's chains and the fitter's arrive mixed, and grouping is
    greedy -- first match wins. The same clip has to answer the same
    way whichever order they come out in.
    """
    import itertools
    want = None
    for perm in itertools.permutations(FIELD):
        out = d3.stitch_ascent_chains(list(perm))
        got = (len(out), [(q["frame"], q["y"]) for q in out[0]["points"]])
        if want is None:
            want = got
        elif got != want:
            check("every order gives the same chain", got, want)
            return
    check("every order gives the same chain", want[0], 1)
    check("and it is the whole flight", len(want[1]), 16)


def test_off_by_default_is_not_how_this_ships():
    check("the join is on", d3.ASCENT_STITCH_PX > 0, True)
    check("and the gate carries it",
          d3.GATE_SPEC["ascent_stitch_px"][0], d3.ASCENT_STITCH_PX)


for fn in (test_the_four_become_one,
           test_the_joined_chain_is_a_flight,
           test_a_second_swing_is_not_joined_to_the_first,
           test_a_different_line_is_not_joined,
           test_something_crossing_the_flight_is_not_part_of_it,
           test_a_mostly_on_the_line_stray_is_not_joined,
           test_one_stray_point_does_not_swing_the_line,
           test_the_answer_does_not_depend_on_the_order_in,
           test_off_by_default_is_not_how_this_ships):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
