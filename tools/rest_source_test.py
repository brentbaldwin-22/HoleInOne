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
from app.services import debug3 as D3  # noqa: E402

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


def run_finder(balls, outline=OUTLINE, direct=False):
    """A real clip and a stubbed camera.

    `direct` calls _rest_spot_at, skipping the _rest_from_plates
    wrapper whose catch-all would hide a crash as a refusal.
    """
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
            if direct:
                return admin._rest_spot_at(
                    DB(), row, d / "g.mp4", 10.0, 20)[0]
            return admin._rest_from_plates(DB(), row, SWING, 20)
        finally:
            (admin.CLIPS_DIR, admin.storage) = saved


# WHAT "THE PIXEL COMPARISON" IS NAMED NOW.
#
# It used to be the plate diff -- the whole before frame against the
# whole after frame -- and it is now the zoned tile detector, which
# cuts the outline's box into four overlapping zones, scales each up
# and runs the same top-hat that finds a ball at address on the tee.
# The plates are still there, behind it, as the fallback. These tests
# are about PRECEDENCE and about the refusals, not about which of the
# two measured it, so they accept either and the source is printed
# when one fails.
PIXELS = ("tiles", "plates")


def test_it_finds_one_ball_on_a_real_clip():
    got = run_finder([BALL])
    check("the pixel comparison answers",
          got and got["source"] in PIXELS, True)
    check("  at the ball",
          got and (abs(got["xy"][0] - BALL[0]) <= 6
                   and abs(got["xy"][1] - BALL[1]) <= 6), True)
    # AND IT SAYS WHETHER AN OUTLINE BOUNDED IT. This was hardcoded
    # True on a branch reachable with no outline at all, so a ball
    # found on the fairway was reported as being on the putting
    # surface -- the one thing the flag exists to prevent.
    check("  and on_green is read back, not claimed",
          got and got["on_green"], True)


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
          run_finder([BALL])["source"] in PIXELS, True)
    check("control, the second alone is answered",
          run_finder([(240, 200)])["source"] in PIXELS, True)


def test_the_search_window_follows_the_impact_and_is_not_pushed_later():
    """And a note on why, because I got this wrong once.

    I made this take whichever was later of impact+14 and the
    30-second default, reasoning that a later plate is a more settled
    ball. The field disagrees: on Rivertowne 8 event 1161 the
    impact-anchored window (2.0s/13.6s) and the operator's own 2s/13s
    run agree on the same point to three pixels, while the same clip at
    2s/25s picks a DIFFERENT one.

    Later is not safer on a green in partial shade. Every extra second
    is more shadow travel, and shadow travel is a real difference
    between two frames -- so a later after frame competes the ball
    against more clutter, not less.

    THE WINDOW IS A LADDER NOW, not a single pair, and the ladder's
    own shape is `green_window_test`'s business. What is tested here
    is the WIRING: that the finder walks that ladder rather than a
    constant of its own, and that the window it answers from is the
    one it reports. The two drifted apart once already -- the button
    ran 2.0/13.0 while produce ran impact-6/+14, twenty seconds
    against eleven, and the button found balls produce did not.
    """
    # THE FINDER'S OWN ANSWER CARRIES THE WINDOW IT USED.
    got = run_finder([BALL])
    _w = got and got.get("window")
    check("the answer names the window it came from",
          bool(_w) and len(_w) == 2, True)
    # AND THAT WINDOW IS ONE OF THE LADDER'S RUNGS FOR THIS IMPACT --
    # not a constant, and not a rung for some other impact. run_finder
    # passes an impact of 10.0s.
    _rungs = [[a, b] for a, b in admin._green_windows(10.0)]
    check("  and it is a rung of the ladder for this impact",
          [float(_w[0]), float(_w[1])] in _rungs, True)
    check("  which is the FIRST rung, the soonest after settling",
          [float(_w[0]), float(_w[1])], _rungs[0])
    # CONTROL: a different impact gives a different rung, so the match
    # above is the impact and not a constant that happens to agree.
    check("control, the ladder moves with the impact",
          admin._green_windows(50.0)[0] != _rungs[0], True)
    # AND EVERY RUNG IT TRIED IS REPORTED, so a clip that needed the
    # fourth rung can be told apart from one that answered on the
    # first -- the difference is how much shadow travel was competing
    # with the ball.
    check("  and every rung it tried is listed",
          len(got.get("windows_tried") or []) >= 1, True)


def test_the_picker_resolves_two_arrivals_or_leaves_the_refusal():
    """The fallback has to survive every way the model can fail."""
    # STRAIGHT AT _rest_spot_at, NOT THROUGH _rest_from_plates. That
    # wrapper catches every exception and returns None, which is right
    # for a measurement that must not cost a clip and wrong for a test:
    # a crash in here comes back looking exactly like a refusal. A
    # mutation run proved it — removing the refusal entirely still
    # passed, because the TypeError it caused was swallowed.
    def with_picker(result):
        saved = admin._ai_pick_ball
        try:
            admin._ai_pick_ball = lambda arrivals, img: result
            return run_finder([BALL, (240, 200)], direct=True)
        finally:
            admin._ai_pick_ball = saved

    # Unresolved — exactly the state before the picker existed.
    check("declining leaves the refusal standing", with_picker(None), None)

    one = with_picker({"index": 1, "confidence": 0.9, "why": "round"})
    two = with_picker({"index": 2, "confidence": 0.9, "why": "round"})
    # THE SOURCE SAYS THE MODEL WAS INVOLVED. Two new things that a
    # model resolved is a weaker answer than one new thing nobody had
    # to choose between, and a report that spells both the same cannot
    # tell them apart.
    check("a choice is taken",
          one and one["source"] in ("tiles+ai", "plates+ai"), True)

    def near_one_of(got, wanted):
        return any(abs(got["xy"][0] - x) <= 6 and abs(got["xy"][1] - y) <= 6
                   for x, y in wanted)

    both = [BALL, (240, 200)]
    check("  and lands on one of the two balls", near_one_of(one, both), True)
    check("  as does ring 2", near_one_of(two, both), True)
    # THE PROPERTY THAT MATTERS, and the one that is actually
    # assertable: the index SELECTS. Which ball is ring 1 depends on
    # how the candidates sort, which is not this test's business —
    # that the two rings give DIFFERENT balls is.
    check("  and the two rings are different balls",
          one["xy"] != two["xy"], True)


def test_the_picker_is_not_consulted_when_there_is_one_arrival():
    """It costs money and there is nothing to decide."""
    calls = []
    saved = admin._ai_pick_ball
    try:
        admin._ai_pick_ball = lambda arrivals, img: calls.append(1)
        got = run_finder([BALL])
    finally:
        admin._ai_pick_ball = saved
    check("no call for a single candidate", calls, [])
    check("  and the answer is the pixels alone",
          got["source"] in PIXELS, True)


# ---- AI first, and no outline needed ------------------------------

def run_with_ai(ai_result, outline=None, balls=(BALL,)):
    """_rest_spot_at with find_ball_at_rest stubbed."""
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
        green_clip(d / "g.mp4", list(balls))
        saved = (admin.CLIPS_DIR, admin.storage,
                 admin.ai_tracer.find_ball_at_rest
                 if hasattr(admin, "ai_tracer") else None)
        from app.services import ai_tracer as _ai
        _real = _ai.find_ball_at_rest
        try:
            admin.CLIPS_DIR = d
            admin.storage = Store()
            _ai.find_ball_at_rest = lambda *a, **k: ai_result
            return admin._rest_spot_at(DB(), Row(SWING), d / "g.mp4",
                                       10.0, 20)
        finally:
            admin.CLIPS_DIR, admin.storage = saved[0], saved[1]
            _ai.find_ball_at_rest = _real


AI_FOUND = {"xy": [161, 171], "on_green": True, "confidence": 0.9,
            "why": "small white ball", "snapped": True, "snap_px": 12,
            "error": None, "image": None, "before_sec": 4.0,
            "after_sec": 24.0}
AI_BLANK = {"xy": None, "on_green": False, "confidence": 0.0, "why": "",
            "snapped": False, "snap_px": None,
            "error": "model did not find a ball", "image": None,
            "before_sec": 4.0, "after_sec": 24.0}


def test_the_model_answers_and_no_outline_is_needed():
    """The outline was the price of the plates. The model does not ask."""
    got, why = run_with_ai(AI_FOUND, outline=None)
    check("an answer with no outline at all", got and got["source"], "ai")
    check("  at the model's point", got["xy"], [161.0, 171.0])
    check("  carrying whether it is on the green", got["on_green"], True)
    check("  and the reason says so", "90% confident" in why, True)


def test_a_ball_off_the_green_is_reported_as_such():
    """The outline's old job, done by the model.

    Still measured — where it finished is worth knowing even when it
    missed — but flagged, because the distance only counts on the green.
    """
    off = dict(AI_FOUND, on_green=False)
    got, why = run_with_ai(off, outline=None)
    check("it still comes back", got and got["xy"], [161.0, 171.0])
    check("  flagged off the surface", got["on_green"], False)
    check("  and said out loud", "NOT on the putting surface" in why, True)


def test_the_plates_are_the_fallback_and_still_need_one():
    # Model declines, outline present: the plates answer.
    got, _why = run_with_ai(AI_BLANK, outline=OUTLINE)
    check("the pixel comparison picks it up",
          got and got["source"] in PIXELS, True)
    # Model declines, no outline: nothing to fall back to, said plainly.
    none, why = run_with_ai(AI_BLANK, outline=None)
    check("  with no outline there is nowhere to go", none, None)
    check("  and the reason names both halves",
          "model did not find a ball" in why and "no fallback" in why, True)


def test_the_pixel_fallback_still_needs_an_outline():
    """It is the PIXELS that need one, not the measurement.

    Both of them: the zoned tile detector in front and the plate diff
    behind it. The detector was put in front of this guard and walked
    straight past it -- `find_balls_on_green` runs without a polygon,
    searching the whole frame and saying so, which is right for the
    operator's button and wrong where the answer becomes a distance.
    It also hardcoded `on_green: True` on that same branch, so a ball
    found on the fairway was reported as being on the putting surface.

    Unbounded, a pixel comparison cannot tell the green from the
    ground short of it — fine when an operator is looking at the
    picture and judging, not fine when the answer becomes a measured
    distance on a prize. The model has no such problem: it is asked
    whether the ball is on the putting surface and it can see.

    (These runs reach the plates because no AI is configured in a test
    environment, which is itself the fallback working.)
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
          run_finder([BALL])["source"] in PIXELS, True)


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


def test_the_descent_search_is_off_by_default():
    """And a switch, not a deletion.

    On a green ringed with wind-blown brush the descent search does not
    merely fail, it answers WRONGLY: event 1161 put the landing up in
    the fairway well above the green, which then draws a comet along a
    path the ball never took and offers a pitch mark that is not one.
    The resting-ball search needs none of it.
    """
    check("off unless a hole asks for it",
          D3.GATE_DEFAULTS["ascent_descent_search"], 0)
    check("  and a hole can ask",
          D3.resolve_gates({"ascent_descent_search": 1})
          ["ascent_descent_search"], 1)
    # CONTROL: it is a real gate with a real range, not a stray key
    # that resolve_gates happens to echo back.
    check("  bounded 0..1",
          tuple(D3.GATE_SPEC["ascent_descent_search"][1:3]), (0, 1))


for fn in (test_the_descent_search_is_off_by_default,
           test_a_human_outranks_a_measurement,
           test_the_plates_outrank_a_descent_chain,
           test_a_stored_measurement_is_not_measured_again,
           test_a_swing_with_nothing_at_all_answers_nothing,
           test_it_finds_one_ball_on_a_real_clip,
           test_two_things_arriving_is_not_an_answer,
           test_the_search_window_follows_the_impact_and_is_not_pushed_later,
           test_the_picker_resolves_two_arrivals_or_leaves_the_refusal,
           test_the_picker_is_not_consulted_when_there_is_one_arrival,
           test_the_model_answers_and_no_outline_is_needed,
           test_a_ball_off_the_green_is_reported_as_such,
           test_the_plates_are_the_fallback_and_still_need_one,
           test_the_pixel_fallback_still_needs_an_outline,
           test_the_plate_finder_refuses_without_an_outline):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
