#!/usr/bin/env python3
"""When two things arrive on the green, which one was the shot?

The plates cannot tell. A partner who also hit in, shade that crawled,
a towel somebody dropped — all of them are genuinely new on the
putting surface, and the search refuses rather than guessing, because
a prize measured off a guess is worse than no measurement.

Refusing is right and it is also a dead end, so a model is asked. This
is the one part of the job that is judgement rather than measurement:
the candidates arrive WITH exact coordinates and the only question is
which of three or four is a golf ball. The model's weak axis — pixel
coordinates, off by 50-100px per this repo's own notes at
`_snap_to_ball` — does not enter into choosing an index.

Which makes the failure modes the whole risk. A model that names a
fifth ring out of four, or answers at 20% confidence, or returns prose
instead of JSON, must come back as "still unresolved" and leave the
refusal standing. Confidently wrong is the one outcome worse than no
answer at all.

    python3 tools/ball_picker_test.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import ai_tracer as A  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class Resp:
    def __init__(self, text):
        self.content = [Block(text)]


def ask(reply, n=3, raises=None):
    """pick_ball_among with the API stubbed to answer `reply`."""
    class Msgs:
        @staticmethod
        def create(**_kw):
            if raises:
                raise RuntimeError(raises)
            return Resp(reply)

    client = types.SimpleNamespace(messages=Msgs())
    saved = (A.have_ai_tracer, A._anthropic_client, A._resolve_model)
    try:
        A.have_ai_tracer = lambda: True
        A._anthropic_client = lambda: client
        A._resolve_model = lambda m=None: "test-model"
        return A.pick_ball_among(b"\xff\xd8\xff", n)
    finally:
        (A.have_ai_tracer, A._anthropic_client, A._resolve_model) = saved


def test_a_clear_answer_is_taken():
    got = ask('{"ball": 2, "confidence": 0.9, "why": "small and round"}')
    check("the chosen ring comes back", got["index"], 2)
    check("  with its confidence", got["confidence"], 0.9)
    check("  and its reason", got["why"], "small and round")
    check("  and no error", got["error"], None)


def test_declining_is_an_answer_and_is_respected():
    """Saying "I cannot tell" has to survive, or the prompt is a lie."""
    got = ask('{"ball": null, "confidence": 0, "why": "both look like turf"}')
    check("no ring is chosen", got["index"], None)
    check("  and it says why", got["error"], "model declined to choose")


def test_low_confidence_is_not_taken():
    got = ask('{"ball": 1, "confidence": 0.3, "why": "maybe"}')
    check("an unsure answer is refused", got["index"], None)
    check("  naming the threshold",
          "below 0.6" in (got["error"] or ""), True)
    # CONTROL: the same ring at high confidence IS taken, so the
    # refusal is the threshold and not the ring.
    check("control, the same ring at 0.9 is taken",
          ask('{"ball": 1, "confidence": 0.9, "why": "y"}')["index"], 1)


def test_a_ring_that_does_not_exist_is_refused():
    """Models do name a fifth candidate out of four.

    Taking it modulo anything would turn a clear failure into a
    plausible wrong ball, which is the single worst outcome here.
    """
    check("out of range is refused",
          ask('{"ball": 5, "confidence": 0.95, "why": "sure"}', n=3)["index"],
          None)
    check("  and zero is too",
          ask('{"ball": 0, "confidence": 0.95, "why": "sure"}', n=3)["index"],
          None)
    check("  negative as well",
          ask('{"ball": -1, "confidence": 0.95, "why": "x"}', n=3)["index"],
          None)
    # CONTROL: the top of the range is valid and taken.
    check("control, the last ring is in range",
          ask('{"ball": 3, "confidence": 0.95, "why": "x"}', n=3)["index"], 3)


def test_rubbish_does_not_become_a_ball():
    check("prose instead of JSON", ask("I think it is the second one")["index"],
          None)
    check("  a string index",
          ask('{"ball": "two", "confidence": 0.9, "why": "x"}')["index"], None)
    check("  a missing confidence",
          ask('{"ball": 2, "why": "x"}')["index"], None)
    check("  and an API that fell over",
          ask('{"ball": 2, "confidence": 0.9}', raises="boom")["index"], None)


def test_it_costs_nothing_when_there_is_nothing_to_decide():
    """One candidate is already the answer; no call is worth paying for."""
    called = []

    class Msgs:
        @staticmethod
        def create(**_kw):
            called.append(1)
            return Resp('{"ball": 1, "confidence": 1, "why": "x"}')

    client = types.SimpleNamespace(messages=Msgs())
    saved = (A.have_ai_tracer, A._anthropic_client)
    try:
        A.have_ai_tracer = lambda: True
        A._anthropic_client = lambda: client
        got = A.pick_ball_among(b"\xff\xd8\xff", 1)
    finally:
        (A.have_ai_tracer, A._anthropic_client) = saved
    check("one candidate makes no call", called, [])
    check("  and says so", got["error"], "nothing to choose between")


def test_no_ai_configured_is_not_an_error_worth_crashing_on():
    saved = A.have_ai_tracer
    try:
        A.have_ai_tracer = lambda: False
        got = A.pick_ball_among(b"\xff\xd8\xff", 3)
    finally:
        A.have_ai_tracer = saved
    check("it declines quietly", got["index"], None)
    check("  and says why", got["error"], "no AI configured")


for fn in (test_a_clear_answer_is_taken,
           test_declining_is_an_answer_and_is_respected,
           test_low_confidence_is_not_taken,
           test_a_ring_that_does_not_exist_is_refused,
           test_rubbish_does_not_become_a_ball,
           test_it_costs_nothing_when_there_is_nothing_to_decide,
           test_no_ai_configured_is_not_an_error_worth_crashing_on):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
