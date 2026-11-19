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


def _ascent_hold():
    """The frames the ball's spot must stay empty, as configured."""
    m = re.search(r"ASCENT_GONE_HOLD_FRAMES = (\d+)", src)
    return int(m.group(1)) if m else 0
_i = src.index("def _ascent_ai_launch")
_j = src.index("\ndef ", _i + 10)
stage1 = src[_i:_j]

print("stage 1 derives a bound from the tiles:")
# THIS TEST HELD THE BUG IN PLACE. It used to require the clamp to
# read `_t.get("in_rule")` -- which is not "the ball was on this
# tile" but "this tile got a vote in the two-of-three rule", True on
# every readable non-strike tile. So the sentence the clamp put on
# the panel, "the ball was still there at X and gone by Y", was not
# what it had measured, and a test asserted that it stay that way.
#
# A second copy of the same loop lived here too, so two brackets
# could disagree about one instant. Both now come from _tile_bracket,
# which is tested on its own behaviour in tools/tile_bracket_test.py.
check("the clamp uses the one bracket, not its own copy",
      "_seen_at, _gone_at = _tile_seen, _tile_gone" in stage1)
check("  so there is no second loop over the tiles",
      '"frames") or []' in stage1, False)
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
print("the watch is bounded by the tiles, not by a fixed margin:")
# A FIXED MARGIN CANNOT WORK IN EITHER DIRECTION. Too narrow and the
# window starts after the ball has gone, so the first frame it looks
# at reads as the impact (f797 against a real f789). Too wide and it
# reaches into the DOWNSWING, where the club crossing the ball's pixel
# empties it just as leaving does (f773 against the same f789). Only
# the tiles bracket the strike without being fooled by either.
# ── IT STARTS AT THE FIRST TILE, NOT THE LAST SIGHTING ─────────────
#
# Starting at the last tile the ball was ON is the tightest window
# that can still bracket the strike, and on a clip where the tiles
# were themselves late EVERY frame it looked at read "gone". A watch
# that never sees the ball present has no `last_seen` to split the
# difference with, so it refuses and the frame falls back to the peak
# less the lag — and the card's two strips then showed different
# windows, which is why they could not be compared.
check("the window starts at the FIRST of the six tiles",
      "_tile_first if _tile_first is not None else _tile_seen" in src)
# REACHING BACK THAT FAR IS WHAT PRODUCED f773, and `gone_hold_frames`
# is what makes it safe: a club crossing the ball's pixel empties it
# for a frame or three exactly as leaving does, and the ball being
# there again straight after is the only thing that tells them apart.
check("  and the spot must STAY empty to count as gone",
      "gone_hold_frames=ASCENT_GONE_HOLD_FRAMES" in src)
check("  by more than the frame or two a club takes to cross it",
      _ascent_hold() >= 4)
check("  and the watch is given it",
      "before_sec=_dep_before, after_sec=_dep_after" in src)
check("  with a fallback when there is no bracket",
      "_dep_before, _dep_after = 0.2, 0.5" in src)
check("  and the window reported is the one searched",
      "- _dep_before) * _dw_fps" in src)
check("no fixed pre-strike margin survives",
      "ASCENT_DEPART_BEFORE_SEC" in src, False)

print()
print("the bracket is a named thing, tested on its own:")
# WHAT IT IS BUILT ON is the whole question -- `in_rule` reads like
# "this tile saw the ball" and means "this tile got a vote in the
# two-of-three rule", True on every readable non-strike tile. A
# bracket on that never closes, the watch falls back to its default
# fifth of a second, and that window is where f797 came from.
#
# That is a behaviour, not a string, so it lives in its own test:
#   python3 tools/tile_bracket_test.py
check("the bracket is its own function, so it can be tested",
      "def _tile_bracket(addr):" in src)
check("  and it is not built on `in_rule`",
      '_t.get("in_rule")' in src, False)
check("  it asks whether a detection sat at the ball's spot",
      "ASCENT_TILE_SAME_BALL_PX" in src)
check("  gone must come after seen",
      "_tile_gone > _tile_seen" in src)
check("  decided once, so window and fallback agree",
      src.count("_braced") >= 3)
check("  and its reasoning reaches the report",
      'out["tile_bracket_why"]' in src)

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
