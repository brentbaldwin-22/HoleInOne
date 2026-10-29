#!/usr/bin/env python3
"""The tip must not stand still at the apex.

Measured on upload 1082's produced clip, counting lit tracer pixels
per output frame: the line grows 100px a frame off the tee, then ZERO
for 0.8 seconds, then 35. The dead stretch is the screen apex. A ball
flying away from the lens genuinely lingers there -- its real motion
falls to about a pixel a frame -- and at a 4px line width that reads
as the tracer stopping. True, and it looks broken.

TWO EARLIER ATTEMPTS FAILED AND BOTH ARE WORTH REMEMBERING. Blending
the schedule toward a constant rate did nothing, because the schedule
already reveals one sample per frame -- the tip's speed IS the sample
spacing. Re-pacing without renormalising did nothing either, because
the tip can only land ON samples, and near the apex they are a pixel
apart.

What works is redistributing along ARC LENGTH: same frames, same
total, the share each frame gets raised to a power.

    python3 tools/tracer_reveal_test.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services import ai_tracer as T  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


CHAIN = [tuple(int(v) for v in g.split(","))
         for g in ("335,1368,488;336,1360,472;337,1352,455;338,1345,440;"
                   "339,1338,424;340,1330,410;341,1324,395;342,1317,381;"
                   "343,1311,368;344,1305,355;345,1299,342;346,1293,330;"
                   "347,1288,318;348,1283,307;349,1278,296;350,1273,285;"
                   "351,1269,275;352,1264,265;353,1260,256;354,1257,248;"
                   "355,1253,239;356,1250,231;357,1247,224;358,1244,217;"
                   "359,1242,210;360,1240,204;361,1238,198;362,1236,193;"
                   "363,1234,188;364,1233,184;365,1232,180;366,1231,176;"
                   "367,1231,173;368,1230,171;369,1230,168;370,1230,167;"
                   "371,1231,165;372,1231,164;373,1232,164;374,1233,164"
                   ).split(";")]
TAIL, _k, _w = T._extend_to_target(CHAIN, (1477.0, 736.0), 60.004,
                                   1920, 1080, land_frame=586)
SAMPLES = T._join_tail(list(CHAIN), TAIL)


def speeds(counts):
    seq = [SAMPLES[min(max(c - 1, 0), len(SAMPLES) - 1)] for c in counts]
    return [math.hypot(seq[i][1] - seq[i - 1][1], seq[i][2] - seq[i - 1][2])
            for i in range(1, len(seq))]


def sched(gamma):
    _o = T.TIP_PACE_GAMMA
    T.TIP_PACE_GAMMA = gamma
    try:
        return T._tip_reveal_schedule(SAMPLES, CHAIN)[2]
    finally:
        T.TIP_PACE_GAMMA = _o


RAW, EASED = sched(1.0), sched(T.TIP_PACE_GAMMA)
S_RAW, S_EASED = speeds(RAW), speeds(EASED)
_ap = slice(30, 100)          # the apex window on this flight
print("apex advance: %.2f px/frame raw -> %.2f eased"
      % (sum(S_RAW[_ap]) / 70.0, sum(S_EASED[_ap]) / 70.0))

print()
print("THE APEX MOVES FASTER:")
check("meaningfully, not marginally",
      sum(S_EASED[_ap]) > 1.7 * sum(S_RAW[_ap]), True)

print()
print("and it is zero-sum -- nothing is invented:")
# SAME FRAMES, SAME CURVE, SAME ENDS. Only the share each frame gets
# changes. A reveal that finished early would show the ball landing
# before it landed.
check("same number of frames", len(EASED), len(RAW))
check("ends on the whole curve", EASED[-1], len(SAMPLES))
check("...as it did before", RAW[-1], len(SAMPLES))
check("monotone -- the tip never rewinds",
      all(EASED[i + 1] >= EASED[i] for i in range(len(EASED) - 1)), True)
# NOT THE SUM OF THE STEPS -- that is chords, not arc. Skipping more
# samples at the apex cuts corners, so the summed chords come out
# about 2% shorter. The invariant that matters is the ends.
check("the tip finishes on the last sample",
      EASED[-1] == RAW[-1] == len(SAMPLES), True)
check("...and it cuts no more than a corner getting there",
      0.95 < sum(S_EASED) / sum(S_RAW) <= 1.0, True)

print()
print("the launch is untouched, because it cannot be:")
# One sample per frame already, and the tip cannot land between
# samples -- so the pixels the apex gains come from the stretches
# that have them to spare, not from the strike.
check("still leaves at the ball's own speed",
      round(max(S_EASED[:20])), round(max(S_RAW[:20])))

print()
print("gamma 1.0 is exactly the old behaviour:")
check("byte for byte", sched(1.0), RAW)
check("...and that is the documented default meaning",
      0.0 < T.TIP_PACE_GAMMA < 1.0, True)

print()
print("it cannot break a schedule it does not understand:")
check("too few frames is returned untouched",
      T._ease_reveal([1, 2], SAMPLES), [1, 2])
check("too few samples too", T._ease_reveal([1, 2, 3, 4], SAMPLES[:3]),
      [1, 2, 3, 4])
# A CURVE OF NO LENGTH has no shares to redistribute.
check("a zero-length curve", T._ease_reveal(
    [1, 2, 3, 4], [(i, 5, 5) for i in range(5)]), [1, 2, 3, 4])

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
