#!/usr/bin/env python3
"""The hole is a detection hole, not a gap in the flight.

The operator zoomed to 2.7x over f1572-f1579 -- the nine frames the
sweep came back empty on -- scanned, and got the ball on every one of
them. So the points past the hole are the same ball, and the sweep was
refusing a real flight because it could not see through its own
detector.

WHY THE SWEEP MISSES THEM. It runs a THREE-frame difference: a blob
has to clear threshold in the diff against the frame before AND the
frame after, at the same pixels. That is the right default -- it is
what kills the "where it was" ghost -- but it is strictly harder than
a two-frame pass, and a ball high in the band is faint. The operator's
own scan is two-frame in a crop, which is why it finds them.

So: re-read each hole the same way, in a box around the line between
its two ends.

    python3 tools/hole_fill_test.py
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


W, H, N = 960, 540, 56
BX, Y0, VY = 700, 500.0, -12.0
# The frames where the ball goes faint, as it does high in the band.
FAINT = range(12, 28)
# WHAT "FAINT" HAS TO MEAN HERE. Six grey levels over the background,
# smeared over a frame's travel, against three levels of sensor noise.
#
# It is a narrow band on purpose, and the honest finding: a three-frame
# AND does not lose a clean faint blob at all -- tried at six contrast
# and noise settings, it kept every one. What it loses is a MARGINAL
# one, where the signal is near threshold in both diffs and only has to
# fail either. That is what a ball looks like high in the band, and it
# is the regime the operator's zoom-and-scan is reaching into.
CONTRAST, NOISE, SMEAR = 6, 3, 12


def clip(path, decoy: bool = False, busy: int = 0):
    """The flight, optionally with company.

    `decoy`: a second faint mover 28px to the side -- INSIDE the crop
    the re-read takes (48px) and outside what it will accept (16px).
    This is what separates "look in a box" from "take what is in the
    box", and a treeline is full of them.

    `busy`: bright movers elsewhere in the frame, which is what the
    crop is for. The re-read keeps eight blobs a frame, largest first,
    and a faint ball loses all eight slots to these if the read is not
    cropped to where the ball is predicted to be.
    """
    import numpy as np
    import cv2

    rng = np.random.default_rng(3)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for f in range(N):
        im = np.full((H, W, 3), 112, dtype="int16")
        im += rng.normal(0, NOISE, (H, W, 3)).astype("int16")
        im = np.clip(im, 0, 255).astype("uint8")
        for i in range(busy):
            _bx = 80 + (i * 37) % 420
            _by = 60 + (i * 53) % 400
            cv2.circle(im, (_bx + (2 if (f + i) % 2 else -2), _by), 9,
                       (235, 235, 235), -1)
        y = Y0 + VY * f
        if 6 < y < H - 6:
            _v = 112 + CONTRAST if f in FAINT else 248
            cv2.line(im, (BX, int(y)), (BX, int(y + SMEAR)),
                     (_v, _v, _v), 2)
            if decoy:
                # BIGGER THAN THE BALL, which is what makes this a test
                # of anything. The detector hands a frame's blobs back
                # LARGEST FIRST, and the largest thing beside a faint
                # ball is never the ball -- the same inversion that
                # made `prefer_area` necessary over a whole band. A
                # decoy the same size as the ball sorts behind it by
                # luck and "take the first one" passes.
                cv2.line(im, (BX + 28, int(y) - 6),
                         (BX + 28, int(y + SMEAR) - 6), (_v, _v, _v), 5)
        vw.write(im)
    vw.release()


def seen_frames(dets):
    return {int(d["frame"]) for d in dets}


def test_the_three_frame_pass_is_what_loses_them():
    """The fixture has to reproduce the failure or it proves nothing."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        three = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                         three_frame=True)
        two = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                       three_frame=False)
    def _near(ds):
        return {int(d["frame"]) for d in ds if abs(d["x"] - BX) < 25}

    _lost = [f for f in FAINT if f not in _near(three)]
    _lost2 = [f for f in FAINT if f not in _near(two)]
    check("the three-frame pass loses most of the faint run",
          len(_lost), 12)
    # CONTROL: the same clip, the same threshold, the same keep --
    # two-frame instead of the AND, which is the one thing changed. It
    # does not get all sixteen either; at this contrast nothing does.
    # It gets two thirds of what the AND throws away, and that is the
    # whole claim.
    check("control, two-frame loses fewer", len(_lost2), 6)
    check("control, and what it keeps is a superset",
          set(_lost2) <= set(_lost), True)


def test_the_re_read_finds_what_the_sweep_missed():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        dets = d3.detect_movers_by_diff(p, 0, N - 1, sens=3, per_frame=40,
                                        three_frame=True,
                                        merge_px=d3.ASCENT_MERGE_PX)
        chain = sorted(
            (d for d in dets if abs(d["x"] - BX) < 40),
            key=lambda d: int(d["frame"]))
        chain = d3._longest_rising(chain, 60)
        _holes = [(a, b) for a, b in zip(chain, chain[1:])
                  if int(b["frame"]) - int(a["frame"]) > 1]
        check("the chain has a hole in it", len(_holes) > 0, True)
        got = d3.fill_chain_holes(p, chain, 15, sens=3)
    _ff = seen_frames(got)
    # NOT EVERY FRAME, AND IT DOES NOT NEED TO BE. At this contrast the
    # two-frame pass does not get all nine either -- and the operator's
    # own scan over f1572-f1579 came back with four of eight. What
    # matters is not how many: it is that what is left is a run of
    # SHORT holes instead of one long one, which is the only thing the
    # gap gate was ever refusing.
    check("it recovers what a two-frame pass can see",
          sorted(_ff), [15, 16, 19, 20, 22, 25])
    check("and they are on the ball's own line",
          all(abs(q["x"] - BX) <= 12 for q in got), True)
    # AND IN THE RIGHT PLACE. Predicting from the start of the hole
    # rather than across it drifts a step a frame, which is how a
    # re-read fills a hole with the wrong points and still reports a
    # number. The ball is reported at the middle of its own smear.
    _by_f = {int(q["frame"]): q for q in got}
    check("and where the ball actually was",
          all(abs(_by_f[f]["y"] - (Y0 + VY * f + SMEAR / 2)) <= 12
              for f in _ff), True)
    # THE WHOLE POINT, in one line: the hole the gates refused is gone.
    _after = sorted(set(int(q["frame"]) for q in chain) | _ff)
    check("no hole longer than the gates keep",
          max(b - a for a, b in zip(_after, _after[1:]))
          <= d3.ASCENT_MAX_STEP_FRAMES, True)
    # CONTROL: before the re-read there was one.
    _before = sorted(int(q["frame"]) for q in chain)
    check("control, before the re-read there was",
          max(b - a for a, b in zip(_before, _before[1:]))
          > d3.ASCENT_MAX_STEP_FRAMES, True)


def test_it_only_looks_where_the_ball_should_be():
    """A box around the segment, not the whole frame.

    The reason this is cheap AND safe: inside a box that size the only
    thing moving along that line is the ball, so a two-frame pass can
    be used without the ghosts it would bring over a whole band.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        # A hole whose ends are nowhere near the ball.
        off = [{"frame": 14, "x": 120, "y": 400, "area": 100},
               {"frame": 22, "x": 140, "y": 300, "area": 100}]
        got = d3.fill_chain_holes(p, off, 15, sens=3)
    check("nothing comes back from empty sky", got, [])


def test_the_budget_is_spent_once():
    """Each hole is a seek and a decode; a band of junk has plenty."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        pts = [{"frame": f, "x": BX, "y": int(Y0 + VY * f), "area": 100}
               for f in (0, 4, 8, 12, 16, 20, 24)]
        budget = [2]
        d3.fill_chain_holes(p, pts, 15, sens=3, budget=budget)
    check("it stops when the budget runs out", budget[0], 0)
    # CONTROL: there were six holes, so it did stop early rather than
    # happening to want exactly two.
    check("control, and there were more holes than that",
          len(pts) - 1, 6)


def test_a_hole_that_is_really_empty_stays_empty():
    """So a filled hole means something.

    If this returned points for any hole, the walk would bridge
    everything and the gap gate would stop meaning anything.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        # Past the end of the flight: the ball leaves the top of frame
        # around f42, so there is nothing in this box but noise -- and
        # the box sits on the ball's own line, which is the case that
        # matters.
        late = [{"frame": 46, "x": BX, "y": 60, "area": 100},
                {"frame": 52, "x": BX, "y": 20, "area": 100}]
        got = d3.fill_chain_holes(p, late, 15, sens=3)
    check("no ball, no points", got, [])


def test_a_decoy_in_the_box_is_not_taken():
    """The crop is generous; the acceptance is not.

    A second mover 28px to the side is inside the 48px box and outside
    the 16px that gets taken. Without that distinction the re-read
    fills the hole with whatever else was moving nearby -- and a hole
    filled wrong is worse than a hole, because the walk bridges it and
    the gap gate stops meaning anything.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "decoy.mp4"
        clip(p, decoy=True)
        pts = [{"frame": 13, "x": BX, "y": int(Y0 + VY * 13), "area": 60},
               {"frame": 25, "x": BX, "y": int(Y0 + VY * 25), "area": 60}]
        got = d3.fill_chain_holes(p, pts, 15, sens=3)
        _probe = d3.detect_movers_by_diff(
            p, 14, 24, sens=3, roi=(BX - 48, 0, 160, H), per_frame=8,
            three_frame=False, merge_px=d3.ASCENT_MERGE_PX)
    check("the ball comes back anyway", len(got) > 0, True)
    check("and none of them is the decoy",
          max((abs(q["x"] - BX) for q in got), default=0) <= 12, True)
    # CONTROL: the decoy really is there, and really is reported
    # first -- so taking the nearest to the prediction rather than the
    # first thing in the box is what kept the ball.
    _multi = [f for f in range(15, 24)
              if len([d for d in _probe if d["frame"] == f]) > 1]
    check("control, frames offered more than one thing",
          len(_multi) > 0, True)
    # CONTROL: and the first thing offered is the decoy, so taking the
    # NEAREST to the prediction rather than the first is what kept the
    # ball.
    _first = [[d for d in _probe if d["frame"] == f][0] for f in _multi]
    check("control, and the first one offered is the decoy",
          all(abs(d["x"] - BX) > d3.ASCENT_FILL_TOL_PX for d in _first),
          True)
    # CONTROL: the decoy IS inside the box the re-read reads, so this
    # is the acceptance doing the work and not the crop.
    check("control, the decoy is inside the crop",
          28 <= d3.ASCENT_FILL_PAD_PX, True)
    check("control, and outside what is accepted",
          28 > d3.ASCENT_FILL_TOL_PX, True)


def test_the_crop_is_what_keeps_the_ball_in_the_running():
    """Eight blobs a frame, largest first -- and the ball is small.

    Over a whole frame a faint ball loses every slot to anything
    brighter. The box is what makes the ball the only thing in the
    running, which is exactly why the operator has to zoom before
    scanning.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "busy.mp4"
        clip(p, busy=14)
        pts = [{"frame": 13, "x": BX, "y": int(Y0 + VY * 13), "area": 60},
               {"frame": 25, "x": BX, "y": int(Y0 + VY * 25), "area": 60}]
        got = d3.fill_chain_holes(p, pts, 15, sens=3)
        # CONTROL: the same frames read whole, with the same keep.
        whole = d3.detect_movers_by_diff(p, 13, 25, sens=3, per_frame=8,
                                         three_frame=False)
    check("cropped, the ball comes back", len(got), 7)
    check("control, read whole it does not",
          len([d for d in whole if abs(d["x"] - BX) < 25]), 0)


def test_a_hole_too_long_to_be_one_flight_is_not_read():
    """A re-read is a seek and a decode; it is not free.

    Past some length a hole is two objects rather than a ball nobody
    saw, and reading it is spending the budget on noise.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "faint.mp4"
        clip(p)
        pts = [{"frame": 2, "x": BX, "y": int(Y0 + VY * 2), "area": 60},
               {"frame": 40, "x": BX, "y": int(Y0 + VY * 40), "area": 60}]
        budget = [9]
        got = d3.fill_chain_holes(p, pts, 15, sens=3, budget=budget)
    check("the hole is not read", got, [])
    check("and the budget is untouched", budget[0], 9)


for fn in (test_the_three_frame_pass_is_what_loses_them,
           test_the_re_read_finds_what_the_sweep_missed,
           test_it_only_looks_where_the_ball_should_be,
           test_a_decoy_in_the_box_is_not_taken,
           test_the_crop_is_what_keeps_the_ball_in_the_running,
           test_a_hole_too_long_to_be_one_flight_is_not_read,
           test_the_budget_is_spent_once,
           test_a_hole_that_is_really_empty_stays_empty):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
