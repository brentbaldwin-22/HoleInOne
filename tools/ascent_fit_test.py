#!/usr/bin/env python3
"""Can a flight be pulled out of the cloud instead of walked into?

build_tracks is SEQUENTIAL: it walks frames in order and extends each
chain one detection at a time, choosing locally and without knowing how
the choice turns out. One bad link ends a flight, and a detection taken
by a longer chain is gone whatever it was.

Measured on #768, after the detector was fixed and the corridor added:
a clean column of detections from f1555 to f1581, and the best chain
the tracker built out of it was FOUR points over sixteen frames — gaps
of four and ten, density 0.25, refused on rate, sparse AND gap. The
flight is obvious in the picture; the walk cannot see it, because
seeing it means looking at every point at once.

A struck ball crossing the band is a straight line at constant speed.
Propose one from a pair of detections, count how many others lie on it,
keep the best. Nothing sequential, nothing to steal, and a frame the
detector missed costs one inlier rather than the rest of the chain.

    python3 tools/ascent_fit_test.py
"""
from __future__ import annotations

import random
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


BX, BY, VX, VY, F0, NF = 1360, 500, 2.0, -15.0, 1550, 30


def flight(drop=(), decel=0.0):
    """The ball, optionally missing on some frames — the detector does
    miss a few, and a fit must not care.

    `decel` slows it as it climbs, which is what a real one does: the
    y-steps measured on #768 run 26, 24, 25, 23, 22, 19, 21, 18, 17, 18
    over the first twenty frames, from perspective as much as physics.
    """
    out = []
    y = float(BY)
    for i in range(NF):
        if i not in drop:
            out.append({"frame": F0 + i, "x": int(BX + VX * i),
                        "y": int(round(y)), "area": 100})
        y += VY + decel * i
    return out


def clutter(per_frame, seed=11):
    rng = random.Random(seed)
    return [{"frame": F0 + i, "x": rng.randint(1000, 1800),
             "y": rng.randint(0, 500), "area": 100}
            for i in range(NF) for _ in range(per_frame)]


def on_ball(chains):
    best = 0
    for c in chains:
        pts = c["points"]
        if pts and all(abs(q["x"] - (BX + VX * (q["frame"] - F0))) <= 9
                       for q in pts):
            best = max(best, len(pts))
    return best


def fragments(chains, lo=4):
    """How many separate chains of any length the pass returned on the
    ball's own line. One flight should be one chain; six was the bug."""
    return sum(1 for c in chains
               if len(c["points"]) >= lo
               and all(abs(q["x"] - (BX + VX * (q["frame"] - F0))) <= 9
                       for q in c["points"]))


def main() -> int:
    # ---- a clean flight -----------------------------------------------
    check("a clean flight comes back whole",
          on_ball(d3.fit_ascent_chains(flight(), 4)), NF)

    # ---- A REAL ONE DECELERATES, and a line is the wrong shape -------
    # On #768 this split ONE flight into six overlapping fragments: two
    # passed the gates as separate swings 40ms apart, four were refused
    # on `rise` for being too short, and all six were the same shot.
    _slow = flight(decel=0.45)
    _got = d3.fit_ascent_chains(_slow, 4, refine=True)
    check("a decelerating flight still comes back whole",
          on_ball(_got), NF)
    check("  as ONE chain, not a handful of fragments",
          fragments(_got), 1)

    # ---- with frames missing ------------------------------------------
    # A walk can survive this too; the point is that a fit does not even
    # notice, because the line is defined by the points that ARE there.
    gone = (3, 4, 9, 14, 15, 16, 22)
    check("frames the detector missed cost one inlier each, not the chain",
          on_ball(d3.fit_ascent_chains(flight(gone), 4)), NF - len(gone))

    # ---- THE CASE FROM #768: a flight inside a crowd -------------------
    dets = sorted(flight(gone) + clutter(14),
                  key=lambda d: (d["frame"], d["x"]))
    _walk = on_ball(d3.build_tracks(dets, 14.0, min_len=4, max_gap=15,
                                    max_span=60,
                                    corridor_px=d3.ASCENT_CORRIDOR_PX))
    _fit = on_ball(d3.fit_ascent_chains(dets, 4))
    check("the fit recovers the flight from a crowded cloud",
          _fit >= NF - len(gone) - 1, True)
    check("  and recovers more of it than the walk does",
          _fit > _walk, True)

    # ---- what it must NOT do ------------------------------------------
    # Pure noise has no line through it worth the name. A fit that
    # invents flights is worse than one that misses them, because the
    # gates downstream are what would have to catch it.
    _noise = d3.fit_ascent_chains(clutter(14), 4)
    _long = [c for c in _noise if len(c["points"]) >= NF // 2]
    check("it does not invent a long flight out of noise", _long, [])
    # AND NOTHING UNDER THE FLOOR, EVER. Two points are not evidence:
    # any two detections are collinear, and a pair of specks that
    # claims its points can block the flight that needed them. A
    # mutation keeping two-point fits survived the check above, because
    # that one only looked for LONG junk.
    check("  and nothing shorter than the floor comes back",
          [c for c in _noise if len(c["points"]) < 4], [])
    check("  whatever the floor is set to",
          [c for c in d3.fit_ascent_chains(flight(), 8)
           if len(c["points"]) < 8], [])
    check("  which can refuse a flight outright (control)",
          d3.fit_ascent_chains(flight()[:5], 8), [])

    # ---- what it must NOT offer --------------------------------------
    # ASSERTED ON THE RETURN, not through on_ball. The first version of
    # these checked whether a bad chain lay on the BALL's line, which it
    # never would — so a mutation that happily fitted descents and
    # diagonals sailed through both.
    falling = [{"frame": F0 + i, "x": BX, "y": int(100 + 15 * i),
                "area": 100} for i in range(NF)]
    check("a descending line yields no chain at all",
          d3.fit_ascent_chains(falling, 4), [])
    # Something crossing the picture rather than leaving it: rising, but
    # further across than up.
    diagonal = [{"frame": F0 + i, "x": int(300 + 30 * i),
                 "y": int(400 - 8 * i), "area": 100} for i in range(NF)]
    check("  nor does a shallow diagonal", d3.fit_ascent_chains(diagonal, 4),
          [])

    # One object, one chain — the same rule build_tracks works to. Tested
    # on the deduper directly: the seeding claims a fit's points as soon
    # as it is found, so overlapping fits barely arise in practice and a
    # broken dedupe went unnoticed through the whole-pipeline check.
    _a = flight()
    _overlap = [list(_a), _a[2:], _a[:-3]]
    check("overlapping fits of one object collapse to one",
          len(d3._dedupe_fits([list(x) for x in _overlap], True)), 1)
    check("  and two genuinely separate ones both survive (control)",
          len(d3._dedupe_fits(
              [list(_a), [{"frame": q["frame"], "x": q["x"] + 600,
                           "y": q["y"], "area": 100} for q in _a]], True)), 2)
    check("one flight yields one chain", len(d3.fit_ascent_chains(_a, 4)), 1)

    # ---- and a seed in the MIDDLE recovers both halves ----------------
    # The walk outward goes both ways. Seeding happens oldest-first, so
    # in a clean cloud the backward leg is never needed and dropping it
    # changed nothing measurable — until the flight's first points are
    # claimed by something else first, which is exactly the crowded
    # case this exists for.
    by_frame: dict = {}
    for d in _a:
        by_frame.setdefault(d["frame"], []).append(d)
    _mid = _a[len(_a) // 2]
    _grown = d3._grow_fit(by_frame, _mid, VX, VY, _mid["frame"], 7.0, 70)
    check("a seed in the middle of a flight recovers both ends",
          len(_grown), NF)
    check("  including frames BEFORE it (control)",
          _grown[0]["frame"] < _mid["frame"], True)

    # ---- THE REAL THING, from #768 -----------------------------------
    # Every point the considered table printed on 20 Oct, verbatim. The
    # sweep returned SIX overlapping fragments of this one flight: two
    # passed the gates as separate swings 40ms apart (9pts and 6pts),
    # and four were refused on `rise` for being too short. Field data
    # beats any fixture I can invent — five of mine failed to reproduce
    # a failure this one contains.
    #
    # Note (1370,393) appearing at BOTH f1550 and f1551, and (1378,313)
    # at f1554 and f1555: the detector reports the ball twice, a frame
    # apart, which is what defeats a dedupe that compares point
    # identity rather than geometry.
    real_rows = [
        [(1549, 1367, 416), (1550, 1370, 393), (1555, 1378, 313),
         (1556, 1379, 296), (1557, 1380, 278), (1559, 1384, 245),
         (1561, 1386, 215), (1563, 1391, 176), (1564, 1391, 163)],
        [(1547, 1362, 491), (1548, 1364, 465), (1549, 1366, 440),
         (1550, 1368, 416), (1551, 1370, 393), (1554, 1378, 313)],
        [(1550, 1370, 393), (1551, 1372, 371), (1552, 1373, 352),
         (1554, 1378, 313), (1555, 1378, 296), (1556, 1380, 278)],
        [(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
         (1549, 1367, 416), (1550, 1370, 393)],
        [(1555, 1374, 331), (1557, 1380, 278), (1558, 1384, 245),
         (1559, 1385, 231)],
        [(1563, 1387, 202), (1564, 1391, 176), (1565, 1391, 163),
         (1566, 1395, 138)],
    ]
    _seen, real = set(), []
    for _row in real_rows:
        for _f, _x, _y in _row:
            if (_f, _x, _y) not in _seen:
                _seen.add((_f, _x, _y))
                real.append({"frame": _f, "x": _x, "y": _y, "area": 100})
    real.sort(key=lambda d: (d["frame"], d["y"]))

    _out = d3.fit_ascent_chains(real, 4, refine=True)
    check("the real #768 detections make ONE chain, not six", len(_out), 1)
    _p = _out[0]["points"]
    check("  spanning the whole flight", (_p[0]["frame"], _p[-1]["frame"]),
          (1546, 1565))
    # The gates that refused every fragment, applied to the whole:
    _rise = _p[0]["y"] - _p[-1]["y"]
    _rate = _rise / (_p[-1]["frame"] - _p[0]["frame"]) * 60.0 / 1080.0
    _dens = len(_p) / (_p[-1]["frame"] - _p[0]["frame"] + 1)
    check("  rising far enough for the rise gate (142px)", _rise >= 142, True)
    check("  fast enough for the rate gate (0.7)", _rate >= 0.7, True)
    check("  dense enough for the sparse gate (0.35)", _dens >= 0.35, True)

    # ---- and the sweep asks for them ----------------------------------
    seen: list = []
    _real = d3.fit_ascent_chains

    def _spy(*a, **kw):
        seen.append(True)
        return _real(*a, **kw)

    import tempfile

    import cv2  # type: ignore
    import numpy as np

    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "f.mp4"
        vw = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"),
                             60.0, (640, 360))
        for f in range(20):
            im = np.full((360, 640, 3), 118, dtype="uint8")
            cv2.circle(im, (300, 300 - 8 * f), 4, (245, 245, 245), -1)
            vw.write(im)
        vw.release()
        d3.fit_ascent_chains = _spy
        try:
            on = d3.sweep_ascents(clip, 60.0, roi={"y": 0.95},
                                  gates={"ascent_fit_lines": 1})
            off = d3.sweep_ascents(clip, 60.0, roi={"y": 0.95},
                                   gates={"ascent_fit_lines": 0})
        finally:
            d3.fit_ascent_chains = _real
    check("the sweep runs the fit when the gate is on", len(seen), 1)
    check("  and reports which it did",
          (on.get("fit_lines"), off.get("fit_lines")), (True, False))
    # AND ITS CHAINS REACH THE GATES. Running the fit and then dropping
    # its results on the floor passed every check above — the same
    # "resolved but not used" hole, now on its fifth outing.
    #
    # BEFORE THE JOIN, because the join is the other half of this. The
    # fit's chain and the tracker's are the same flight, so
    # stitch_ascent_chains now merges them and n_tracks comes back the
    # same either way — which is the join working, not the fit being
    # dropped. n_stitched is how many it absorbed, so the two added
    # back together is what the gates were handed.
    def _found(r):
        return (r.get("n_tracks") or 0) + (r.get("n_stitched") or 0)

    check("the fit's chains are judged alongside the tracker's",
          _found(on) > _found(off), True)
    check("  and the sweep counts them", (on.get("n_fits") or 0) > 0, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
