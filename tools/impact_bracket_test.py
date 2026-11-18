#!/usr/bin/env python3
"""The six tiles bracket the strike; the watch refines inside it.

TWO MEASUREMENTS OF ONE INSTANT, AND THEY DISAGREED. On the swing
that prompted this the six tiles -- 0.2s apart, each judged by the
same detector through the same stance box -- said the ball sat at
-0.20s and was gone by 0.00s, putting the strike inside that fifth of
a second. The frame-by-frame departure watch said f824, which is two
tiles LATER than the tile the ball was already gone on.

That failure is documented in the source: "the tee, the divot and the
shadow stay bright after the ball has gone, so the watch frequently
never concluded and the impact frame came back later than the
strike". The watch is the finer instrument; the tiles are the more
robust one. So the tiles bound it and the watch refines within.

The clamp may only ever pull the frame EARLIER, which is the
harmless direction -- it is where the tracer's line begins, and a
line that starts a fraction early reads as a tracer while one that
starts late reads as a bug.

    python3 tools/impact_bracket_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
_i = src.index("def _ascent_ai_launch")
_j = src.index("\ndef ", _i + 10)
stage1 = src[_i:_j]

print("stage 1 derives a bound from the tiles:")
check("it reads the per-tile results", '"frames") or []' in stage1)
check("  using whether the ball was in the rule on each",
      '_t.get("in_rule")' in stage1)
check("  and records the bound it derived",
      '"measured_impact_bound"' in stage1)
check("  with the two tiles it came from",
      '"measured_impact_bound_why"' in stage1)

print()
print("the clamp only ever pulls EARLIER:")
# The comparison must be one-directional. A clamp that could also push
# the frame later would be the bug it exists to prevent.
_blk = stage1[stage1.index('"measured_impact_bound"'):]
check("it acts only when the watch is LATER than the bound",
      "> _bound" in _blk)
check("  never when it is earlier", "< _bound" not in _blk)
check("  and keeps the unclamped number",
      '"measured_impact_unclamped"' in _blk)

print()
print("and it says so, rather than silently moving the number:")
check("it logs the clamp", "clamped" in _blk)
prod = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text()
check("the page shows it", "measured_impact_unclamped" in prod)
check("  and the reason", "measured_impact_bound_why" in prod)


def _in_closed_details(text, idx):
    body = re.sub(r'\{/\*.*?\*/\}', '', text[:idx], flags=re.S)
    d = 0
    for m in re.finditer(r'<details([^>]*)>|</details>', body):
        d += -1 if m.group(0).startswith("</") else 1
    return d > 0


check("  outside the closed fold",
      _in_closed_details(prod, prod.index("measured_impact_unclamped")),
      False)

print()
print("the measured frame still reaches the clip:")
check("the merge takes it", '_ai.get("measured_impact_frame")' in src)
check("  keeping the estimate beside it", '"impact_frame_est"' in src)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the tiles bound it, the watch refines it")

# ── THE WINDOW MUST START BEFORE THE BALL GOES ──────────────────────
#
# THE EIGHT-FRAME ERROR. The departure watch looked from `at_sec`
# minus 0.2s, which on the reported swing is f796 -- and the ball left
# at f789. So it never saw the ball PRESENT ("last seen fNone" on the
# panel), found the spot empty on the first frame it looked at, and
# reported that frame as the impact. f797 was not a measurement; it
# was where we started looking. And `at_sec` is itself the audio peak
# less a guessed lag, so a fifth of a second was never enough margin
# for the thing it is meant to bracket.
print()
print("the departure watch starts well before the strike:")
check("the margin is a named constant",
      "ASCENT_DEPART_BEFORE_SEC" in src)
check("  wide enough to see the ball still there",
      float(re.search(r"ASCENT_DEPART_BEFORE_SEC = ([\d.]+)",
                      src).group(1)) >= 0.5)
check("  and the watch is given it",
      "before_sec=ASCENT_DEPART_BEFORE_SEC" in src)
check("  and the window reported matches the one searched",
      "- ASCENT_DEPART_BEFORE_SEC) * _dw_fps" in src)

print()
print("the strike is split between seen and gone, as asked:")
_s1b = src[src.index("def _ascent_ai_launch"):]
_s1b = _s1b[:_s1b.index("\ndef ", 10)]
check("it takes the midpoint", "(int(_last) + int(_gone))" in _s1b)
check("  recording both bounds",
      '"measured_impact_seen"' in _s1b
      and '"measured_impact_gone"' in _s1b)

print()
print("and NOTHING SEEN means NOTHING MEASURED:")
# The failure that made f797 beat a correct estimate: a window that
# began after the ball had gone still produced a number, and the
# number was the window's own first frame.
check("a watch that never saw the ball reports no frame",
      'out["measured_impact_frame"] = None' in _s1b)
# A CONTIGUOUS fragment: the sentence is split across a line
# continuation in the source, so the whole phrase never appears.
check("  and says it is not a measurement",
      "NOT measured:" in _s1b)
check("  naming the frame it refused to use",
      "started looking" in _s1b)

if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — and the window brackets what it measures")
