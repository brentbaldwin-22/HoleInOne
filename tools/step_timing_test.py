#!/usr/bin/env python3
"""Find ascents says how long each of its steps took.

TWO NUMBERS WERE DOING THE WORK OF NINE.

The ascent run has always reported its four stages, and on a find-only
run two of them are the whole clock: stage 1 (the tee sweep) and stage 3
(the green pass). "Stage 1 took 94s" is the question restated, not an
answer -- inside it are a detection pass that decodes frames, a linker
that reads nothing, two re-read passes that SEEK, and a set of pictures,
and those four want completely different fixes.

So each stage now reports its own steps:

  * `sweep_ascents` splits what it used to call `linking` into the walk,
    the fits, the stitch (which re-reads every hole) and the chain
    extension (which re-reads too) -- and finally times the gates, which
    no lap on that clock had ever covered.
  * `_ascent_run` carries those out as `scan_stages`, with the window
    calculation and the picture drawing either side of them.
  * `_ascent_descents` records its own six: opening the green, placing
    the windows, the segmentation pass, the matching, the resting-ball
    plates and the pictures.
  * The modal renders them indented under the stage they belong to,
    shared against THAT STEP rather than against the whole run.

    python3 tools/step_timing_test.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print("  %-64s %s" % (name, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAIL.append(name)


# ── 1. the sweep's own clock, measured by running it ─────────────────
#
# A REAL SWEEP, NOT THE SOURCE. The laps either measure the calls they
# sit around or they do not, and only a run can say which -- a lap
# started one line too early reads perfectly in the text.
print("the sweep itemises its own time:")
try:
    import cv2  # type: ignore
    import numpy as np

    from app.services import debug3 as d3
except Exception as exc:  # noqa: BLE001
    print("  SKIPPED — opencv/numpy not importable here (%s)" % exc)
    _timing = None
else:
    _dir = Path(tempfile.mkdtemp())
    _clip = _dir / "tee.avi"
    W, H, N = 320, 240, 45
    _vw = cv2.VideoWriter(str(_clip), cv2.VideoWriter_fourcc(*"MJPG"),
                          30.0, (W, H))
    _rng = np.random.default_rng(3)
    for i in range(N):
        _fr = np.full((H, W, 3), 30, np.uint8)
        # A LITTLE SENSOR NOISE. A perfectly still frame gives the
        # detector nothing to do and the laps nothing to measure.
        _fr += _rng.integers(0, 4, (H, W, 3), dtype=np.uint8)
        if 5 <= i < 35:
            cv2.circle(_fr, (int(150 + 1.2 * (i - 5)),
                             int(170 - 4.6 * (i - 5))), 3,
                       (255, 255, 255), -1)
        _vw.write(_fr)
    _vw.release()
    _out = d3.sweep_ascents(_clip, 30.0)
    _timing = _out.get("timing") or {}
    print("    %s, total %ss" % (_timing, _out.get("seconds")))
    for _k in ("detect", "linking", "walk", "fits", "stitch", "extend",
               "judge"):
        check("it reports %s" % _k, _k in _timing, True)
    # THE OLD TWO KEYS STILL MEAN WHAT THEY MEANT. `linking` is
    # everything after detection, and the four new keys under it add up
    # to it -- so the sweep-map caption, which prints whatever is in
    # this dict, is not quietly double-counting.
    _parts = sum(float(_timing.get(k) or 0.0)
                 for k in ("walk", "fits", "stitch", "extend"))
    check("the four steps inside linking add up to it (±0.03s)",
          abs(_parts - float(_timing.get("linking") or 0.0)) <= 0.03, True)
    # AND THE WHOLE RUN IS ACCOUNTED FOR. Before the judge lap the
    # gates were outside every lap on this clock: the sweep's own
    # `seconds` included them, nothing in the breakdown did, and the
    # difference between the two was the one number with no name.
    _named = sum(float(_timing.get(k) or 0.0)
                 for k in ("detect", "linking", "judge"))
    check("detect + linking + judge covers the sweep's own total (±0.1s)",
          abs(_named - float(_out.get("seconds") or 0.0)) <= 0.1, True)
    check("the gates were timed at all", "judge" in _timing
          and float(_timing["judge"]) >= 0.0, True)

_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")


def _block(start_marker: str, end_marker: str) -> str:
    _i = _src.index(start_marker)
    return _src[_i:_src.index(end_marker, _i)]


# ── 2. stage 2 carries them out ──────────────────────────────────────
print("stage 2 reports the sweep's steps under it:")
_s1 = _block("    _ascent_trim_considered(sweep)",
             '    _asc = list(sweep.get("ascents") or [])')
check("the run builds scan_stages", 'rep["scan_stages"]' in _s1, True)
for _n, _what in (("2a", "the window calculation"),
                  ("2b", "detection"),
                  ("2c", "the walk"),
                  ("2d", "the fits"),
                  ("2e", "the stitch and its re-reads"),
                  ("2f", "following stopped chains"),
                  ("2g", "the gates"),
                  ("2h", "drawing the pictures")):
    check("%s is %s" % (_n, _what), '"%s"' % _n in _s1, True)
# MEASURED, NOT RETYPED. Every one of the sweep's own laps is read off
# the sweep's report -- a number typed into the page by hand describes
# the run it was typed during and no other.
check("the sweep's laps are read from its own timing dict",
      _s1.count('_sw_t.get(') >= 5, True)
check("the window lap is measured around _ascent_windows",
      bool(re.search(r"_t_sub = time\.perf_counter\(\)\n"
                     r"    _win = _ascent_windows\(", _src)), True)
check("the picture lap is measured around the four image helpers",
      "_sec_pics = time.perf_counter() - _t_sub" in _s1, True)
# AND THE SWEEP'S REMAINDER IS NAMED. The laps are taken inside the
# same call that reports `seconds`, so a gap between them is a step
# nobody is measuring -- which is the only way a timing table finds
# something its author did not think of.
check("a gap between the laps and the sweep's own total is shown",
      '"2z"' in _s1 and "Unattributed, inside the sweep" in _s1, True)
check("...and it is the sweep's clock it is measured against",
      'sweep.get("seconds")' in _s1, True)
check("steps that took no time are left out",
      'if x["seconds"] > 0.004' in _s1, True)

# ── 3. stage 4 does the same ─────────────────────────────────────────
print("stage 4 reports the green pass's steps under it:")
_s3 = _block("def _ascent_descents(", "\ndef _ascent_window_views(")
for _n, _what in (("4a", "opening the green and reading the clocks"),
                  ("4b", "placing the flight windows"),
                  ("4c", "the descent search"),
                  ("4d", "matching swings to landings"),
                  ("4e", "drawing the window views"),
                  ("4f", "the resting-ball plates")):
    check("%s is %s" % (_n, _what), '"%s"' % _n in _s3, True)
# A RUN THAT GIVES UP STILL SAYS WHAT IT DID. Three of this function's
# exits are early ones -- no green file, no impact frame, nothing found
# -- and those are the runs whose timing is most worth reading.
check("the laps are recorded as they finish, not at the end",
      _s3.count("_lap()") >= 6, True)
check("the no-window exit reports what it reached",
      bool(re.search(r'out\["reason"\] = "no swing had an impact frame',
                     _s3)) and _s3.count('_sub("4e"') >= 2, True)
check("every early exit's rows survive on the report",
      '"stages": []' in _s3, True)
check("the expensive step says it scales with the window, not the clip",
      "scales with the seconds" in _s3, True)
check("the run carries them out as descent_stages",
      'rep["descent_stages"] = [x for x in (_desc.get("stages") or [])'
      in _src, True)

# ── 4. the modal shows them under the step they belong to ────────────
print("the modal renders each step's own steps:")
_jsx = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text(encoding="utf-8")
_card = _jsx[_jsx.index("function ProduceStepSummary("):
             _jsx.index("function SwingCard(")]
check("stage 2's steps come from scan_stages",
      "2: rep?.scan_stages || []" in _card, True)
check("stage 4's steps come from descent_stages",
      "4: rep?.descent_stages || []" in _card, True)
check("they are indented under their own stage row",
      "r.subs?.length > 0" in _card, True)
# SHARE OF THE STEP, NOT OF THE RUN. "The stitch was 70% of stage 1"
# points at something to change; "9% of the button" does not.
check("the share is of the step, not of the whole run",
      "% of this step" in _card and "/ r.sec" in _card, True)
check("a sub-step's own one-line explanation is shown",
      "{sub.detail}" in _card, True)
check("sub-steps too small to matter are dropped",
      "(x.seconds || 0) > 0.05" in _card, True)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("all good — every step of find ascents reports its own time")
