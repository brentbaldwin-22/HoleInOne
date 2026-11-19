#!/usr/bin/env python3
"""Two chains of one flight must not become two clips.

THE BUG THIS PREVENTS, from the first end-to-end production run:
"it produces an extra clip because it kept 2 chains of the one swing.
but both produce fine." Each kept ascent becomes a clip, gets a
tracer drawn on it and goes out, so the operator got two videos of
one shot.

NOT THE TEST `stitch_ascent_chains` ALREADY DOES. That joins chains
lying along a common axis within `ascent_stitch_px` of it, which is a
question about SHAPE, and it misses a pair that is genuinely offset --
the leading and trailing edge of one smear, or two segments either
side of a hole the fill could not close. This asks a question about
PHYSICS that no offset changes: did these two leave the same place at
the same moment? A golfer cannot strike two balls half a second apart
from one spot.

The dangerous direction is over-merging: two real swings collapsed
into one loses a shot, and losing one is worse than producing a
surplus. So the separations are tested as hard as the merges.

    python3 tools/one_ball_one_clip_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402
from app.services.debug3 import GATE_DEFAULTS  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


FPS = 60.0


def chain(first_frame, n_points, from_x=700, from_y=720, from_frame=None):
    return {"first_frame": first_frame, "n_points": n_points,
            "from_x": from_x, "from_y": from_y,
            "from_frame": first_frame if from_frame is None else from_frame,
            "points": [{"frame": first_frame + i, "x": from_x + i * 20,
                        "y": from_y - i * 18} for i in range(n_points)]}


def run(chains, gates=None):
    return admin._collapse_one_ball(chains, FPS, gates or {})


print("two pieces of one flight collapse to one:")
# Same tee spot, 3 frames apart at 60fps -- one ball.
kept, merged = run([chain(1454, 7), chain(1457, 4)])
check("one swing survives", len(kept), 1)
check("  the LONGER chain is the one kept",
      kept[0]["n_points"], 7)
check("  and the other is reported, not silently dropped",
      len(merged), 1)
check("  with a reason", "same flight" in (merged[0].get("why") or ""))

print()
print("offset pieces collapse too — the thing stitching misses:")
# 60 px apart: far outside ascent_stitch_px (12), so the shape test
# cannot join them. Same instant, same tee: still one ball.
check("ascent_stitch_px is much smaller than the offset",
      GATE_DEFAULTS["ascent_stitch_px"] < 60)
kept, _ = run([chain(1454, 7, from_x=700), chain(1455, 5, from_x=760)])
check("they still collapse", len(kept), 1)

print()
print("TWO REAL SWINGS MUST SURVIVE — losing one is the worse error:")
# Same spot, but seconds apart: a golfer hitting twice.
kept, merged = run([chain(1454, 7), chain(1454 + int(4 * FPS), 6)])
check("both kept when they are seconds apart", len(kept), 2)
check("  and nothing was merged", len(merged), 0)
# Same instant, but far apart: two golfers on one tee.
kept, _ = run([chain(1454, 7, from_x=400), chain(1454, 6, from_x=1500)])
check("both kept when they leave from different spots", len(kept), 2)

print()
print("and they come back in the order the balls left:")
kept, _ = run([chain(2000, 5), chain(1454, 7), chain(1700, 6)])
check("three swings, three clips", len(kept), 3)
check("  earliest first",
      [c["first_frame"] for c in kept], [1454, 1700, 2000])

print()
print("it declines to guess rather than merging blind:")
check("one ascent is returned untouched", len(run([chain(1454, 7)])[0]), 1)
check("none is none", run([])[0], [])
# No tee spot and no points: nothing to compare, so nothing is merged.
_blind = [{"first_frame": 1454, "n_points": 5, "points": []},
          {"first_frame": 1455, "n_points": 4, "points": []}]
check("with nothing to compare on, both survive",
      len(run(_blind)[0]), 2)

print()
print("the tolerances are tunable and the collapse is reported:")
check("the seconds gate exists",
      "ascent_same_swing_sec" in GATE_DEFAULTS)
check("the distance gate exists",
      "ascent_same_swing_px" in GATE_DEFAULTS)
# A zero-second window can never merge anything.
kept, _ = run([chain(1454, 7), chain(1457, 4)],
              gates={"ascent_same_swing_sec": 0.0})
check("a zero window merges nothing", len(kept), 2)
_adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
# ── THE RUN NO LONGER NEEDS TO COLLAPSE ANYTHING ───────────────────
#
# `_collapse_one_ball` exists because the sweep returned every chain
# it liked and the run then had to notice that two of them were one
# flight. STEP 2 removes the cause rather than the symptom: step 1
# says how many swings there were, and step 2 keeps ONE chain per
# swing. Two pieces of one flight cannot both survive, because only
# one of them is kept in the first place.
#
# The function is still here, still tested above on its own, because
# the question it answers -- did these two leave the same place at
# the same moment -- is the right one to ask if the sweep is ever
# trusted to count swings again.
check("one chain per swing, so there is nothing left to collapse",
      "_best = sorted(_cands, key=_grade)[0]" in _adm)
check("  and the chains that lost are still reported",
      '_sw["considered"] = sorted(' in _adm)
check("  with the ones no swing claimed counted",
      'rep["ascents_unclaimed"]' in _adm)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — one ball, one clip")
