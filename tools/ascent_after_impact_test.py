#!/usr/bin/env python3
"""The sky is searched after the impact, and only because of one.

TWO RULES, AND #1916 BROKE BOTH.

  NOTHING BEFORE THE IMPACT FRAME. The window used to open a second
  before it, and the window itself came from the RIG's impact rather
  than from the cut the produce was about -- so on #1916 it opened
  four seconds early, found a ball there, and built the clip around a
  launch 273 frames BEFORE the strike it claimed to be a record of.
  A ball cannot leave before it is struck: every frame earlier is one
  where only another swing's ball can be found.

  AND NO SWING MEANS NO SEARCH. Steps 2 to 4 run once per swing, so
  with no swings there is nothing to produce -- and sweeping anyway
  reads every frame of the clip to make chains that can be matched to
  nothing. The sky is searched because a strike was heard, not in the
  hope of finding one.

    python3 tools/ascent_after_impact_test.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.admin as A  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class Row:
    id = 1916
    course_id = 1


def windows(swings, fps=60.0):
    rep = {"swings": swings}
    return A._ascent_windows(None, Row(), "x.mp4", fps, rep), rep


print(__doc__.strip().splitlines()[0])

print("\n1. the window opens ON the impact frame, never before it")
w, rep = windows([{"impact_frame": 1920}])
check("one window", len(w), 1)
check("it starts at the impact", w[0][0], 1920)
check("and runs forward", w[0][1] > w[0][0], True)
check("#1916's launch f1647 is NOT in it",
      any(lo <= 1647 <= hi for lo, hi in w), False)

print("\n2. one window per swing, each after its own impact")
w, _ = windows([{"impact_frame": 1920}, {"impact_frame": 2400},
                {"impact_frame": 900}])
check("three windows", len(w), 3)
check("in time order", [lo for lo, _ in w], [900, 1920, 2400])
check("each opens on its own impact",
      all(lo in (900, 1920, 2400) for lo, _ in w), True)
# AND THEY DO NOT REACH BACK over each other: swing 2's window must
# not contain swing 1's impact, or the sweep can hand swing 2 a chain
# that belongs to swing 1.
check("no window reaches back to an earlier impact",
      all(not (lo <= 900 <= hi) for lo, hi in w[1:]), True)

print("\n3. the run says what it searched and why")
_w, rep = windows([{"impact_frame": 1920}])
_note = rep.get("window") or {}
_txt = str(_note.get("detail") or _note)
check("it names step 1 as the source", "step 1" in _txt or
      "impact frame" in _txt)
check("...and says it looked only after them", "AFTER" in _txt)

print("\n4. a swing with no impact frame contributes no window")
w, _ = windows([{"impact_frame": None}, {"impact_frame": 1920}])
check("only the one that has one", len(w), 1)
check("...and it is that one's", w[0][0], 1920)

print("\n5. the pre-roll is gone from the impact path")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_fn = SRC[SRC.index("def _ascent_windows("):]
_fn = _fn[:_fn.index("\ndef ", 10)]
# The block that builds the window from step 1's frames, not the
# whole function -- `_around` is defined above it and still serves
# the fallback paths.
_step0 = _fn[_fn.index("_sw_frames = []"):
             _fn.index("# 1. The rig heard it.")]
check("step 0 does not subtract a pre-roll",
      "ASCENT_WIN_PRE_SEC" in _step0, False)
check("...and the fallback paths still have theirs",
      "ASCENT_WIN_PRE_SEC" in _fn, True)
check("...and it is reached before the rig's impact",
      _fn.index("rep or {}).get(\"swings\")")
      < _fn.index("# 1. The rig heard it."), True)

print("\n6. no swing, no sweep")
_run = SRC[SRC.index("def _ascent_run("):]
check("the run returns before sweeping",
      'if not (rep.get("swings") or []):' in _run, True)
check("...saying why", "nothing to search the sky" in _run, True)
check("...and it happens before the sweep call",
      _run.index('if not (rep.get("swings") or []):')
      < _run.index("_d3.sweep_ascents("), True)
check("the stage says it was not run", "NOT RUN. Step 1 found no swing"
      in _run, True)

print("\n7. the clip is cut from the IMPACT, not from the chain")
# THE BUG THE WHOLE WAY DOWN. `t0` was launch_frame - pre-roll, so a
# chain found in the wrong place moved the CLIP to the wrong place:
# #1916 cut f1527-f1781 for a strike at f1920. The impact frame is the
# one thing here that is not a guess -- it is what the cutter heard
# and what the audio cut was made around -- so the clip is that
# moment plus a pre-roll and cannot be anywhere else.
_SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_fp = _SRC[_SRC.index("def _d3_fast_produce("):]
_fp = _fp[:_fp.index("\ndef ", 10)]
check("t0 comes from the impact", "t0 = max(0.0, t_impact - D3_PRE_ROLL_SEC)"
      in _fp, True)
check("...not from the launch frame",
      "t0 = max(0.0, t_launch - D3_PRE_ROLL_SEC)" in _fp, False)
check("an empty chain does not raise on max()",
      'if pts else t_impact' in _fp, True)
check("and the end cannot be pulled back before the follow-through",
      "t_impact + D3_MIN_AFTER_IMPACT_SEC" in _fp, True)

from app.routers.admin import (  # noqa: E402
    D3_MIN_AFTER_IMPACT_SEC, D3_POST_TRACER_SEC, D3_PRE_ROLL_SEC,
)
from app.routers.production_flow import (  # noqa: E402
    FLOW_CUT_POST_SEC, FLOW_CUT_PRE_SEC,
)


def window(impact_f, chain_end_f, fps=60.0):
    t_i = impact_f / fps
    t0 = max(0.0, t_i - D3_PRE_ROLL_SEC)
    t_end = (chain_end_f / fps) if chain_end_f else t_i
    t1 = max(t_end + D3_POST_TRACER_SEC, t_i + D3_MIN_AFTER_IMPACT_SEC)
    return round(t0 * fps), round(t1 * fps)


for _label, _chain in (("no chain at all", None),
                       ("a chain after the impact", 2100),
                       ("#1916's chain, 273 frames EARLY", 1781)):
    _lo, _hi = window(1920, _chain)
    check("  %s -> f%d-f%d contains the impact" % (_label, _lo, _hi),
          _lo <= 1920 <= _hi, True)

print("\n8. and that clip lives inside the audio cut it came from")
# The cut is impact -4s..+8s. A produce that reached outside it would
# be showing frames the audio never pointed at, which is the whole
# complaint.
check("the pre-roll is inside the cut's lead",
      D3_PRE_ROLL_SEC <= FLOW_CUT_PRE_SEC, True)
check("the minimum tail is inside the cut's tail",
      D3_MIN_AFTER_IMPACT_SEC <= FLOW_CUT_POST_SEC, True)
# And the sweep cannot hand back a chain past the cut either, because
# it only looks ASCENT_WIN_POST_SEC forward from the impact.
from app.routers.admin import ASCENT_WIN_POST_SEC  # noqa: E402
check("the sweep cannot find a chain beyond the cut",
      ASCENT_WIN_POST_SEC + D3_POST_TRACER_SEC <= FLOW_CUT_POST_SEC, True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
