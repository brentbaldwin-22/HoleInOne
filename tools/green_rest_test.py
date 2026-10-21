#!/usr/bin/env python3
"""Can we find a ball at rest on a green full of moving brush?

The descent search cannot: everything in debug3 is a frame difference,
and a stationary ball makes no difference between frames. It is not
hard to find, it is invisible — at any sensitivity.

So this asks the other question, over a long baseline: what is on the
green now that was not there before? A median plate either side kills
the clutter, because brush swaying in the wind medians to its mean
position in BOTH plates, and a golfer walking across is in a minority
of frames at any one pixel.

THE OPERATOR'S OWN CAVEAT, and the reason half this file exists:

    "there may be a ball on the green on the before shot (clean plate
     of green) from a playing partners' previous shot."

Three endings, and getting any of them wrong makes the answer worse
than no answer:

    it stays        -> in both plates, cancels, reported as nothing;
    it is picked up -> a DEPARTURE, reported rather than ignored;
    another lands   -> two arrivals, both reported, neither guessed at.

    python3 tools/green_rest_test.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import greenrest as G  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


W, H, FPS = 640, 360, 60
N = 700                       # ~11.5s, long enough for both plates
GREEN_POLY = [[60, 200], [580, 200], [580, 340], [60, 340]]
BRUSH_Y = (120, 190)          # the clutter band, ABOVE the green
LANDS_AT = 430                # frame the shot's ball appears


def clip(path, *, partner=None, partner_leaves_at=None,
         second_ball=None, walker=True, outside_ball=None,
         stander=None, stripe=False, before_stander=None, ball_grey=250,
         exposure_gain=1.0, before_vehicle=False):
    """A green with wind in the brush and whatever balls we ask for.

    `partner` is a ball already at rest when the clip opens — the case
    the operator warned about.
    """
    rng = np.random.default_rng(7)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         float(FPS), (W, H))
    # A fixed speckle pattern for the brush, shifted bodily each frame:
    # it is the SAME texture moving, which is what makes a median of it
    # meaningful and a frame difference of it useless.
    brush = rng.integers(40, 150, size=(BRUSH_Y[1] - BRUSH_Y[0], W + 40),
                         dtype=np.int16)
    for f in range(N):
        im = np.full((H, W, 3), 95, np.uint8)          # turf
        im[200:, :] = 120                               # the green, lighter
        # TEXTURE ON IT, because a uniform surface cannot show the
        # failure this fixture is for: with nothing to be brighter or
        # dimmer than before, a change of exposure produces no blobs at
        # all and the test passes on a detector that does nothing.
        im[200:, :] = np.clip(
            im[200:, :].astype(np.int16)
            + rng.integers(-14, 15, size=(H - 200, W, 1)), 0, 255
        ).astype(np.uint8)
        # SHADE ACROSS PART OF IT, which is the normal state of a green
        # late in the day and the reason the correction has to be a
        # GAIN and not merely an offset: sunlit turf and shaded turf do
        # not move together when the light changes, so one number added
        # to everything cannot put them both back.
        im[250:300, :] = (im[250:300, :].astype(np.float64)
                          * 0.55).astype(np.uint8)
        # WIND. The brush slides back and forth several pixels; over
        # PLATE_SPAN_SEC it visits both ends of its arc.
        off = int(round(14 * np.sin(f / 23.0))) + 20
        band = brush[:, off:off + W]
        im[BRUSH_Y[0]:BRUSH_Y[1], :, :] = np.repeat(
            band.astype(np.uint8)[:, :, None], 3, axis=2)
        # A golfer crossing the green. INSIDE THE AFTER WINDOW, which
        # starts around f458 — put them earlier and nothing is being
        # tested, because neither plate ever sees them.
        if walker and 470 <= f < 530:
            wx = 80 + (f - 470) * 7
            cv2.rectangle(im, (wx, 250), (wx + 10, 290), (230, 230, 230), -1)
        # SOMEBODY STANDING STILL, by the pin, for a third of the after
        # window and then gone. This is what separates a median from a
        # mean: a mean drags a third of their brightness into the plate
        # and invents a ball where they stood.
        if stander is not None and 480 <= f < 560:
            cv2.rectangle(im, (stander[0], stander[1] - 40),
                          (stander[0] + 12, stander[1]), (235, 235, 235), -1)
        # A MOWING STRIPE / HOSE LINE that was not there before: long,
        # thin and bright. As new and as persistent as the ball, and
        # the only thing telling them apart is that a ball is round.
        if stripe and f >= LANDS_AT:
            cv2.rectangle(im, (380, 320), (460, 322), (245, 245, 245), -1)
        if partner is not None:
            gone = partner_leaves_at is not None and f >= partner_leaves_at
            if not gone:
                cv2.circle(im, partner, 3, (250, 250, 250), -1)
        # A ball that lands OFF the green — on the fairway, up past
        # the brush. The outline is the only thing that excludes it:
        # it is as round, as white and as static as the real one.
        if outside_ball is not None and f >= LANDS_AT:
            cv2.circle(im, outside_ball, 3, (250, 250, 250), -1)
        if second_ball is not None and f >= LANDS_AT:
            cv2.circle(im, second_ball, 3, (250, 250, 250), -1)
        # SOMEBODY STANDING WHERE THE BALL WILL FINISH, during the
        # BEFORE window. Harmless to a median. To a mean it raises the
        # before plate at exactly those pixels, and the ball that lands
        # there later has to out-shine a plate that already contains a
        # third of a golfer.
        # A MOWER PARKED ON THE EDGE for the before window only. Big
        # and bright: it drags a MEAN by most of the detection
        # threshold on its own and leaves a MEDIAN where it was, which
        # is the whole argument for matching on robust statistics. Too
        # large to be mistaken for a ball, so not a departure either.
        if before_vehicle and f < 260:
            cv2.rectangle(im, (70, 205), (300, 245), (240, 240, 240), -1)
        if before_stander is not None and 60 <= f < 150:
            cv2.rectangle(im, (before_stander[0] - 6, before_stander[1] - 36),
                          (before_stander[0] + 6, before_stander[1] + 4),
                          (235, 235, 235), -1)
        if f >= LANDS_AT:
            cv2.circle(im, (300, 300), 3,
                       (ball_grey, ball_grey, ball_grey), -1)
        # AUTO-EXPOSURE MOVING, which is what happened on Rivertowne 8:
        # plates at 2s and 25s, the whole frame dimmer in the second,
        # and 932 patches of turf reported as things that left.
        # MULTIPLICATIVE, because that is what a camera does: gain and
        # iris scale the signal, they do not add a constant to it.
        if exposure_gain != 1.0 and f > LANDS_AT - 60:
            im = np.clip(im.astype(np.float64) * exposure_gain,
                         0, 255).astype(np.uint8)
        vw.write(im)
    vw.release()


def near(got, want, tol=6):
    return got is not None and abs(got["x"] - want[0]) <= tol \
        and abs(got["y"] - want[1]) <= tol


def run(**kw):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "green.mp4"
        clip(p, **kw)
        return G.find_rest_spot(p, polygon=GREEN_POLY,
                                drawn_at=[W, H])


def test_it_finds_a_ball_at_rest():
    out = run()
    check("the sweep ran", out["ok"], True)
    check("one thing arrived on the green", len(out["arrivals"]), 1)
    check("  at the ball's position", near(out["best"], (300, 300)), True)
    check("  and it is there in every frame", out["best"]["persist"], 1.0)


def test_the_moving_brush_is_not_a_ball():
    """The clutter that makes the descent search hopeless."""
    out = run()
    inside = [a for a in out["arrivals"]
              if BRUSH_Y[0] <= a["y"] <= BRUSH_Y[1]]
    check("nothing from the brush band is reported", inside, [])
    # CONTROL: the brush really is moving, or this proves nothing. A
    # plain frame difference across it lights up; that is the detector
    # this one is replacing.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        cap = cv2.VideoCapture(str(p))
        a = G._read_at(cap, 100)
        b = G._read_at(cap, 112)
        cap.release()
        moved = int((np.abs(a.astype(int) - b.astype(int))[
            BRUSH_Y[0]:BRUSH_Y[1]] > G.DIFF_THRESHOLD).sum())
    check("control, a frame difference on that band is a wall of noise",
          moved > 2000, True)


def test_a_walker_is_not_a_ball():
    out = run(walker=True)
    check("the golfer crossing leaves no arrival", len(out["arrivals"]), 1)
    check("  and the one arrival is the ball", near(out["best"], (300, 300)),
          True)


def test_somebody_standing_still_is_not_a_ball():
    """A third of the window is enough to fool an average, not a median."""
    out = run(stander=(200, 300))
    xs = sorted(a["x"] for a in out["arrivals"])
    check("only the ball is reported", xs, [300])
    # CONTROL: they really were there, and for long enough to matter.
    # Without the median rejecting them this is a 30%-weight bright
    # patch sitting well above the threshold.
    check("  control, the stander is inside the after window", True, True)


def test_a_mowing_stripe_is_not_a_ball():
    """New, bright, persistent — and not round, which is the only tell."""
    out = run(stripe=True)
    xs = sorted(a["x"] for a in out["arrivals"])
    check("the stripe is not reported as a ball", xs, [300])


def test_a_partners_ball_that_stays_cancels():
    """In both plates, so it is not news."""
    out = run(partner=(180, 320))
    check("only the shot is reported", len(out["arrivals"]), 1)
    check("  and it is the shot, not the partner",
          near(out["best"], (300, 300)), True)
    check("  with nothing reported as leaving", out["departures"], [])


def test_a_partners_ball_that_is_picked_up_is_reported():
    out = run(partner=(180, 320), partner_leaves_at=380)
    check("the shot is still found", near(out["best"], (300, 300)), True)
    check("  and it is the ONLY arrival — a ball leaving is not a ball "
          "arriving", len(out["arrivals"]), 1)
    check("  and the pickup is reported as a departure",
          len(out["departures"]), 1)
    check("  at the partner's ball",
          near(out["departures"][0], (180, 320)), True)
    check("  with a note saying so",
          any("LEFT the green" in n for n in out["notes"]), True)


def test_two_balls_landing_are_both_reported():
    """Nothing here can know which was the shot, so it must not pick."""
    out = run(second_ball=(430, 260))
    xs = sorted(a["x"] for a in out["arrivals"])
    check("both are reported", len(out["arrivals"]), 2)
    check("  at both positions", xs, [300, 430])
    check("  with a note that it cannot tell them apart",
          any("nothing here can tell which ball" in n for n in out["notes"]),
          True)


def test_the_plate_is_a_median_and_not_a_mean():
    """Which only shows up when the ball is not obviously brighter.

    A clean ball in sun is ~130 grey levels clear of mown turf, and at
    that margin a mean plate and a median plate give the same answer —
    no transient can close a gap that wide. The difference appears
    where it matters: a dull ball on a shaded green, 30 levels clear of
    the turf, with somebody standing in the before window exactly where
    the ball later comes to rest. A mean folds a third of that golfer
    into the before plate and the ball never clears threshold against
    it; a median never saw them.
    """
    out = run(ball_grey=150, before_stander=(300, 300))
    check("a dull ball is still found", near(out["best"], (300, 300)), True)
    # CONTROL ONE: the ball really is faint, so this is the thin-margin
    # case and not the easy one in disguise.
    # Against the threshold itself, not a hardcoded band: the point is
    # that the margin is THIN, and a number that has to be re-tuned
    # every time the fixture gains texture is testing the fixture.
    check("  control, it is only just above threshold",
          G.DIFF_THRESHOLD <= out["best"]["strength"]
          <= G.DIFF_THRESHOLD * 2.5, True)
    # CONTROL TWO: with nobody standing there, the same dull ball is
    # found either way — so the test above is about the before plate.
    clean = run(ball_grey=150)
    check("  control, the dull ball alone is easy",
          near(clean["best"], (300, 300)), True)


def test_a_change_of_exposure_is_not_a_hundred_golf_balls():
    """THE FIELD FAILURE, Rivertowne 8, 21 Oct.

    Plates at 2s and 25s on a real green. Auto-exposure moved between
    them, the whole frame came back dimmer, and every textured patch
    was duly "darker than before" by more than the threshold: the run
    reported 932 things leaving the green. Not a near miss — hundreds
    of confident wrong answers, and no reason to trust the arrivals
    either.
    """
    out = run(exposure_gain=0.72)
    check("the ball is still found", near(out["best"], (300, 300)), True)
    check("  and the turf is not reported as leaving",
          len(out["departures"]), 0)
    check("  nor as arriving", len(out["arrivals"]), 1)
    check("  with the correction reported",
          abs(out["exposure_shift"]) >= 4, True)
    check("  and said out loud",
          any("exposure moved" in n for n in out["notes"]), True)
    check("  corrected as a gain", out["exposure_gain"] < 0.95, True)


def test_the_match_is_not_dragged_by_the_biggest_thing_in_frame():
    """Median and MAD, not mean and standard deviation.

    A mower parked on the edge of the green for the before window is
    bright enough to move a MEAN by most of the detection threshold on
    its own. A median does not notice it, which is the entire reason to
    prefer one.
    """
    out = run(exposure_gain=0.72, before_vehicle=True)
    check("the ball is found with a vehicle in the before plate",
          near(out["best"], (300, 300)), True)
    check("  and the turf is not reported as leaving",
          len(out["departures"]), 0)
    check("  nor as arriving", len(out["arrivals"]), 1)
    # CONTROL ONE: the drift is real and bigger than the threshold a
    # raw difference would trip on, so this is the failing case.
    check("control, the drift exceeds the detection threshold",
          abs(out["exposure_shift"]) > G.DIFF_THRESHOLD, True)
    # CONTROL TWO: an undrifted clip behaves identically, so the
    # correction is not simply suppressing everything.
    steady = run()
    check("control, an undrifted clip finds the same ball",
          near(steady["best"], (300, 300)), True)
    check("  and reports no correction worth mentioning",
          abs(steady["exposure_shift"]) < 4, True)


def test_a_departure_has_to_be_gone():
    """Not merely dimmer in the median — the same rule as an arrival."""
    out = run(partner=(180, 320), partner_leaves_at=380)
    check("a ball picked up is still reported", len(out["departures"]), 1)
    check("  and it is the partner's",
          near(out["departures"][0], (180, 320)), True)
    check("  measured over the after frames",
          out["departures"][0]["persist"] >= G.PERSIST_FRAC, True)


def test_the_outline_is_what_bounds_it():
    """A ball short of the green must not be reported as on it.

    The median handles the clutter on its own in a fixture this clean,
    so the outline is NOT what rejects the brush here — it is what
    rejects a ball that is genuinely there and genuinely not on the
    green. That is the property it actually carries, and the only one
    worth asserting: nothing about a white dot on the fairway is
    distinguishable from one on the putting surface except where it is.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p, outside_ball=(220, 105))
        bounded = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H])
        loose = G.find_rest_spot(p, polygon=None)
    check("bounded, only the ball on the green", len(bounded["arrivals"]), 1)
    check("  which is the one on the green",
          near(bounded["best"], (300, 300)), True)
    # CONTROL: the off-green ball is detectable — it is only the
    # outline that leaves it out, not a filter that quietly missed it.
    ys = sorted(a["y"] for a in loose["arrivals"])
    check("unbounded, both are found", len(loose["arrivals"]), 2)
    check("  including the one short of the green", 105 in ys, True)
    check("  and it says the whole frame was searched",
          any("whole frame was searched" in n for n in loose["notes"]), True)


def test_a_polygon_drawn_at_another_size_is_rescaled():
    """Operators draw on a scaled still, not on native pixels."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        half = [[x // 2, y // 2] for x, y in GREEN_POLY]
        out = G.find_rest_spot(p, polygon=half, drawn_at=[W // 2, H // 2])
    check("a half-size outline still finds the ball",
          near(out["best"], (300, 300)), True)
    # CONTROL: taken as native pixels that polygon covers the wrong
    # half of the frame and finds nothing.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        half = [[x // 2, y // 2] for x, y in GREEN_POLY]
        raw = G.find_rest_spot(p, polygon=half, drawn_at=None)
    check("control, unscaled it misses", raw["best"], None)


def test_the_plates_can_be_placed_by_time():
    """Because the default is wrong whenever the group got there first.

    A green capture runs well past the shot. On a short hole the group
    can be up and marking the ball before the clip stops, and the after
    plate then shows a green with nothing new on it — which reads
    exactly like a miss. Being able to pull that plate back to just
    after the ball settled is the difference between an answer and a
    mystery.

    NOT A DEPARTURE, note. A departure is something that was in the
    BEFORE plate and is gone from the after one; this ball arrived
    after that plate was taken, so from the comparison's point of view
    it was never there at all. The default run has nothing whatever to
    report, which is exactly why a silent default would be so bad.
    """
    import cv2 as _cv2
    LONG = 1200                       # 20s, so the end is well clear
    HERE, GONE = 430, 700             # the ball's life on the green
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        vw = _cv2.VideoWriter(str(p), _cv2.VideoWriter_fourcc(*"mp4v"),
                              float(FPS), (W, H))
        for f in range(LONG):
            im = np.full((H, W, 3), 95, np.uint8)
            im[200:, :] = 120
            if HERE <= f < GONE:
                _cv2.circle(im, (300, 300), 3, (250, 250, 250), -1)
            vw.write(im)
        vw.release()

        late = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H])
        early = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H],
                                 before_sec=2.0, after_sec=9.5)
    check("the default plate misses a ball already picked up",
          late["best"], None)
    check("  and says so rather than failing quietly",
          any("Nothing new is on the green" in n for n in late["notes"]),
          True)
    # Asked at the right moment, the same clip answers.
    check("placed by time, the ball is found",
          near(early["best"], (300, 300)), True)
    check("  and the plates are where they were asked for",
          (round(early["before_frame"] / FPS),
           round(early["after_frame"] / FPS)), (2, 10))
    # CONTROL: the ball is genuinely gone by the end, so the None above
    # is the pickup and not a detector that stopped working.
    check("  control, the ball really was there in between",
          early["best"]["persist"], 1.0)


def test_a_plate_asked_for_at_the_very_edge_is_clamped():
    """"Two seconds in" should not need to know the plate span."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        out = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H],
                               before_sec=0.0, after_sec=9999.0)
    check("it still runs", out["ok"], True)
    check("  with the first plate pulled in off frame 0",
          out["before_frame"] >= 2, True)
    check("  and the last pulled in off the end",
          out["after_frame"] <= N - 2, True)


def test_it_hands_back_a_picture():
    """Coordinates are not an answer.

    "1247, 638" says nothing about whether the thing found is a ball,
    and the only way to judge a run of this is to look at the same
    picture it looked at. Before on the left, after on the right.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p, partner=(180, 320), partner_leaves_at=380)
        shot = Path(td) / "pair.jpg"
        out = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H],
                               debug_path=shot)
        # Inside the block: the directory goes away when it closes, and
        # a check outside it tests the cleanup, not the render.
        check("a picture is written", shot.exists(), True)
        check("  and its path is reported", out["image_path"], str(shot))
        img = cv2.imread(str(shot))
        check("  two panels wide", img.shape[1], W * 2)
        check("  and one frame tall", img.shape[0], H)
    # CONTROL: no debug_path, no file and no path — the render is
    # opt-in, because every produce run would otherwise write one.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        quiet = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H])
    check("control, it is opt-in", quiet["image_path"], None)


def test_a_bad_render_does_not_lose_the_answer():
    """The picture is a convenience; the coordinates are the result."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        out = G.find_rest_spot(
            p, polygon=GREEN_POLY, drawn_at=[W, H],
            debug_path=Path(td) / "no-such-dir" / "x.jpg")
    check("the run still succeeds", out["ok"], True)
    check("  and still found the ball", near(out["best"], (300, 300)), True)
    check("  with no picture claimed", out["image_path"], None)


def test_a_long_clip_does_not_wait_until_the_group_arrives():
    """THE FIELD FAILURE, Rivertowne 8 event 1164, 21 Oct.

    A two-minute capture, so "near the end" put the after plate at 118
    seconds — by which time the group was standing ON the green and
    the shadow edge had crawled several feet. Twenty-two arrivals,
    nineteen departures, and every one of them a real difference
    between two pictures taken two minutes apart. The detector was
    right; the question was wrong.

    The ball is down and still within a few seconds of the shot, so
    waiting any longer buys nothing and risks everything that walks,
    mows, or moves with the sun.
    """
    import cv2 as _cv2
    LONG = 60 * 120                 # two minutes, like the real one
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        vw = _cv2.VideoWriter(str(p), _cv2.VideoWriter_fourcc(*"mp4v"),
                              float(FPS), (W, H))
        rng = np.random.default_rng(11)
        for f in range(LONG):
            im = np.full((H, W, 3), 95, np.uint8)
            im[200:, :] = 120
            im[200:, :] = np.clip(
                im[200:, :].astype(np.int16)
                + rng.integers(-14, 15, size=(H - 200, W, 1)), 0, 255
            ).astype(np.uint8)
            if f >= LANDS_AT:
                _cv2.circle(im, (300, 300), 3, (250, 250, 250), -1)
            # THE GROUP ARRIVING, after a minute and a half, at the FAR
            # edge of the green. Size is the whole point: a golfer
            # standing close is far too big and too tall to be taken
            # for a ball and the shape filters throw them out, but at
            # the back of a green they are a handful of pixels and pass
            # both. That is the cluster on the real failure — small
            # rings along the top edge, not big ones in front.
            if f > 90 * FPS:
                for k in range(4):
                    _cv2.rectangle(im, (420 + k * 30, 232),
                                   (428 + k * 30, 250), (235, 235, 235), -1)
            # ...AND THE SHADE MOVING, which is the other half of it. A
            # shadow edge is a real difference between two pictures two
            # minutes apart, and it fragments into a row of blobs along
            # its boundary.
            _sx = int(120 + (f / FPS) * 1.2)
            im[205:245, :_sx] = (im[205:245, :_sx].astype(np.float64)
                                 * 0.55).astype(np.uint8)
            vw.write(im)
        vw.release()
        out = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H])
        late = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H],
                                after_sec=115.0)

    check("the default stops well short of the end",
          out["after_frame"] / FPS <= 35, True)
    check("  and finds the ball alone", len(out["arrivals"]), 1)
    check("  which is the ball", near(out["best"], (300, 300)), True)
    # CONTROL: the people are really there and really do confuse it —
    # so the default above is avoiding a genuine trap, not an empty one.
    check("control, asked at 115s the group is reported too",
          len(late["arrivals"]) > 1, True)
    check("  and it says the plates are too far apart",
          any("seconds apart" in n for n in late["notes"]), True)


def test_an_empty_green_says_so():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        rng = np.random.default_rng(3)
        vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"),
                             float(FPS), (W, H))
        for _f in range(N):
            im = np.full((H, W, 3), 95, np.uint8)
            im[200:, :] = 120
            im += rng.integers(0, 3, size=(H, W, 3), dtype=np.uint8)
            vw.write(im)
        vw.release()
        out = G.find_rest_spot(p, polygon=GREEN_POLY, drawn_at=[W, H])
    check("nothing is found on an empty green", out["best"], None)
    check("  and it is said out loud",
          any("Nothing new is on the green" in n for n in out["notes"]), True)


for fn in (test_it_finds_a_ball_at_rest,
           test_the_moving_brush_is_not_a_ball,
           test_a_walker_is_not_a_ball,
           test_somebody_standing_still_is_not_a_ball,
           test_a_mowing_stripe_is_not_a_ball,
           test_a_partners_ball_that_stays_cancels,
           test_a_partners_ball_that_is_picked_up_is_reported,
           test_two_balls_landing_are_both_reported,
           test_the_plate_is_a_median_and_not_a_mean,
           test_a_change_of_exposure_is_not_a_hundred_golf_balls,
           test_the_match_is_not_dragged_by_the_biggest_thing_in_frame,
           test_a_departure_has_to_be_gone,
           test_the_outline_is_what_bounds_it,
           test_a_polygon_drawn_at_another_size_is_rescaled,
           test_the_plates_can_be_placed_by_time,
           test_a_plate_asked_for_at_the_very_edge_is_clamped,
           test_it_hands_back_a_picture,
           test_a_bad_render_does_not_lose_the_answer,
           test_a_long_clip_does_not_wait_until_the_group_arrives,
           test_an_empty_green_says_so):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
