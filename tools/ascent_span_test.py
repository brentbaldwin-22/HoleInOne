#!/usr/bin/env python3
"""A swing's ascent span must have a CEILING, not just a floor.

#1780, measured end to end:

    step 1 impact        f1286   (tee camera, 60fps)
    chain produced from  f1480   -- 194 frames, 3.2s later
    ball landed          f1574   (green camera, clocks synced to 0.01s)

f1286 gives a 4.8-second flight for a 145-yard shot. f1480 gives 1.6.
The ball was most of the way to the green before the chain the clip
was drawn from even started.

HOW IT GOT THERE: a swing's span ran from its own impact frame to the
NEXT swing's impact frame --

    _f1 = (swings[k + 1]["impact_frame"] if k + 1 < len(swings)
           else None)
    ...
    return hi is None or _ff < hi

-- so the LAST swing's span had no end, and on a single-swing clip
that is the only swing. Every chain in the remaining twenty seconds
competed for "the ball leaving the tee", graded on gate refusals and
length alone. The real ascent picks up one refusal, a clean chain of
the ball coming down (or a bird, or a cart) picks up none, and the
clean one wins. That is exactly "it found all the right chains and
still produced the wrong one".

    python3 tools/ascent_span_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    ASCENT_SPAN_MAX_SEC, ASCENT_SWING_SPAN_SLACK_FRAMES,
    _ascent_span_verdict,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


FPS = 60.0
SPAN = int(round(FPS * ASCENT_SPAN_MAX_SEC))


def verdict(first, lo=1286, hi=None):
    return _ascent_span_verdict(first, lo, hi, SPAN, FPS)


print("\n1. #1780, with the numbers it actually ran on")
check("the real ascent is this swing's", verdict(1292), "")
check("a chain on the impact frame itself counts", verdict(1286), "")
check("f1480 is inside the span and REFUSED", bool(verdict(1480)), True)
check("...and says how late it was",
      "3.2s after the strike" in (verdict(1480) or ""), True)
check("...rather than being hidden entirely",
      verdict(1480) is not None, True)

print("\n2. where the ceiling falls")
check("a chain %.1fs after the strike is kept"
      % ((SPAN - 1) / FPS), verdict(1286 + SPAN - 1), "")
check("one frame past the ceiling is refused",
      bool(verdict(1286 + SPAN + 1)), True)

print("\n3. the floor is unchanged")
check("a chain a little before impact is kept (the strike is an "
      "estimate)", verdict(1286 - ASCENT_SWING_SPAN_SLACK_FRAMES), "")
check("one well before it belongs to no swing here",
      verdict(1286 - ASCENT_SWING_SPAN_SLACK_FRAMES - 1), None)

print("\n4. a following swing still ends the span early")
check("a chain after the NEXT strike is that swing's, not this one",
      verdict(1400, lo=1286, hi=1390), None)
check("and one before it is still this swing's",
      verdict(1300, lo=1286, hi=1390), "")

print("\n5. a ceiling alone is not enough — the refusal must be VISIBLE")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("the late chains still reach the considered table",
      "_cons.append({" in _run and "_verdict is None:" in _run, True)
check("...carrying the reason they were not used",
      "_why.append(_verdict)" in _run, True)
check("but never reach the candidates",
      bool(re.search(r"else:\s*\n\s*_cands\.append\(_c\)", _run)), True)
check("and the printed span shows the ceiling",
      "_f0 + _span_max" in _run, True)

print("\n6. the produce must not overwrite the measured impact frame")
_fp = SRC[SRC.index("def _d3_fast_produce("):]
_fp = _fp[:_fp.index("\ndef ", 10)]
check("the swing record takes step 1's frame when there is one",
      'int(sw["impact_frame"])' in _fp, True)
check("...with the launch frame kept beside it, not instead of it",
      '"launch_frame": launch_f,' in _fp, True)
check("nothing writes the launch frame AS the impact frame",
      '"impact_frame": launch_f,' not in _fp, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
