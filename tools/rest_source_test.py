#!/usr/bin/env python3
"""Which answer wins when the ball's resting place has three sources?

The number this feeds is a distance from the pin, and a distance from
the pin decides a prize. So the order is not a preference, it is a
claim about which measurement is true:

  operator  a human watched the ball and stopped clicking where it
            stopped. Nothing measured can improve on that, and
            overruling it would make their own correction vanish.
  plates    the green before the shot compared with the green once
            everything has settled. Where it FINISHED.
  descent   the last frame of an automatic descent chain: where the
            ball stopped FALLING. That is the pitch mark, and a ball
            bounces and rolls from there.

The bug this guards is quiet and expensive: a pitch mark silently
printed as a resting position, short of the truth by however far the
ball ran out.

    python3 tools/rest_source_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402
from app.services import greenrest as G  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Row:
    id = 1
    green_filename = "g.mp4"
    camera_event_id = None

    def __init__(self, swing):
        self.edit_metrics = {"swings": [swing]}


def resolve(swing, plates=None):
    """_green_rest_for_swing with the plate finder stubbed."""
    saved = (admin._camera_pair_for, admin._rest_from_plates)
    try:
        admin._camera_pair_for = lambda db, row: (10, 20)
        admin._rest_from_plates = lambda db, row, sw, gid: plates
        return admin._green_rest_for_swing(None, Row(swing), 0)
    finally:
        (admin._camera_pair_for, admin._rest_from_plates) = saved


PLATES = {"xy": [500.0, 400.0], "camera_id": 20, "source": "plates"}
CHAIN = {"idx": 0, "descent_auto": True,
         "green_track": [{"frame": 9, "x": 100, "y": 200}]}
HAND = {"idx": 0, "descent_auto": False,
        "green_track": [{"frame": 9, "x": 100, "y": 200}]}


def test_a_human_outranks_a_measurement():
    got = resolve(HAND, plates=PLATES)
    check("the operator's own point wins", got["source"], "operator")
    check("  and it is their point", got["xy"], [100.0, 200.0])
    # CONTROL: the plates were available and would have won otherwise,
    # so this is a priority and not an empty branch.
    check("control, with no hand plot the plates are taken",
          resolve(CHAIN, plates=PLATES)["source"], "plates")


def test_the_plates_outrank_a_descent_chain():
    """A pitch mark is not a resting place."""
    got = resolve(CHAIN, plates=PLATES)
    check("the plates win over the chain", got["source"], "plates")
    check("  and the answer is theirs", got["xy"], [500.0, 400.0])
    # CONTROL: the chain is right there and is what gets used when the
    # plates decline, so nothing is being dropped on the floor.
    check("control, no plates falls back to the chain",
          resolve(CHAIN, plates=None)["source"], "descent")
    check("  at the chain's last point",
          resolve(CHAIN, plates=None)["xy"], [100.0, 200.0])


def test_a_stored_measurement_is_not_measured_again():
    """The ascent pass writes it onto the swing; this is a lookup.

    Re-running the finder here would read the whole green clip a
    second time for a number already sitting on the row.
    """
    swing = dict(CHAIN)
    swing["rest_spot"] = [640, 480]
    calls = []

    def _spy(db, row, sw, gid):
        calls.append(1)
        return PLATES

    saved = (admin._camera_pair_for, admin._rest_from_plates)
    try:
        admin._camera_pair_for = lambda db, row: (10, 20)
        admin._rest_from_plates = _spy
        got = admin._green_rest_for_swing(None, Row(swing), 0)
    finally:
        (admin._camera_pair_for, admin._rest_from_plates) = saved

    check("the stored point is used", got["xy"], [640.0, 480.0])
    check("  and it is still the plates", got["source"], "plates")
    check("  without reading the clip again", calls, [])
    # CONTROL: with nothing stored, the finder IS called — so the empty
    # call list above is the cache and not a disconnected wire.
    check("control, no stored point runs the finder",
          resolve(CHAIN, plates=PLATES)["xy"], [500.0, 400.0])


def test_a_swing_with_nothing_at_all_answers_nothing():
    check("no track and no plates is None",
          resolve({"idx": 0, "descent_auto": True}, plates=None), None)


# ---- the finder actually running, on a real clip ------------------

import tempfile  # noqa: E402
import numpy as np  # noqa: E402
import cv2  # noqa: E402

CW, CH, CFPS, CN = 320, 240, 30, 900        # 30 seconds
BALL = (160, 170)
OUTLINE = {"points": [[40, 140], [300, 140], [300, 230], [40, 230]],
           "frame": {"w": CW, "h": CH}}
# Impact at 10s of the green clip: the plates land at 4s and 24s, both
# comfortably inside a 30-second capture.
SWING = {"idx": 0, "impact_frame": 600, "fps": 60, "descent_auto": True}


def green_clip(path, balls):
    rng = np.random.default_rng(3)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         float(CFPS), (CW, CH))
    for f in range(CN):
        im = np.full((CH, CW, 3), 118, np.uint8)
        im = np.clip(im.astype(np.int16)
                     + rng.integers(-8, 9, size=(CH, CW, 1)),
                     0, 255).astype(np.uint8)
        if f >= 14 * CFPS:                   # lands at 14s, stays
            for b in balls:
                cv2.circle(im, b, 3, (250, 250, 250), -1)
        vw.write(im)
    vw.release()


def run_finder(balls, outline=OUTLINE):
    """_rest_from_plates against a real clip and a stubbed camera."""
    class Cam:
        green_roi = outline

    class DB:
        def get(self, _model, _pk):
            return Cam()

    class Store:
        @staticmethod
        def ensure_local(*_a, **_k):
            return True

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        green_clip(d / "g.mp4", balls)
        row = Row(SWING)
        saved = (admin.CLIPS_DIR, admin.storage)
        try:
            admin.CLIPS_DIR = d
            admin.storage = Store()
            return admin._rest_from_plates(DB(), row, SWING, 20)
        finally:
            (admin.CLIPS_DIR, admin.storage) = saved


def test_it_finds_one_ball_on_a_real_clip():
    got = run_finder([BALL])
    check("the plates answer", got and got["source"], "plates")
    check("  at the ball", 
          got and (abs(got["xy"][0] - BALL[0]) <= 6
                   and abs(got["xy"][1] - BALL[1]) <= 6), True)


def test_two_things_arriving_is_not_an_answer():
    """A partner who also hit in, or a shadow that moved.

    There is no way from here to know which blob was the shot, and a
    prize measured off a guess is worse than no measurement.
    """
    got = run_finder([BALL, (240, 200)])
    check("two arrivals declines to answer", got, None)
    # CONTROL: either ball alone IS answered, so the None above is the
    # ambiguity and not a detector that failed on this clip.
    check("control, the first alone is answered",
          run_finder([BALL])["source"], "plates")
    check("control, the second alone is answered",
          run_finder([(240, 200)])["source"], "plates")


def test_the_after_plate_is_never_earlier_than_the_default():
    """Anchoring it purely on impact looked right and was not.

    Rivertowne 8 event 1161: the impact resolved to -0.4s of the green
    clip, so impact+14 put the after plate at 13.6s — and the same clip
    asked at 25s by hand found the ball. A plate taken too early
    catches a ball still rolling, or not yet down. Later is the safe
    direction, and the 30-second default is already the latest it is
    safe to be.
    """
    seen = {}
    real = G.find_rest_spot

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    class Cam:
        green_roi = OUTLINE

    class DB:
        def get(self, _model, _pk):
            return Cam()

    class Store:
        @staticmethod
        def ensure_local(*_a, **_k):
            return True

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        green_clip(d / "g.mp4", [BALL])
        saved = (admin.CLIPS_DIR, admin.storage, G.find_rest_spot)
        try:
            admin.CLIPS_DIR = d
            admin.storage = Store()
            G.find_rest_spot = spy
            # An impact that resolves EARLY, as the field case did.
            admin._rest_spot_at(DB(), Row(SWING), d / "g.mp4", -0.4, 20)
        finally:
            (admin.CLIPS_DIR, admin.storage,
             G.find_rest_spot) = saved

    check("the after plate is not dragged early by the impact",
          seen.get("after_sec"), G.DEFAULT_AFTER_SEC)
    # CONTROL: a LATE impact does move it — the rule is "never earlier",
    # not "always the default", or a two-minute capture with a late
    # swing would plate itself before the ball was struck.
    seen.clear()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        green_clip(d / "g.mp4", [BALL])
        saved = (admin.CLIPS_DIR, admin.storage, G.find_rest_spot)
        try:
            admin.CLIPS_DIR = d
            admin.storage = Store()
            G.find_rest_spot = spy
            admin._rest_spot_at(DB(), Row(SWING), d / "g.mp4", 50.0, 20)
        finally:
            (admin.CLIPS_DIR, admin.storage,
             G.find_rest_spot) = saved
    check("control, a late impact pushes it later", seen.get("after_sec"), 64.0)
    check("  and the before plate still follows the impact",
          seen.get("before_sec"), 44.0)


def test_without_an_outline_it_will_not_measure():
    """Unbounded it cannot tell the green from the ground short of it.

    Fine when an operator is looking at the picture and judging. Not
    fine when the answer becomes a measured distance on a prize.
    """
    check("no outline at all, no automatic answer",
          run_finder([BALL], outline=None), None)
    # AN OUTLINE THAT EXISTS BUT IS EMPTY is the case the guard is
    # really for. Nothing raises on it — the finder would happily take
    # an empty polygon and search the whole frame, measuring a prize
    # off a white dot that might be on the fairway.
    check("  nor an emptied one",
          run_finder([BALL], outline={"points": [],
                                      "frame": {"w": CW, "h": CH}}), None)
    # CONTROL: the same clip with an outline answers, so the refusal is
    # the policy and not a clip the finder could not read.
    check("control, with an outline the same clip answers",
          run_finder([BALL])["source"], "plates")


def test_the_plate_finder_refuses_without_an_outline():
    """Unbounded it cannot tell the green from the fairway short of it.

    Fine when an operator is looking at the picture and judging. Not
    fine when the answer becomes a measured distance on a prize.
    """
    class Cam:
        green_roi = None

    class DB:
        def get(self, _model, _pk):
            return Cam()

    out = admin._rest_from_plates(
        DB(), Row({"idx": 0, "impact_frame": 600, "fps": 60}), 
        {"idx": 0, "impact_frame": 600, "fps": 60}, 20)
    check("no outline, no automatic answer", out, None)
    # CONTROL: with an outline it gets as far as looking for the clip,
    # so the None above is the outline check and not an import error.
    class Cam2:
        green_roi = {"points": [[0, 0], [10, 0], [10, 10]],
                     "frame": {"w": 100, "h": 100}}

    class DB2:
        def get(self, _model, _pk):
            return Cam2()

    row = Row({"idx": 0, "impact_frame": 600, "fps": 60})
    row.green_filename = "does-not-exist.mp4"
    check("control, with an outline it proceeds to the clip",
          admin._rest_from_plates(
              DB2(), row, {"idx": 0, "impact_frame": 600, "fps": 60}, 20),
          None)


for fn in (test_a_human_outranks_a_measurement,
           test_the_plates_outrank_a_descent_chain,
           test_a_stored_measurement_is_not_measured_again,
           test_a_swing_with_nothing_at_all_answers_nothing,
           test_it_finds_one_ball_on_a_real_clip,
           test_two_things_arriving_is_not_an_answer,
           test_the_after_plate_is_never_earlier_than_the_default,
           test_without_an_outline_it_will_not_measure,
           test_the_plate_finder_refuses_without_an_outline):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
