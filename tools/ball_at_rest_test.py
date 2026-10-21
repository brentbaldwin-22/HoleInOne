#!/usr/bin/env python3
"""Find the ball at rest from two colour frames, and nothing else.

    "let's just use ai. we're making this way too complicated. if i ask
     claude ai or chatgpt, it can identify the ball on the green every
     time. colour picture before and colour picture after. ai identify
     the ball at rest on the green."

Which is right, and the reason is worth stating: a ball at rest on
mown grass is one of the things a vision model is genuinely good at,
and "what is in the second picture that was not in the first" is a
question about meaning rather than pixel arithmetic. The plate
comparison this replaces had to be TOLD what counts as the green. A
model can see it — so the outline is gone too.

THE ONE THING A MODEL IS NOT GOOD AT is exactly where it lands. This
repo already measured it, at `_snap_to_ball`: a claimed rest position
can be off by 50-100px, which on a green is tens of feet. So the
answer is snapped to the real bright blob nearest it. The model says
WHICH object; the pixels say WHERE.

Everything below is about the ways that can go wrong quietly: a
position off the edge of the frame, an unsure answer taken as sure, a
snap that drags the point onto the flagstick.

    python3 tools/ball_at_rest_test.py
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import ai_tracer as A  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


W, H, FPS, N = 960, 540, 30, 600          # 20 seconds
BALL = (520, 400)
FLAG = (300, 330)


def clip(path, ball=BALL, lands_at=8 * FPS):
    """A green with a flagstick throughout and a ball from `lands_at`."""
    rng = np.random.default_rng(5)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         float(FPS), (W, H))
    for f in range(N):
        im = np.full((H, W, 3), 70, np.uint8)
        im[280:, :] = (60, 120, 70)                      # the green
        im[280:, :] = np.clip(
            im[280:, :].astype(np.int16)
            + rng.integers(-9, 10, size=(H - 280, W, 1)), 0, 255
        ).astype(np.uint8)
        # A flagstick: bright, and NOT the answer. It is also what a
        # careless snap radius would reach for.
        cv2.line(im, (FLAG[0], FLAG[1] - 60), FLAG, (250, 250, 250), 2)
        if f >= lands_at and ball:
            cv2.circle(im, ball, 4, (252, 252, 252), -1)
        vw.write(im)
    vw.release()


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


def ask(reply, path, before=2.0, after=13.0, raises=None, debug=None,
        pair=None):
    class Msgs:
        @staticmethod
        def create(**_kw):
            if raises:
                raise RuntimeError(raises)
            return types.SimpleNamespace(content=[Block(reply)])

    client = types.SimpleNamespace(messages=Msgs())
    saved = (A.have_ai_tracer, A._anthropic_client, A._resolve_model)
    try:
        A.have_ai_tracer = lambda: True
        A._anthropic_client = lambda: client
        A._resolve_model = lambda m=None: "test-model"
        return A.find_ball_at_rest(path, before, after,
                                   debug_path=debug,
                                   pair_path=pair)
    finally:
        (A.have_ai_tracer, A._anthropic_client, A._resolve_model) = saved


def test_a_rough_answer_is_snapped_onto_the_ball():
    """The whole design in one assertion.

    The model is told to be 40px off — about what this repo measured —
    and the answer still comes back ON the ball, because the snap did
    the last 40px with arithmetic instead of judgement.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        off = (BALL[0] + 28, BALL[1] - 29)
        got = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "small white ball"}' % off, p)
    check("an answer comes back", got["error"], None)
    check("  snapped", got["snapped"], True)
    check("  onto the ball",
          abs(got["xy"][0] - BALL[0]) <= 3 and abs(got["xy"][1] - BALL[1]) <= 3,
          True)
    check("  and it says how far it moved", got["snap_px"] >= 30, True)
    check("  on the green", got["on_green"], True)


def test_the_snap_does_not_reach_the_flagstick():
    """A wide snap radius is how a ball becomes a flag.

    The model is told the ball is near the FLAG, nowhere near the real
    ball. The snap must take the nearest ball-SIZED blob — and the
    flagstick, being a long line, is not one.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        got = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "x"}' % (FLAG[0] + 6, FLAG[1] - 20), p)
    # Either it finds nothing ball-sized nearby and keeps the claim, or
    # it snaps to something small — but it must NOT return the stick.
    check("the answer is not the flagstick's centre",
          abs(got["xy"][1] - (FLAG[1] - 30)) > 8 or not got["snapped"], True)
    # CONTROL: the same machinery DOES snap when a real ball is near,
    # so the above is the area filter and not a snap that never fires.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        near = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                   ' "why": "x"}' % (BALL[0] + 20, BALL[1]), p)
    check("control, a claim near the ball does snap", near["snapped"], True)


def test_a_position_off_the_frame_is_refused():
    """Clamping would turn a clear failure into an edge-of-green answer."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        check("off the right edge",
              ask('{"x": 5000, "y": 100, "on_green": true, '
                  '"confidence": 0.9, "why": "x"}', p)["xy"], None)
        check("  and negative",
              ask('{"x": -4, "y": 100, "on_green": true, '
                  '"confidence": 0.9, "why": "x"}', p)["xy"], None)
        # CONTROL: an in-frame position at the same confidence works.
        check("control, in frame is taken",
              ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "x"}' % BALL, p)["xy"] is not None, True)


def test_an_unsure_or_empty_answer_is_not_taken():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        check("no ball found",
              ask('{"x": null, "y": null, "on_green": false, '
                  '"confidence": 0, "why": "cannot see one"}', p)["error"],
              "model did not find a ball")
        low = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.2,'
                  ' "why": "x"}' % BALL, p)
        check("  low confidence", low["xy"], None)
        check("    naming the bar", "below 0.5" in (low["error"] or ""), True)
        check("  prose instead of JSON", ask("it is on the left", p)["xy"],
              None)
        check("  and an API that fell over",
              ask('{"x": 1, "y": 1}', p, raises="boom")["xy"], None)


def test_it_says_when_the_ball_missed_the_green():
    """Which is the outline's old job, done by the model instead."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        got = ask('{"x": %d, "y": %d, "on_green": false, "confidence": 0.8,'
                  ' "why": "it is in the fringe"}' % BALL, p)
    check("the position still comes back", got["xy"] is not None, True)
    check("  flagged as off the putting surface", got["on_green"], False)


def test_a_missing_clip_is_not_a_ball():
    got = ask('{"x": 1, "y": 1, "on_green": true, "confidence": 0.9,'
              ' "why": "x"}', Path("/nope/nothing.mp4"))
    check("no frames, no answer", got["xy"], None)
    check("  and it says so",
          got["error"], "could not read both frames from the green clip")


def test_it_shows_both_frames():
    """The question is what CHANGED, so both have to be on screen.

    A single marked panel asks the operator to take the comparison on
    trust. In colour, because a white ball on green grass is a colour
    fact and greyscale throws away the easier half of it.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        pair = Path(td) / "pair.jpg"
        got = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "x"}' % BALL, p, pair=pair)
        check("a pair is written", pair.exists(), True)
        check("  and reported", got["pair_image"], str(pair))
        img = cv2.imread(str(pair))
        check("  two panels wide", img.shape[1], W * 2)
        # COLOUR, not greyscale: the three channels must actually
        # differ, or this is a grey image in a colour container.
        b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
        check("  and genuinely in colour",
              not (np.array_equal(b, g) and np.array_equal(g, r)), True)
    # CONTROL: no pair_path, no file and nothing claimed.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        check("control, it is opt-in",
              ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "x"}' % BALL, p)["pair_image"], None)


def test_the_prompt_demands_both_conditions():
    """On the green AND not there before — the operator's own wording.

    A prompt that asks only "where is the ball" will happily return a
    partner's ball that was already sitting there, or one in the
    fringe. Both are confidently wrong answers to a question about a
    prize.
    """
    t = A.BALL_AT_REST_PROMPT
    check("it asks the direct question",
          "WHERE IS THE GOLF BALL ON THE GREEN" in t, True)
    check("  insists on the putting surface",
          "ON THE PUTTING SURFACE" in t, True)
    check("  and on the ball being new",
          "NOT THERE IN THE BEFORE FRAME" in t, True)
    check("  rules out a ball already there",
          "belongs to somebody" in t, True)
    check("  and offers a way out",
          "could not find one" in t, True)


def test_it_draws_what_it_found_in_colour():
    """It used to render the greyscale plate the old method worked in.

    On a green, white-on-green IS the signal — a black-and-white
    picture of a golf course throws away the thing that makes the
    answer checkable at a glance. Nothing downstream ever wanted it
    grey; it was an artefact of the method that has been replaced.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "g.mp4"
        clip(p)
        shot = Path(td) / "found.jpg"
        got = ask('{"x": %d, "y": %d, "on_green": true, "confidence": 0.9,'
                  ' "why": "x"}' % BALL, p, debug=shot)
        check("a picture is written", shot.exists(), True)
        check("  and reported", got["image"], str(shot))
        img = cv2.imread(str(shot))
        b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
        check("  in colour",
              not (np.array_equal(b, g) and np.array_equal(g, r)), True)
        check("  and one panel, not a pair", img.shape[1], W)


for fn in (test_a_rough_answer_is_snapped_onto_the_ball,
           test_the_snap_does_not_reach_the_flagstick,
           test_a_position_off_the_frame_is_refused,
           test_an_unsure_or_empty_answer_is_not_taken,
           test_it_says_when_the_ball_missed_the_green,
           test_a_missing_clip_is_not_a_ball,
           test_it_shows_both_frames,
           test_the_prompt_demands_both_conditions,
           test_it_draws_what_it_found_in_colour):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
