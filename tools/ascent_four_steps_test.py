#!/usr/bin/env python3
"""Find/Produce ascents is four steps, and they run per swing.

THE ORDER WAS INVERTED. The run used to ask the cutter for a WINDOW,
sweep it, chain what it found, and work backwards from the chains to
how many balls had left. On the clip that prompted this, one strike
produced ELEVEN chains "seen leaving" -- eleven candidate clips for
one swing -- and which one produced came down to a list position.

The cutter is the thing that knows where the swings are, so it is now
asked that directly:

  1. the cutter finds the swings, and sets each impact frame
  2. per swing: the best chain above the skyline inside the cone,
     graded by the gates; failing that, the cone accumulator below it
  3. per swing: the descent
  4. per swing: the tee spot, the tracer and the clip

Everything else is parked, not deleted.

    python3 tools/ascent_four_steps_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
JSX = (ROOT / "frontend" / "src" / "pages"
       / "AdminProduction.jsx").read_text()

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def _live(text):
    """Only the code that RUNS.

    The removed logic is parked as comments rather than deleted, at the
    operator's request, so a plain substring search over this file
    finds `_fill_climb_to_ball(` in a line that begins with a `#` and
    concludes the run still calls it. Every assertion below is about
    what executes.
    """
    return "\n".join(ln for ln in text.split("\n")
                      if not ln.lstrip().startswith("#"))


_run_all = SRC[SRC.index("def _ascent_run("):]
_run_all = _run_all[:_run_all.index("\ndef ", 10)]
_run = _live(_run_all)

print("the run is four steps, numbered as the operator numbered them:")
_titles = re.findall(r'_stage\((\d), "([^"]+)"', _run)
check("four of them", len(_titles), 4)
check("  1 is the cutter", _titles[0][0] == "1"
      and "cutter" in _titles[0][1].lower(), True)
check("  2 is the ascent", _titles[1][0] == "2"
      and "ascent" in _titles[1][1].lower(), True)
check("  3 is the descent", _titles[2][0] == "3"
      and "coming down" in _titles[2][1].lower(), True)
check("  4 is the tracer", _titles[3][0] == "4"
      and "tracer" in _titles[3][1].lower(), True)
# EVERY STEP REPORTS ITS OWN TIME. `_stage` takes the seconds as its
# last argument, and a step that passes none reads as instant.
check("and every one of them is timed",
      len(re.findall(r"time\.perf_counter\(\) - _t\)", _run)) >= 4, True)

print()
print("STEP 1 — the cutter finds the swings, not a window:")
check("it asks the cutter for swings",
      "_ascent_cutter_swings(" in _run, True)
_s1 = _live(SRC[SRC.index("def _ascent_cutter_swings("):
                SRC.index("def _ascent_run(")])
# A POSITIVE CLAIM, not the absence of the old one: `_ct[0]` appears
# in this function's own docstring, explaining what it replaced, and
# an assertion that the string is gone fails on the explanation.
check("  it loops over every strike the cutter reported",
      bool(re.search(r"for _i, _t in enumerate\(sorted\(", _s1)), True)
check("  appending one swing for each",
      'out["swings"].append(sw)' in _s1, True)
# THE PEAK IS NOT THE IMPACT, and the whole reason step 1 is more than
# a call to the cutter is that the peak runs ~19 frames late at 60fps.
check("the measured frame is preferred",
      'measured_impact_frame' in _s1, True)
check("  and the peak less the audio lag is the fallback",
      "_heard - _lag" in _s1, True)
check("  with the swing saying which of the two it got",
      '"impact_from"' in _s1 and '"measured"' in _s1, True)

print()
print("STEP 2 — one ascent per swing, graded by the gates:")
check("each swing gets its own span",
      "_spans.append((_f0, _f1))" in _run, True)
check("  chains are graded fewest-refusals then longest",
      bool(re.search(r'len\(c\.get\("why"\) or \[\]\), '
                     r'-int\(c\.get\("n_points"\)', _run)), True)
check("  and exactly one survives", "_best = sorted(_cands, key=_grade)[0]"
      in _run, True)
# A SWING THAT KEEPS NOTHING IS NOT A SWING WITH NO ANSWER.
check("nothing above the skyline falls to the accumulator",
      "_ascent_cone_accumulate(" in _run, True)
check("  and the losers are reported, not dropped silently",
      '_sw["considered"] = sorted(' in _run, True)

print()
print("STEP 4 — the tee spot is the ball that was watched leaving:")
# THE RED RING ON THE TILES. An observation of the pixel the ball left
# from beats any extrapolation of it.
check("the measured ball is preferred",
      '"measured — the ball was watched sitting at this spot"' in _run
      or "measured — the ball was watched sitting" in _run, True)
check("  the chain run back is the fallback",
      "_ascent_tee_spot(" in _run, True)
check("  and the card is told which one it got",
      '_entry["ball_from"]' in _run, True)
check("step 1's frame beats the chain's own back-run",
      '_entry["impact_frame"] = int(_sw["impact_frame"])' in _run, True)
check("  keeping the estimate beside it",
      '_entry["impact_frame_est"]' in _run, True)

print()
print("and what was removed is PARKED, not deleted:")
# The operator asked for it commented out rather than gone, so the
# reasoning in it survives for whoever needs it next.
for _fn in ("_fill_climb_to_ball", "_ascent_apply_ai_launch",
            "_ascent_pin_rig_impact", "_collapse_one_ball"):
    check("  %s is still defined" % _fn, ("def %s(" % _fn) in SRC, True)
    check("    ...but the run no longer calls it",
          ("    %s(" % _fn) in _run or ("= %s(" % _fn) in _run, False)
check("the old stage 1 is kept as comment",
      "PARKED: THE OLD STAGE 1" in SRC, True)
check("  and so are the stage-3 extras",
      "PARKED: the stage-3 extras" in SRC, True)

print()
print("and the page shows every swing, with its pictures:")
check("there is a per-swing panel",
      "function AscentSwingSteps(" in JSX, True)
check("  it is rendered", "<AscentSwingSteps rep={rep} />" in JSX, True)
check("  with the tiles the step drew", "frames_image_url" in JSX, True)
check("  and the graded chains it did not keep",
      "sw.considered" in JSX, True)
# THE SUB-STEP TIMINGS FOLLOW THE STEP THEY BELONG TO. The descent's
# own breakdown was keyed to stage 4; the descent is step 3 now, and a
# stale key does not error -- the breakdown just stops appearing.
check("the descent's sub-steps are keyed to step 3",
      "3: rep?.descent_stages" in JSX, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — four steps, per swing, each one timed")
