#!/usr/bin/env python3
"""The drawn tip may run ahead of the ball, without changing the clip.

The flight is timed to the OTHER camera's clock, and the tee half is
cut where the green half begins -- so on a long shot the cut arrives
with the arc only part drawn. The line stops in mid-air, which reads as
the tracer being too slow, because next to it the real ball has already
gone.

`TRACER_PACE` uncovers more of the line on each frame. What it must NOT
do is change anything else: the geometry is the same curve, the frame
span is the same span, and the cut lands where it always did. Only how
much of the line is showing on each one changes.

    python3 tools/tracer_pace_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import ai_tracer as T  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SAMPLES = [(1000 + i, 700 + 3 * i, 500 - 4 * i + 0.02 * i * i)
           for i in range(200)]
ANCHORS = [(1000 + i, 700 + 3 * i, 500 - 4 * i + 0.02 * i * i)
           for i in range(0, 60, 6)]


def sched(pace):
    return T._tip_reveal_schedule(SAMPLES, ANCHORS, pace=pace)


print("\n-- the ball's own pace is still available")
_f0, _f1, _c1 = sched(1.0)
check("pace 1.0 gives a schedule", len(_c1) > 0, True)


print("\n-- a faster pace draws more of the line, in the same frames")
_g0, _g1, _c2 = sched(1.35)
check("the first frame is unchanged", _g0, _f0)
check("the last frame is unchanged", _g1, _f1)
check("  and so is the number of frames", len(_c2), len(_c1))
_q = len(_c1) // 4
check("a quarter in, the tip is further along", _c2[_q] > _c1[_q], True)
check("halfway, further still", _c2[len(_c1) // 2] > _c1[len(_c1) // 2],
      True)


print("\n-- and it stays a legal schedule")
# The renderer slices `points[:counts[i]]`, so these must never go
# backwards and never run off the end of the curve.
check("never goes backwards",
      all(a <= b for a, b in zip(_c2, _c2[1:])), True)
check("never exceeds the curve", max(_c2), len(SAMPLES))
check("never shows nothing", min(_c2) >= 1, True)
# A big pace saturates rather than overruns.
_, _, _c3 = sched(4.0)
check("a big pace saturates safely", max(_c3), len(SAMPLES))
check("  and is still monotone",
      all(a <= b for a, b in zip(_c3, _c3[1:])), True)


print("\n-- the geometry is untouched; only the uncovering changes")
# CONTROL: the schedule is a count of samples, so the curve itself
# cannot be altered by it. Same samples in, same samples out.
check("control, the sample list is not modified",
      SAMPLES[0], (1000, 700, 500.0))
check("control, pace is a module constant anyone can dial",
      isinstance(T.TRACER_PACE, float), True)
check("  and the default runs the tip ahead", T.TRACER_PACE > 1.0, True)

src = (ROOT / "backend" / "app" / "services"
       / "ai_tracer.py").read_text(encoding="utf-8")
check("the pace is applied after the easing, not before",
      src.index("sched = _ease_reveal(counts, samples)")
      < src.index("_p = TRACER_PACE if pace is None else float(pace)"),
      True)
check("and the render log says what pace it used",
      "at pace {TRACER_PACE:.2f}" in src, True)

print("\n-- how early the line finishes, which is the cost of pace")
# A LINE THAT FINISHES EARLY THEN SITS STILL is the honest trade here,
# and worth seeing rather than discovering on a clip: past some pace
# the whole curve is uncovered before the camera cut, and the last
# stretch of the tee half shows a complete, motionless line. Measured
# on a 329-point curve: 1.0 finishes at frame 327 of 329, 1.35 at 242,
# 2.0 at 163 -- half way.
_LONG = [(1000 + i, 700 + 3 * i, 500 - 4 * i + 0.012 * i * i)
         for i in range(329)]
_LA = [(1000 + i, 700 + 3 * i, 500 - 4 * i + 0.012 * i * i)
       for i in range(0, 60, 10)]


def _full_at(pace):
    _, _, c = T._tip_reveal_schedule(_LONG, _LA, pace=pace)
    j = next((j for j, v in enumerate(c) if v >= len(_LONG)), None)
    return None if j is None else j / float(len(c))


_f1, _f2 = _full_at(1.0), _full_at(2.0)
print("     pace 1.0 finishes %.0f%% through; pace 2.0 at %.0f%%"
      % (_f1 * 100, _f2 * 100))
check("a faster pace finishes the line earlier", _f2 < _f1, True)
check("  and 2.0 finishes around halfway", _f2 < 0.6, True)
# CONTROL: finishing early must not mean finishing SHORT. The whole
# curve is still drawn -- that is the point of the clamp.
_, _, _c20 = T._tip_reveal_schedule(_LONG, _LA, pace=2.0)
check("control, the whole curve is still reached", max(_c20), len(_LONG))


print("\n-- the pace travels to the produce log")
adm = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("produce says which pace drew the clip",
      "tip drawn at pace" in adm, True)
src2 = (ROOT / "backend" / "app" / "services"
        / "ai_tracer.py").read_text(encoding="utf-8")
check("  carried on both tail records",
      src2.count('"pace": TRACER_PACE,'), 2)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
