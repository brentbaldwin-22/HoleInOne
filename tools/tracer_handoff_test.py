#!/usr/bin/env python3
"""Five points of jitter were setting the hand-off heading.

"IT'S ASCENDING QUICKLY AND THEN TAKES A SHARP TURN AND SLOWS DOWN."

The slowdown is not at the hand-off -- measured on upload 1082 the
chain's last five frames move 1.0px each and the tail's first five
move 1.2. There is no step. The TURN is, and this is why.

A MOG2 centroid on a fading ball jitters about a pixel. At the top of
the climb the ball's real screen motion falls to about that, so the
last few points of a chain carry no direction at all -- on 1082 the x
reads 1230,1230,1231,1231,1232,1233 and on 1084 it reads
1373,1374,1374,1374,1375,1376. Fitting those in rotates the hand-off
heading by about a quarter turn, from "still climbing" to "already
going right and down", and the continuation leaves along it.

`bezier_continuation` has dropped trailing stalled points since it was
written. The two tail models never did.

    python3 tools/tracer_handoff_test.py
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


def chain(raw):
    return [tuple(int(v) for v in g.split(",")) for g in raw.split(";")]


# The real chains, from the tail-input dumps of two produces.
C1082 = chain(
    "364,1233,184;365,1232,180;366,1231,176;367,1231,173;368,1230,171;"
    "369,1230,168;370,1230,167;371,1231,165;372,1231,164;373,1232,164;"
    "374,1233,164")
C1084 = chain(
    "350,1376,240;351,1375,235;352,1374,231;353,1374,227;354,1373,224;"
    "355,1373,221;356,1374,218;357,1374,217;358,1374,215;359,1375,214;"
    "360,1376,214")


def heading(a, b):
    return math.degrees(math.atan2(-(b[2] - a[2]), b[1] - a[1]))


print("the trim finds the stalled tail on both real chains:")
for nm, c in (("1082", C1082), ("1084", C1084)):
    kept = T._drop_stalled_tail(c)
    check("  %s drops its noise-floor tail" % nm, len(c) - len(kept) > 0, True)
    check("  ...and keeps the rest", len(kept) >= 6, True)

print()
print("AND THAT IS WHAT TURNS THE HAND-OFF ROUND:")
# MEASURED THE WAY THE MODELS MEASURE IT -- a quadratic fit over the
# window, read at the hand-off -- not as a three-point heading, which
# is the jitter this is about.
import numpy as np  # noqa: E402


def fit_heading(pts):
    t = np.array([q[0] - pts[-1][0] for q in pts], float)
    vx = np.polyfit(t, [q[1] for q in pts], 2)[1]
    vy = np.polyfit(t, [q[2] for q in pts], 2)[1]
    return math.degrees(math.atan2(-vy, vx))


for nm, c in (("1082", C1082), ("1084", C1084)):
    _raw = fit_heading(c)
    _fix = fit_heading(T._drop_stalled_tail(c))
    print("  %s: with the stall %+4.0f deg -> without it %+4.0f deg (%+.0f)"
          % (nm, _raw, _fix, _fix - _raw))
    # STILL CLIMBING, not already heading right and down.
    check("  %s: trimmed, the ball is still climbing" % nm,
          45 < _fix < 135, True)
    check("  %s: untrimmed, it is not" % nm, 45 < _raw < 135, False)
    check("  %s: the stall was worth at least a quarter turn" % nm,
          abs(_fix - _raw) > 60.0, True)

print()
print("a chain that never stalls is left alone:")
_moving = [(300 + i, 1000 - 7 * i, 800 - 19 * i) for i in range(20)]
check("nothing dropped", len(T._drop_stalled_tail(_moving)), 20)
# THE MODELS MUST STILL RUN on a chain that is ALL stall -- a trim
# that eats the flight is worse than the jitter it removes.
_stuck = [(300 + i, 1000 + (i % 2), 800) for i in range(20)]
check("a chain of pure stall keeps half of itself",
      len(T._drop_stalled_tail(_stuck)) >= 10, True)
check("never fewer than two", len(T._drop_stalled_tail(_stuck[:3])) >= 2, True)
check("empty is safe", T._drop_stalled_tail([]), [])

print()
print("all three models trim before they measure:")
import inspect  # noqa: E402
for fn in (T._ballistic_tail, T._curved_tail, T._arc_tail):
    _src = inspect.getsource(fn)
    check("  %s" % fn.__name__, "_drop_stalled_tail(pts)" in _src, True)

print()
print("and the drawn chain gives way where the tail starts:")
# The tail now BEGINS inside the chain. Appending it would put two
# points on those frames -- the stalled ones the models just rejected
# and the model's own -- and the renderer would draw out to each.
_drawn = [(10, 0, 0), (11, 1, 1), (12, 2, 2), (13, 3, 3)]
_tail = [(12, 90, 90), (13, 91, 91)]
check("overlapping frames are replaced",
      T._join_tail(_drawn, _tail), [(10, 0, 0), (11, 1, 1),
                                    (12, 90, 90), (13, 91, 91)])
check("no tail, no change", T._join_tail(_drawn, []), _drawn)

print()
print("END TO END on upload 1082's own flight and landing:")
FULL = chain(
    "335,1368,488;336,1360,472;337,1352,455;338,1345,440;339,1338,424;"
    "340,1330,410;341,1324,395;342,1317,381;343,1311,368;344,1305,355;"
    "345,1299,342;346,1293,330;347,1288,318;348,1283,307;349,1278,296;"
    "350,1273,285;351,1269,275;352,1264,265;353,1260,256;354,1257,248;"
    "355,1253,239;356,1250,231;357,1247,224;358,1244,217;359,1242,210;"
    "360,1240,204;361,1238,198;362,1236,193;363,1234,188;364,1233,184;"
    "365,1232,180;366,1231,176;367,1231,173;368,1230,171;369,1230,168;"
    "370,1230,167;371,1231,165;372,1231,164;373,1232,164;374,1233,164")
tail, kind, why = T._extend_to_target(FULL, (1477.0, 736.0), 60.004,
                                      1920, 1080, land_frame=586)
check("a tail is drawn", tail is not None and len(tail) > 0, True)
_base = heading(FULL[-10], FULL[-5])          # the chain's last SIGNAL
_join = heading(tail[0], tail[4]) - _base
print("  join to the chain's own heading: %+.0f deg (was about -110)" % _join)
check("the join is no longer a quarter turn", abs(_join) < 30.0, True)
check("...and it still lands on the mark",
      (tail[-1][1], tail[-1][2]), (1477, 736))
# AND IT CARRIES THE BALL THROUGH THE APEX rather than inheriting a
# false one from the jitter.
check("the apex is above where the chain gave out",
      min(q[2] for q in tail) < FULL[-1][2], True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
