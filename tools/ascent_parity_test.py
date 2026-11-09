#!/usr/bin/env python3
"""The column search and the whole-band sweep are the SAME search.

THE CLAIM THAT WAS NOT TRUE. `find_ascents` is described in its own
docstring, in the produce report and in a commit message as "the same
search the whole-band sweep runs -- same detector, same gates,
narrowed to the column the ball crosses". It was not, in five ways,
and on a real clip the sweep found the ball as a clean chain while
the column search came back with ZERO TRACKS over the same footage:

    sensitivity   2, where the sweep runs 3
    per frame     30 kept, where the sweep keeps 40
    differencing  three-frame, where the sweep stopped using it
    ghost/merge/prefer_area   not passed at all
    gates         module defaults, not the hole's

The third is the one the codebase itself warns about, in
sweep_ascents: ANDing the diffs either side of a frame "also refused
faint balls, which is what a ball is by the top of its climb, and it
is off by default now".

A NARROWED SEARCH THAT IS ALSO A WEAKER ONE IS NOT A NARROWED SEARCH.
This checks the two read their settings from the same place, so the
claim cannot quietly stop being true again.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    from app.services import debug3 as d3

    # ── BOTH TAKE THE HOLE'S GATES ──────────────────────────────────
    ok("find_ascents accepts the hole's gates",
       "gates" in inspect.signature(d3.find_ascents).parameters, True)
    ok("  as sweep_ascents does",
       "gates" in inspect.signature(d3.sweep_ascents).parameters, True)

    # ── AND READ THE SAME KEYS OUT OF THE SAME TABLE ────────────────
    _src = inspect.getsource(d3.find_ascents)
    _swp = inspect.getsource(d3.sweep_ascents)
    ok("the column search resolves the gate table",
       "resolve_gates(gates)" in _src, True)
    for _k in ("ascent_per_frame", "ascent_three_frame",
               "ascent_ghost_px", "ascent_prefer_area",
               "ascent_merge_px", "ascent_corridor_px"):
        ok("  both read %s" % _k,
           (_k in _src, _k in _swp), (True, True))

    # ── NONE OF THE OLD HARD-CODED SETTINGS SURVIVE ─────────────────
    # Each of these is one of the five differences, spelled the way it
    # was spelled when it was wrong.
    ok("sensitivity is no longer hard-coded to 2",
       "sens=2" in _src, False)
    ok("the per-frame cap is no longer the descent's 30",
       "per_frame=DESCENT_DIFF_PER_FRAME" in _src, False)
    ok("three-frame differencing is no longer forced on",
       "three_frame=True" in _src, False)
    ok("the corridor is no longer a bare 14.0",
       "build_tracks(dets, 14.0" in _src, False)

    # ── AND THE PICTURE CAN SAY WHICH STAGE CAME BACK EMPTY ─────────
    # "0 chains considered" cannot distinguish a band with no ball in
    # it from a detector that emitted nothing, and those have
    # opposite fixes. The sweep's map has always printed the funnel;
    # this one prints it now too.
    ok("the column search reports its detection count",
       "n_dets" in _src, True)
    ok("  and its track count", "n_tracks" in _src, True)
    ok("  and which differencing it used", "three_frame" in _src, True)

    # ── IT STILL RUNS, AND STILL RETURNS THE OLD SHAPE ──────────────
    _r = d3.find_ascents(Path("/nonexistent.mp4"), 30.0, 10, (100, 100))
    ok("a missing clip is a reason, not a raise", _r["ok"], False)
    for _k in ("points", "n_points", "rise_px", "rate", "reason",
               "n_dets", "n_tracks"):
        ok("  and %s is still reported" % _k, _k in _r, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
