#!/usr/bin/env python3
"""The same composite must get the same verdict, on both paths.

TWO PATHS REACH THE SWING JUDGE. The rig asks through
/verify-swing, deciding whether to spend ~7 MB of a cellular link on a
clip. Test Cut asks from the admin page, re-running it on its own
composite of the same clip. They are the same function, the same
prompt and the same model -- `judge_swing_heat_image` both times.

On upload 1203 they returned OPPOSITE verdicts for the same instant:
the rig SWING at 4.83s, Test Cut not-a-swing at MEDIUM confidence at
4.83s. The two composites were put side by side and are the same
picture -- same framing, same heat, same crop.

So nothing was left to differ but the sampling, which was unset and
therefore 1.0. A borderline composite was a coin weighted by its own
ambiguity. From the outside that reads as "the rig judges easier"; it
is really "the judge was asked twice".

This pins it and keeps it pinned. The verdict the rig acts on has to
be the verdict Test Cut will show for the same image, or a
disagreement between them means nothing and cannot be investigated.

    python3 tools/judge_determinism_test.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


from backend.app.services import ai_tracer  # noqa: E402


class FakeMessages:
    def __init__(self, reply):
        self.kwargs = None
        self._reply = reply

    def create(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(content=[
            types.SimpleNamespace(type="text", text=self._reply)])


class FakeClient:
    def __init__(self, reply):
        self.messages = FakeMessages(reply)


def ask(reply, tmp):
    """Run the real judge against a stubbed Anthropic client."""
    img = tmp / "heat.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0not-really-a-jpeg")
    fake = FakeClient(reply)
    _old_client = ai_tracer._anthropic_client
    _old_has = ai_tracer.HAS_ANTHROPIC
    import os
    _had_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "test-key-not-a-real-one"
    ai_tracer.HAS_ANTHROPIC = True
    ai_tracer._anthropic_client = lambda: fake
    try:
        out = ai_tracer.judge_swing_heat_image(img)
    finally:
        ai_tracer._anthropic_client = _old_client
        ai_tracer.HAS_ANTHROPIC = _old_has
        if _had_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = _had_key
    return out, fake.messages.kwargs


import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    TMP = Path(_td)

    print("the call the judge actually makes:")
    out, kw = ask('{"is_swing": true, "confidence": "high", '
                  '"reason": "club plume above the head"}', TMP)
    check("it is pinned to deterministic sampling",
          kw.get("temperature"), 0)
    check("...and still asks the swing-heat prompt",
          kw["system"][0]["text"] is ai_tracer._JUDGE_SWING_HEAT_PROMPT, True)
    check("...with the image attached",
          kw["messages"][0]["content"][1]["type"], "image")

    print()
    print("the verdict is read straight through:")
    check("a swing is a swing", out["is_swing"], True)
    check("...with its confidence", out["confidence"], "high")
    check("...and its reason", out["reason"], "club plume above the head")

    out, _ = ask('{"is_swing": false, "confidence": "medium", '
                 '"reason": "no thin vertical club plume above the head"}',
                 TMP)
    # UPLOAD 1203's OWN VERDICT, as Test Cut rendered it. The rig has
    # to be able to reach this same answer from this same picture.
    check("not-a-swing at medium is not softened", out["is_swing"], False)
    check("...and keeps its confidence", out["confidence"], "medium")

    print()
    print("what must NOT become a refusal:")
    # NULL IS NOT FALSE. A judge that cannot answer must never cost a
    # swing -- the rig uploads on null exactly as it uploads on true.
    out, _ = ask('not json at all', TMP)
    check("an unparseable reply is 'could not tell'", out["is_swing"], None)
    out, _ = ask('{"confidence": "low"}', TMP)
    check("a reply with no verdict is too", out["is_swing"], None)
    out, _ = ask('{"is_swing": "yes"}', TMP)
    check("and so is a non-boolean verdict", out["is_swing"], None)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
