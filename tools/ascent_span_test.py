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
    ASCENT_SPAN_LEAD_SEC, ASCENT_SPAN_MAX_SEC, _ascent_span_verdict,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


FPS = 60.0
SPAN = int(round(FPS * ASCENT_SPAN_MAX_SEC))
LEAD = int(round(FPS * ASCENT_SPAN_LEAD_SEC))


def verdict(first, frm=None, lo=1286, hi=None):
    """`frm` defaults to `first` for the cases where only one frame is
    being varied."""
    return _ascent_span_verdict(first, first if frm is None else frm,
                                lo, hi, LEAD, SPAN, FPS)


# #1780's three accepted chains, read off the step-2 table. The "at"
# column is `from_frame` -- the chain's own back-run to the strike --
# and "back" is first_frame minus from_frame, so:
#
#   #1   at f1286  back  -4   26 pts   <- the right one
#   #2   at f1446  back  34    7 pts   <- 3.2s late
#   #3   at f1255  back  14    7 pts   <- an earlier swing's
C1 = dict(first=1282, frm=1286)
C2 = dict(first=1480, frm=1446)
C3 = dict(first=1269, frm=1255)

print("\n1. #1780's own three chains, against a strike at f1286")
check("#1 (26 points) IS this swing's ball leaving",
      verdict(**C1), "")
check("#2 is inside the span and refused for being late",
      bool(verdict(**C2)), True)
# 2.7s, not 3.2: the gap is measured from the chain's OWN back-run
# (f1446), which is the most generous frame it offers. Its first
# detection is a further 34 frames later still.
check("  ...saying how late", "2.7s after the strike" in (verdict(**C2) or ""),
      True)
check("  ...and still listed rather than hidden",
      verdict(**C2) is not None, True)
check("#3 belongs to an earlier swing", verdict(**C3), None)

# THE ONE-FRAME MISS. `first_frame` alone put #1 at f1282 against a
# floor of f1283 and dropped the only real chain in the clip.
print("\n1b. the chain's own back-run is what makes #1 survive")
check("judged on first_frame alone it starts before the strike",
      C1["first"] < 1286, True)
check("but its back-run lands on the strike exactly",
      C1["frm"], 1286)
check("so the rule must ask both frames, and does",
      _ascent_span_verdict(1282, 1286, 1286, None, 0, SPAN, FPS), "")

print("\n2. where the ceiling falls")
check("a chain %.1fs after the strike is kept"
      % ((SPAN - 1) / FPS), verdict(1286 + SPAN - 1), "")
check("one frame past the ceiling is refused",
      bool(verdict(1286 + SPAN + 1)), True)

print("\n3. the floor, which is now a quarter of a second")
check("a chain just inside the lead is kept (the strike is an estimate)",
      verdict(1286 - LEAD), "")
check("one before it belongs to an earlier swing",
      verdict(1286 - LEAD - 1), None)

print("\n4. a following swing still ends the span early")
check("a chain after the NEXT strike is that swing's, not this one",
      verdict(1400, lo=1286, hi=1390), None)
check("and one before it is still this swing's",
      verdict(1300, lo=1286, hi=1390), "")

print("\n5. a ceiling alone is not enough — the refusal must be VISIBLE")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("EVERY chain reaches this swing's considered table",
      "_cons.append(dict(" in _run
      and 'if _verdict is None:\n                _why.append(' in _run, True)
check("...carrying the reason it was not used",
      "_why.append(_verdict)" in _run
      and '"not this swing\'s: the strike is f%s%s, and this "' in _run, True)
check("...and that table is what 2a prints",
      '_st.get("n") != "2a"' in _run
      and '_ascent_chain_table(_s2.get("considered"))' in _run, True)
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
