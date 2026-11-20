#!/usr/bin/env python3
"""Every window a swing will be searched in, on step 1, in the open.

ASKED FOR: "in our swing table here, can we also identify which frames
will be swept for the ascent search, which, the before/after times for
the 'find ball on green' search, and which frames be swept for descent
search. all for each swing. and put it outside the collapsed section."

THE THREE LIVE ON DIFFERENT CLOCKS, which is the trap. The ascent is
swept on the TEE clip; the ball-on-green test and the descent sweep
are on the GREEN clip, at its own frame rate and with an offset
between the two cameras. Frame numbers from both in one table, with
nothing saying which is which, invite subtracting one from the other.

NOTHING IS RECOMPUTED HERE. Each span is read off what the run
actually did — a table that re-derives a window can disagree with the
search it describes, and then it is worse than no table at all.

    python3 tools/swing_windows_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    _ascent_swing_windows, _swing_window_table,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


FPS, GFPS = 60.0, 50.0
REP = {
    "swings": [{"impact_frame": 1647}, {"impact_frame": 4703}],
    "clips": [
        {"n": 1, "rest_window": [26.1, 34.4], "rest_windows_tried": 2,
         "landing_window_sec": [27.4, 36.2],
         "landing_window_from": "impact to the end of the green cut"},
        {"n": 2, "rest_window": [77.2, 85.5],
         "landing_window_sec": [78.5, 87.3],
         "landing_window_from": "impact to the end of the green cut"},
    ],
}
DESC = {"green_fps": GFPS}
# The cutter's windows: peak -1s .. +4s around each strike.
WIN = [(1587, 1887), (4643, 4943)]

rows = _ascent_swing_windows(REP, DESC, WIN, FPS)

print("\n1. one row per swing, with all three windows")
check("two swings in, two rows out", len(rows), 2)
r1 = rows[0]
check("the ascent window is the one holding this strike",
      r1["ascent_frames"], [1587, 1887])
check("...in tee seconds too", r1["ascent_sec"], [26.45, 31.45])
check("the ball-on-green pair is carried straight through",
      r1["green_ball_sec"], [26.1, 34.4])
check("the descent window in green seconds",
      r1["descent_sec"], [27.4, 36.2])
check("...and in GREEN frames, at the green's own rate",
      r1["descent_frames"], [int(27.4 * GFPS), int(36.2 * GFPS)])
check("the second swing takes the second window",
      rows[1]["ascent_frames"], [4643, 4943])

print("\n2. matched by content, not by position")
# A window list in the other order must still find the right one: the
# cutter's windows and the swings that survive step 1 are two lists,
# and nothing guarantees they stay parallel.
_flip = _ascent_swing_windows(REP, DESC, list(reversed(WIN)), FPS)
check("swing 1 still gets the window its strike is in",
      _flip[0]["ascent_frames"], [1587, 1887])
check("and swing 2 gets its own", _flip[1]["ascent_frames"], [4643, 4943])

print("\n3. no cutter window means the whole clip, and says so")
_all = _ascent_swing_windows(REP, DESC, None, FPS)
check("flagged rather than left blank", _all[0]["ascent_all"], True)
check("with no frames invented", _all[0]["ascent_frames"], None)
t_all = _swing_window_table(_all)
check("the table says it in words",
      "the whole clip (no cutter window)" in t_all["rows"][0][2], True)

print("\n4. the table names the camera on every span")
t = _swing_window_table(rows)
check("a header row", len(t["columns"]), 5)
check("impact is tee", "tee" in t["rows"][0][1], True)
check("the ascent sweep is tee", t["rows"][0][2].endswith("tee"), True)
check("the ball-on-green pair is green",
      t["rows"][0][3].endswith("green"), True)
check("the descent sweep is green", t["rows"][0][4].endswith("green"), True)
check("and the note warns the two clocks are different",
      "different clocks" in (t.get("note") or ""), True)
check("before and after read as two instants, not a span",
      t["rows"][0][3].startswith("before 26.10s → after 34.40s"), True)

print("\n5. a missing window is a dash, never a zero")
_thin = _ascent_swing_windows(
    {"swings": [{"impact_frame": 1647}], "clips": [{"n": 1}]}, {}, [], FPS)
t2 = _swing_window_table(_thin)
check("no ball-on-green test ran", t2["rows"][0][3], "—")
check("no descent window placed", t2["rows"][0][4], "—")
check("but the swing is still listed", t2["rows"][0][0], "Swing 1")

print("\n6. it is wired to step 1, and outside the fold")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("hung on stage 1", 'int(_st.get("n") or 0) == 1' in _run
      and '_st["table"] = _swing_window_table(' in _run, True)
check("built after step 3, when all three are known",
      _run.index("_ascent_swing_windows(") > _run.index(
          '_stage(3, "Balls coming down"'), True)
check("and a failure to build it cannot cost the run",
      "could not build the swing window table" in _run, True)
JSX = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()
check("the page has a table that is not behind the toggle",
      "function PlainTable({ t })" in JSX, True)
check("...rendered beside WorkToggle, not inside it",
      "{r.table && <PlainTable t={r.table} />}" in JSX, True)
check("and the stage's table is passed through",
      "st.table," in JSX, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
