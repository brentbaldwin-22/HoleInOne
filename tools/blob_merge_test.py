#!/usr/bin/env python3
"""One ball, one point: does the smear get collapsed before tracking?

THE OPERATOR'S OWN OBSERVATION, which no simulation here had
reproduced:

    "the moving ball creates a 2 or 3 spots/points per frame. so the
     trailing dot in one frame could be lower than the leading dot in
     the previous frame (making it look like it's decelerating)."

A ball crossing 26px during one exposure is a smear, and a threshold
across a smear breaks into components. Each is reported at its own
centroid, so one object arrives three deep -- and then the tracker has
three candidates a frame to link wrongly, y stops being monotonic
between adjacent frames, and each ball costs three of the per-frame
keep instead of one.

    python3 tools/blob_merge_test.py
"""
from __future__ import annotations

import sys
import tempfile
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


# ---------------------------------------------------------------- unit

def test_collapses_a_smear():
    """Three components of one smear come back as one point."""
    # A 22px smear in three pieces, all the same size, plus one real
    # other object well clear of it.
    hits = [(100, 700.0, 500.0), (100, 700.0, 511.0), (100, 700.0, 522.0),
            (100, 300.0, 500.0)]
    got = d3._merge_blobs(hits, d3.ASCENT_MERGE_PX)
    check("a smear and a stranger make two detections", len(got), 2)
    smear = [h for h in got if h[1] > 500][0]
    check("and the smear reports its middle", round(smear[2]), 511)
    # CONTROL: the same blobs with merging off stay four.
    check("control, merging off", len(d3._merge_blobs(hits, 0.0)), 4)


def test_area_is_the_biggest_member_not_the_sum():
    """Because prefer_area is calibrated on one component's size.

    Summing would make every merged ball measure like a tree and lose
    the per-frame cut it was added to win.
    """
    hits = [(90, 700.0, 500.0), (101, 700.0, 509.0), (74, 700.0, 518.0)]
    got = d3._merge_blobs(hits, d3.ASCENT_MERGE_PX)
    check("one detection", len(got), 1)
    check("area is the largest component", got[0][0], 101)
    # CONTROLS: the two values this must not be.
    check("control, not the sum", got[0][0] == 90 + 101 + 74, False)
    check("control, not the mean", got[0][0] == (90 + 101 + 74) // 3, False)


def test_the_point_is_the_centre_of_mass():
    """Weighted by area, not the midpoint of the extremes.

    A streak is not evenly bright end to end, and the big component is
    where most of the ball's pixels are. Averaging the centroids
    unweighted lets a one-pixel speck at one end pull the reported
    position as hard as the body of the smear.
    """
    hits = [(300, 700.0, 500.0), (300, 700.0, 506.0), (4, 700.0, 518.0)]
    got = d3._merge_blobs(hits, d3.ASCENT_MERGE_PX)
    check("one detection", len(got), 1)
    # 300*500 + 300*506 + 4*518 over 604 -> 503.1
    check("the speck barely moves it", round(got[0][2], 1), 503.1)
    # CONTROL: unweighted this would be 508, five pixels further down --
    # a fifth of a step, on every frame the speck appears.
    check("control, unweighted would be",
          round(sum(h[2] for h in hits) / 3, 1), 508.0)


def test_does_not_chain_across_a_treeline():
    """Leader clustering, not single-link.

    Single-link would walk a row of blobs each merge_px from the next
    into one cluster spanning the frame, whose centroid is nowhere in
    particular.
    """
    # Eleven blobs in a line, one pixel inside the reach of the next:
    # every one a neighbour, the row 190px long end to end.
    _gap = d3.ASCENT_MERGE_PX - 1.0
    hits = [(200, 400.0 + _gap * i, 300.0) for i in range(11)]
    got = d3._merge_blobs(hits, d3.ASCENT_MERGE_PX)
    check("the row does not become one blob", len(got) > 1, True)
    # CONTROL: single-link on this row WOULD collapse it, so the
    # assertion above is testing the clustering rule and not the input.
    check("control, single-link would have",
          _single_link(hits, d3.ASCENT_MERGE_PX), 1)
    # And no cluster reaches further than a ball's own smear.
    check("every cluster stays local",
          max(_extent(hits, got, d3.ASCENT_MERGE_PX), default=0.0)
          <= 2 * d3.ASCENT_MERGE_PX, True)


def _single_link(hits, px):
    """What single-link would have returned, for the control above."""
    groups: list = []
    for h in hits:
        near = [g for g in groups
                if any(abs(h[1] - q[1]) <= px and abs(h[2] - q[2]) <= px
                       for q in g)]
        if near:
            merged = [h] + [q for g in near for q in g]
            groups = [g for g in groups if g not in near] + [merged]
        else:
            groups.append([h])
    return len(groups)


def _extent(hits, merged, px):
    """How far each merged point sits from the inputs it claims."""
    out = []
    for _a, mx, my in merged:
        own = [h for h in hits
               if abs(h[1] - mx) <= 2 * px and abs(h[2] - my) <= 2 * px]
        if own:
            out.append(max(max(abs(h[1] - mx), abs(h[2] - my))
                           for h in own))
    return out


def test_a_ghost_one_step_away_survives():
    """The two-sided constraint on the default.

    A two-frame diff lights the ball where it is AND where it was. If
    the default merged those, every measured step would halve and the
    rate gate would refuse a real flight. The ghost drop is what
    removes it; this only has to not average it in.
    """
    step = 26.0
    ball = [(100, 700.0, 500.0), (100, 700.0, 509.0)]
    ghost = [(100, 700.0, 500.0 + step), (100, 700.0, 509.0 + step)]
    got = d3._merge_blobs(ball + ghost, d3.ASCENT_MERGE_PX)
    check("ball and ghost stay two things", len(got), 2)
    check("neither lands between them",
          all(abs(g[2] - (504.5 + step / 2)) > 6.0 for g in got), True)


# ------------------------------------------------------------ the clip

W, H, N = 960, 540, 20
BX, Y0, VY = 700, 510.0, -26.0   # 26px a frame, like a struck ball
# HOW LONG THE STREAK IS. Drawn as a line and not as dots, because the
# pieces are not in the scene -- they are what three-frame differencing
# leaves of a continuous streak when the frame before and the frame
# after cancel parts of it. Slightly longer than the step, so adjacent
# frames' streaks overlap: the condition the operator described, where
# the trailing end of one frame sits below the leading end of the last.
SMEAR = 31


def clip(path):
    import numpy as np
    import cv2

    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for f in range(N):
        im = np.full((H, W, 3), 115, dtype="uint8")
        y = Y0 + VY * f
        cv2.line(im, (BX, int(y)), (BX, int(y + SMEAR)),
                 (248, 248, 248), 3)
        vw.write(im)
    vw.release()


def per_frame_counts(dets):
    by_f: dict = {}
    for d in dets:
        by_f.setdefault(d["frame"], []).append(d)
    return by_f


def test_the_detector_sees_three_and_reports_one():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "smear.mp4"
        clip(p)
        raw = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                       three_frame=True)
        one = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                       three_frame=True,
                                       merge_px=d3.ASCENT_MERGE_PX)
        _raw_n = per_frame_counts(raw)
        _one_n = per_frame_counts(one)
        # The fixture has to actually smear, or there is nothing to
        # merge and the rest of this proves nothing.
        check("the fixture smears",
              max((len(v) for v in _raw_n.values()), default=0) >= 3, True)
        check("merged, one point a frame",
              max((len(v) for v in _one_n.values()), default=0), 1)
        check("and no frame is lost",
              len(_one_n) >= len(_raw_n), True)

        # WHAT IT COSTS THE TRACKER. Three candidates a frame means a
        # link can go leading-to-trailing, which reads as a step a
        # third of the real one.
        _rt = d3.build_tracks(raw, 14.0, min_len=4, max_gap=3,
                              max_span=70, corridor_px=10.0)
        _ot = d3.build_tracks(one, 14.0, min_len=4, max_gap=3,
                              max_span=70, corridor_px=10.0)
        _rl = max((len(t["points"]) for t in _rt), default=0)
        _ol = max((len(t["points"]) for t in _ot), default=0)
        check("the merged chain is the whole flight",
              _ol >= len(_one_n) - 1, True)
        check("and it is no shorter than the unmerged one",
              _ol >= _rl, True)
        # THE STEPS, which is the claim about deceleration. Merged,
        # every step is the ball's own 26px. Unmerged, at least one is
        # not: the tracker had three candidates to acquire on and took
        # a link from one frame's leading end to the next's trailing
        # one, which reads as a ball that moved a third as far.
        check("merged, every step is the ball's own",
              _steps_off_by_more_than(_ot, 26, 3), 0)
        check("control, unmerged they are not",
              _steps_off_by_more_than(_rt, 26, 3) > 0, True)


def _steps_off_by_more_than(tracks, want, tol):
    """How many links in the longest chain are not one frame of flight."""
    if not tracks:
        return -1
    best = max((t["points"] for t in tracks), key=len)
    return sum(1 for i in range(len(best) - 1)
               if abs(abs(best[i + 1]["y"] - best[i]["y"]) - want) > tol)


def test_one_ball_costs_one_slot():
    """Which is only true if the merge runs BEFORE the per-frame cut.

    Each component of a smear is its own candidate for the `per_frame`
    keep, so one ball eats three slots and a cap sized for the scene
    binds three times as early as it reads. Merging after the cut would
    report the same one point a frame and leave that unchanged.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "smear.mp4"
        clip(p)
        raw: dict = {}
        one: dict = {}
        d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=2,
                                 three_frame=True, stats=raw)
        d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=2,
                                 three_frame=True,
                                 merge_px=d3.ASCENT_MERGE_PX, stats=one)
        check("control, unmerged the cap binds",
              int(raw.get("capped") or 0) > 0, True)
        check("merged, it does not", int(one.get("capped") or 0), 0)


def test_the_default_is_on():
    """Because a knob nobody sets is a knob that does nothing.

    Four of these have shipped reported-but-unused or off-by-default
    and been re-asked about from the field.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "smear.mp4"
        clip(p)
        out = d3.sweep_ascents(p, 60.0, sens=3)
    check("the sweep merges without being asked",
          out.get("merge_px"), d3.ASCENT_MERGE_PX)
    check("and the default is not zero", d3.ASCENT_MERGE_PX > 0, True)


def test_the_sweep_passes_it():
    """The hole that has opened four times: reported but not used."""
    seen: dict = {}
    _real = d3.detect_movers_by_diff

    def spy(*a, **kw):
        seen["merge_px"] = kw.get("merge_px")
        return _real(*a, **kw)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "smear.mp4"
        clip(p)
        d3.detect_movers_by_diff = spy
        try:
            out = d3.sweep_ascents(p, 60.0, sens=3,
                                   gates={"ascent_merge_px": 22.0})
        finally:
            d3.detect_movers_by_diff = _real
    check("the sweep hands it to the detector", seen.get("merge_px"), 22.0)
    check("and reports what it used", out.get("merge_px"), 22.0)
    check("and how many blobs it absorbed",
          (out.get("merged_away") or 0) > 0, True)


for fn in (test_collapses_a_smear,
           test_area_is_the_biggest_member_not_the_sum,
           test_the_point_is_the_centre_of_mass,
           test_does_not_chain_across_a_treeline,
           test_a_ghost_one_step_away_survives,
           test_the_detector_sees_three_and_reports_one,
           test_one_ball_costs_one_slot,
           test_the_default_is_on,
           test_the_sweep_passes_it):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
