#!/usr/bin/env python3
"""A chain that just stops, mid-flight.

Event 815: the sweep found the first seven frames of a ball leaving
the tee -- f3763 (1648,510) climbing to f3769 (1628,432) -- and ended
there. It passed rate, bend, density, straightness, tilt, backrun and
the hitting-area test. It failed exactly one gate, rise, at 78px
against the 142px that ASCENT_MIN_RISE_FRAC asks for at 1080p.

The flight carried on for another forty frames and about four hundred
pixels. The operator's own scan at 2x shows every one of them.

NOTHING WAS WRONG WITH THE CHAIN. The sweep stopped looking. And
because no chain existed above it, the stitcher had nothing to join
and fill_chain_holes had no hole to fill -- it re-reads gaps BETWEEN
points and has nothing to say about a chain that simply ends.

    python3 tools/chain_extend_test.py
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


W, H, N = 960, 540, 70
BX, Y0 = 700, 500.0
# A ball that SLOWS as it climbs, which is what a real one does and
# what a constant-velocity prediction has to tolerate.
STEP0, DECAY = 14.0, 0.97
# The frames the main detector is given. Past this the ball is still
# there and still bright; the fixture just hands the sweep a chain
# that stops, which is the situation being fixed.
#
# FIVE POINTS, to match event 815 — and, like it, a rise that falls
# short of ASCENT_MIN_RISE_FRAC while every other gate is satisfied.
# A longer stub clears the rise gate on its own and the test proves
# nothing.
FOUND_UNTIL = 6


def path():
    ys, y, v = [], Y0, STEP0
    for _ in range(N):
        ys.append(y)
        y -= v
        v *= DECAY
    return ys


YS = path()


# WHAT "FAINT" MEANS, and why it has to be this narrow: six grey
# levels over background, smeared over a frame's travel, against three
# levels of noise. A three-frame AND does not lose a clean blob at
# all. What it loses is a marginal one, near threshold in both diffs
# and only having to fail either -- which is what a ball high in the
# band looks like.
# Eight, not six, and the difference is measured: at six the ball is
# below what ANY pass follows reliably (two-frame sees 15 frames of 40,
# three-frame sees 1, and the extension gets six points before a chunk
# comes back empty). At eight the separation is the real one --
# three-frame 12 frames, two-frame 40 -- which is the regime a ball
# high in the band is actually in.
FAINT_CONTRAST, NOISE, SMEAR = 8, 3, 12
# A decoy this far to the side is INSIDE the crop the extension reads
# (ASCENT_FILL_PAD_PX, 48) and outside what it will accept
# (ASCENT_EXTEND_TOL_PX, 24).
DECOY_DX = 36


def clip(path_, faint=False, decoy=False, falling=False, dropout=None,
         ball_gap=None, busy=0):
    """The flight, optionally with the things that catch a sloppy read.

    `faint`   — the ball at the contrast where a three-frame AND loses
                it and a two-frame pass does not.
    `decoy`   — a BRIGHTER blob beside the ball's path, inside the
                crop and outside the tolerance.
    `falling` — a second object descending, for the chain that should
                never be followed.
    `dropout` — unused now; `ball_gap` of one frame does the job and
                does it honestly. A two-frame difference lights a ball
                that VANISHES at the position it just left, so a
                one-frame gap produces a detection at the previous
                point: inside the tolerance, and not rising.
    """
    import numpy as np
    import cv2

    rng = np.random.default_rng(4)
    vw = cv2.VideoWriter(str(path_), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for f in range(N):
        im = np.full((H, W, 3), 110, dtype="int16")
        im += rng.normal(0, NOISE if faint else 2,
                         (H, W, 3)).astype("int16")
        im = np.clip(im, 0, 255).astype("uint8")
        y = YS[f]
        _v = 110 + FAINT_CONTRAST if faint else 240
        for i in range(busy):
            _bx = 60 + (i * 41) % 380
            _by = 50 + (i * 59) % 420
            cv2.circle(im, (_bx + (3 if (f + i) % 2 else -3), _by), 9,
                       (250, 250, 250), -1)
        if ball_gap and ball_gap[0] <= f <= ball_gap[1]:
            # The ball is simply not detected here. Whatever else is in
            # the box is the only candidate.
            pass
        elif f == dropout:
            # The ball is not here this frame; something at the last
            # position is.
            _py = YS[f - 1]
            if 6 < _py < H - 6:
                cv2.circle(im, (BX, int(_py)), 3, (240, 240, 240), -1)
        elif 6 < y < H - 6:
            if faint:
                cv2.line(im, (BX, int(y)), (BX, int(y + SMEAR)),
                         (_v, _v, _v), 2)
            else:
                cv2.circle(im, (BX, int(y)), 3, (_v, _v, _v), -1)
        if decoy and 6 < y < H - 6:
            cv2.circle(im, (BX + DECOY_DX, int(y) + 3), 5,
                       (250, 250, 250), -1)
        if falling:
            _fy = 60 + 11 * f
            if 6 < _fy < H - 6:
                cv2.circle(im, (BX - 220, int(_fy)), 4,
                           (245, 245, 245), -1)
        vw.write(im)
    vw.release()


def stub(n):
    """The chain as the sweep had it: the first `n` frames and no more."""
    return [{"frame": f, "x": BX, "y": int(YS[f]), "area": 60}
            for f in range(1, n)]


def test_it_follows_the_flight_to_the_top():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        pts = stub(FOUND_UNTIL)
        before = pts[0]["y"] - pts[-1]["y"]
        more = d3.extend_chain(p, pts, max_frames=60, sens=3)
    check("the stub is the short chain we were handed", len(pts), 5)
    check("and it does not rise far", before < 100, True)
    check("the extension finds more of it", len(more) > 10, True)
    _frames = [q["frame"] for q in more]
    check("in order, past the end of the stub",
          _frames == sorted(_frames) and _frames[0] > pts[-1]["frame"],
          True)
    check("and all of it is on the ball's line",
          all(abs(q["x"] - BX) <= 12 for q in more), True)
    after = pts[0]["y"] - more[-1]["y"]
    check("the rise is now a flight, not a stub", after > 300, True)
    # THE WHOLE POINT, in the gate's own terms: 0.132 of frame height.
    _need = 0.132 * H
    check("control, before extending it fails the rise gate",
          before >= _need, False)
    check("and after extending it passes", after >= _need, True)


def test_it_keeps_up_with_a_decelerating_ball():
    """The prediction is constant-velocity; the ball is not.

    Re-fitting the heading each chunk is what keeps the error inside
    the tolerance. Without it the guess runs ahead of a slowing ball
    and the matches stop.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=60, sens=3)
    # The ball really does slow down over the run that came back.
    _steps = [abs(more[i + 1]["y"] - more[i]["y"])
              for i in range(len(more) - 1)
              if more[i + 1]["frame"] - more[i]["frame"] == 1]
    check("the fixture really decelerates",
          _steps[0] - _steps[-1] >= 2, True)
    check("and it was followed anyway", len(more) > 10, True)


def test_it_stops_when_the_ball_does():
    """Not a licence to invent points off the top of the frame."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=200,
                               sens=3)
    check("it does not run past the top of frame",
          all(q["y"] >= 0 for q in more), True)
    check("nor past the end of the clip",
          all(q["frame"] < N for q in more), True)


def test_an_empty_sky_extends_nothing():
    """A chain pointing at nothing comes back with nothing.

    If this invented points, every junk chain in the band would grow
    until it passed the rise gate, which is the opposite of the job.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        # Same heading, 300px to the left of the ball: empty sky.
        off = [{"frame": f, "x": BX - 300, "y": int(YS[f]), "area": 60}
               for f in range(1, FOUND_UNTIL)]
        more = d3.extend_chain(p, off, max_frames=60, sens=3)
    check("nothing comes back", more, [])


def test_a_chain_going_nowhere_is_not_followed():
    """Only a rising thing gets chased."""
    flat = [{"frame": f, "x": BX, "y": 300, "area": 60}
            for f in range(1, 8)]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        check("a level chain is left alone",
              d3.extend_chain(p, flat, max_frames=60, sens=3), [])
        falling = [{"frame": f, "x": BX, "y": 100 + 12 * f, "area": 60}
                   for f in range(1, 8)]
        check("and so is a falling one",
              d3.extend_chain(p, falling, max_frames=60, sens=3), [])


def test_the_budget_is_shared_and_spent():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flight.mp4"
        clip(p)
        b = [2]
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=60,
                               sens=3, budget=b)
    check("it stops when the budget runs out", b[0], 0)
    check("control, and two chunks is less than the whole flight",
          len(more) <= 2 * d3.ASCENT_EXTEND_CHUNK_FRAMES, True)


def test_it_reads_two_frame_because_three_loses_a_faint_ball():
    """The sweep's own detector is what could not see this.

    The sweep ANDs the diff against the frame before and the frame
    after; the extension does not. On a ball at the contrast a real
    one reaches high in the band, that is the difference between
    following the flight and stopping where the sweep stopped.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p, faint=True)
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=60, sens=3)
        # CONTROL: the same region, the same threshold, three-frame.
        _f0 = stub(FOUND_UNTIL)[-1]["frame"]
        _three = d3.detect_movers_by_diff(
            p, _f0, _f0 + 30, sens=3,
            roi=(BX - 60, 0, 120, H), per_frame=8, three_frame=True,
            merge_px=d3.ASCENT_MERGE_PX)
        _two = d3.detect_movers_by_diff(
            p, _f0, _f0 + 30, sens=3,
            roi=(BX - 60, 0, 120, H), per_frame=8, three_frame=False,
            merge_px=d3.ASCENT_MERGE_PX)
    check("the faint flight is followed", len(more) >= 20, True)
    check("control, two-frame sees more of it than three-frame",
          len({d["frame"] for d in _two})
          > len({d["frame"] for d in _three}), True)


def test_a_decoy_beside_the_path_is_not_taken():
    """The crop is generous; what gets accepted is not.

    THE DECOY ONLY MATTERS WHEN THE BALL IS MISSING. With both in the
    box the ball is nearer the prediction and wins on distance alone,
    so the tolerance never has to do anything -- an earlier version of
    this test "passed" for exactly that reason and the check it was
    meant to defend could be deleted without it noticing.

    So: the ball goes undetected for a stretch, and for those frames
    the blob 36px to the side is the ONLY candidate. Inside the box
    the extension reads, outside what it accepts.
    """
    _gap = (FOUND_UNTIL + 2, FOUND_UNTIL + 10)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "decoy.mp4"
        clip(p, decoy=True, ball_gap=_gap)
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=60, sens=3)
    check("nothing came off the decoy",
          max((abs(q["x"] - BX) for q in more), default=0) <= 12, True)
    check("control, the decoy is inside the crop",
          DECOY_DX <= d3.ASCENT_FILL_PAD_PX, True)
    check("control, and outside the tolerance",
          DECOY_DX > d3.ASCENT_EXTEND_TOL_PX, True)
    # CONTROL: the decoy really is the only thing there during the gap,
    # so the tolerance is what refused it rather than the ball
    # out-competing it.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "decoy2.mp4"
        clip(p, decoy=True, ball_gap=_gap)
        _in_gap = d3.detect_movers_by_diff(
            p, _gap[0], _gap[1], sens=3,
            roi=(BX - 60, 0, 160, H), per_frame=8, three_frame=False,
            merge_px=d3.ASCENT_MERGE_PX)
    check("control, only the decoy is there during the gap",
          _in_gap and all(q["x"] > BX + 20 for q in _in_gap), True)


def test_it_reads_a_crop_and_not_the_frame():
    """Spied, because the result alone cannot tell.

    On a quiet synthetic clip, reading the whole frame finds the same
    ball and the test passes either way. What the crop buys is that
    the ball is not competing with everything else moving -- and the
    only honest way to assert it is to look at what was asked for.
    """
    seen: list = []
    _real = d3.detect_movers_by_diff

    def spy(*a, **kw):
        seen.append(kw.get("roi"))
        return _real(*a, **kw)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "crop.mp4"
        clip(p)
        d3.detect_movers_by_diff = spy
        try:
            d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=20, sens=3)
        finally:
            d3.detect_movers_by_diff = _real
    check("it read something", len(seen) > 0, True)
    check("every read was cropped", all(r is not None for r in seen), True)
    check("and none of them was the whole frame",
          all(r[2] < W and r[3] < H for r in seen), True)
    # CONTROL: the crops really are small -- a box the size of the
    # frame would satisfy "not None" and buy nothing.
    check("control, and they are a fraction of it",
          max(r[2] * r[3] for r in seen) < 0.25 * W * H, True)


def test_a_falling_chain_is_not_followed_even_with_prey():
    """The guard, against something it could actually have chased.

    A chain pointing DOWN at an object that is genuinely descending.
    Without the rising test the extension follows it happily and
    reports a descent as an ascent.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "falling.mp4"
        clip(p, falling=True)
        down = [{"frame": f, "x": BX - 220, "y": 60 + 11 * f, "area": 60}
                for f in range(1, FOUND_UNTIL)]
        # SPIED, because the per-match rising test would refuse every
        # point of a descent anyway -- so "came back empty" passes with
        # or without the heading check, and the two guards cover for
        # each other. What only the heading check does is refuse to
        # SPEND A SEEK on a chain that was never going up.
        seen: list = []
        _real = d3.detect_movers_by_diff

        def spy(*a, **kw):
            seen.append(1)
            return _real(*a, **kw)

        d3.detect_movers_by_diff = spy
        try:
            got = d3.extend_chain(p, down, max_frames=60, sens=3)
        finally:
            d3.detect_movers_by_diff = _real
    check("a descending chain comes back empty", got, [])
    check("and was not read at all", len(seen), 0)


def test_a_match_that_is_not_rising_is_refused():
    """A ball that vanishes for a frame lights where it just was.

    A two-frame difference does not care which way a thing changed, so
    a ball that disappears produces a detection at the position it
    left. Near the apex a step is shorter than the tolerance -- 11px
    against 24 on this fixture -- so that detection is INSIDE the
    window and is the only candidate on the frame.

    Nothing else refuses it. The distance test lets it through, and
    without the rising test the chain takes a point level with the one
    before it and carries on from there.
    """
    _drop = FOUND_UNTIL + 3
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "drop.mp4"
        clip(p, ball_gap=(_drop, _drop))
        more = d3.extend_chain(p, stub(FOUND_UNTIL), max_frames=60, sens=3)
        # CONTROL: something really is detected on that frame, and it
        # really is inside the tolerance -- so the rising test is what
        # refused it, not the distance.
        _there = d3.detect_movers_by_diff(
            p, _drop - 1, _drop, sens=3, roi=(BX - 60, 0, 120, H),
            per_frame=8, three_frame=False, merge_px=d3.ASCENT_MERGE_PX)
    _hit = [q for q in _there if q["frame"] == _drop
            and abs(q["x"] - BX) <= 12]
    check("control, the vanishing ball is detected where it was",
          len(_hit) > 0, True)
    check("control, at the position it left, not the one it was going to",
          _hit and abs(_hit[0]["y"] - YS[_drop - 1]) <= 3, True)
    check("control, which is inside the tolerance",
          abs(YS[_drop - 1] - YS[_drop]) < d3.ASCENT_EXTEND_TOL_PX, True)
    # WHAT IS ACTUALLY GUARANTEED, and it is not "that frame is
    # skipped". The walk reads in chunks, so the last ACCEPTED point is
    # often two frames back rather than one -- and against that, the
    # vanishing ball's detection is still rising and is taken, one
    # frame late. Harmless, and not what the guard is for.
    #
    # The guard's promise is narrower: no accepted point ever sits at
    # or below the one before it. That is what is asserted.
    check("the flight is followed past the gap", len(more) >= 8, True)
    check("and every point rises on the one before",
          all(more[i + 1]["y"] < more[i]["y"]
              for i in range(len(more) - 1)), True)


for fn in (test_it_follows_the_flight_to_the_top,
           test_it_reads_two_frame_because_three_loses_a_faint_ball,
           test_a_decoy_beside_the_path_is_not_taken,
           test_it_reads_a_crop_and_not_the_frame,
           test_a_falling_chain_is_not_followed_even_with_prey,
           test_a_match_that_is_not_rising_is_refused,
           test_it_keeps_up_with_a_decelerating_ball,
           test_it_stops_when_the_ball_does,
           test_an_empty_sky_extends_nothing,
           test_a_chain_going_nowhere_is_not_followed,
           test_the_budget_is_shared_and_spent):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
