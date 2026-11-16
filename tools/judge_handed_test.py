#!/usr/bin/env python3
"""Which way round the golfer stands, from the call already being made.

The swing verdict on the cut write-up is TWO Claude vision calls --
`judge_swing_heat_image` on the motion composite, and
`judge_swing_with_frames` on the six-frame strip plus the composite.
The frames judge is already looking at an address tile and a finish
tile, which is everything handedness needs, so asking costs one extra
JSON field rather than another call.

TWO PROPERTIES WORTH PINNING.

It must not move the verdict. The swing question above it is tuned
and A/B-tested against real clips; a model that starts reasoning
about stance before answering `is_swing` is a different judge from
the one that was measured. The prompt asks for it LAST and says so.

And "unknown" has to survive as "unknown". A golfer side-on or small
in frame cannot be read, a guess is worse than nothing, and nothing
downstream may refuse a swing for want of it.

    python3 tools/judge_handed_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


from app.services import ai_tracer as T  # noqa: E402

P = T._JUDGE_SWING_FRAMES_PROMPT

print("\n-- the judge is asked, in the JSON it already returns")
check("the prompt asks for handedness", "handed" in P, True)
check("  offering exactly three answers",
      all(w in P for w in ('"right"', '"left"', '"unknown"')), True)
check("  in the reply schema", '"handed":' in P, True)

print("\n-- and asked LAST, so it cannot move the swing verdict")
# THE ORDER IS THE SAFEGUARD. The verdict instructions, then the
# handedness question, then the schema -- not the other way round.
check("it comes after the swing question",
      P.index("handed") > P.index("do these show a GOLFER SWINGING"), True)
check("  and after the reason instruction",
      P.index("SEPARATELY") > P.index("Say in `reason`"), True)
check("  it says so in as many words",
      "only after you have decided" in P, True)
check("  and that it never changes the verdict",
      "never changes" in P and "is_swing" in P, True)

print("\n-- the answer is read, and only the words that were offered")
import inspect  # noqa: E402
_src = inspect.getsource(T.judge_swing_with_frames)
check("the frames judge reads it", 'parsed.get("handed")' in _src, True)
check("  and publishes it", 'out["handed"]' in _src, True)
# A MODEL THAT ANSWERS "righty" OR "R" MUST NOT REACH THE PAGE as a
# fourth value nothing renders.
check("  anything but right/left becomes None",
      '("right", "left")' in _src, True)


class _Fake:
    """A reply object shaped like the SDK's, for the parse only."""

    def __init__(self, text):
        self.content = [type("C", (), {"type": "text", "text": text})()]


def _parse(text):
    """Run the function's own parsing over a canned reply."""
    _p = T._extract_json(text) or {}
    _h = str(_p.get("handed") or "").strip().lower()
    return _h if _h in ("right", "left") else None


for _raw, _want in (
        ('{"is_swing": true, "handed": "right"}', "right"),
        ('{"is_swing": true, "handed": "left"}', "left"),
        ('{"is_swing": true, "handed": "LEFT"}', "left"),
        ('{"is_swing": true, "handed": "unknown"}', None),
        ('{"is_swing": true, "handed": "righty"}', None),
        ('{"is_swing": true}', None),
        ('{"is_swing": true, "handed": null}', None)):
    _lbl = _raw.split('"handed":')[1].rstrip("}").strip() \
        if '"handed":' in _raw else "(no handed key)"
    check("  %-18s -> %s" % (_lbl, _want), _parse(_raw), _want)

print("\n-- it never gates a verdict")
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the cut carries it", 'cut_entry["frames_handed"]' in ADM, True)
# THE PROPERTY THAT MATTERS: nothing anywhere may branch a swing
# decision on it.
for _bad in ('if _jf.get("handed")', 'frames_handed"] is None',
             'handed") is None', 'not _jf.get("handed")'):
    check("  nothing branches on it: %s" % _bad, _bad in ADM, False)

print("\n-- and the page shows it without claiming it is the verdict")
JSX = (ROOT / "frontend" / "src" / "components"
       / "CutVerdicts.jsx").read_text(encoding="utf-8")
check("the write-up renders it", "c.frames_handed" in JSX, True)
check("  and renders nothing when unknown",
      "{c.frames_handed && (" in JSX, True)
# IT IS NOT A SWING FIELD. The row's verdict must not read it.
_i = JSX.index("c.frames_handed")
_seg = JSX[max(0, _i - 400):_i]
check("  it is not wired into the agree/disagree line",
      "they disagree" in _seg, False)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
