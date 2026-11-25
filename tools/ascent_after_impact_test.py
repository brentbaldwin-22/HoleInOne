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

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
