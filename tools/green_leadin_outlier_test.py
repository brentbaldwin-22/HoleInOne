#!/usr/bin/env python3
"""One bad plotted point must not cost the whole lead-in.

WHY THIS EXISTS. `lead_in` judges its parabola with a single RMS gate:
11.9px extrapolates, 12.1px refuses outright and the green view opens
on an empty picture until the first plotted point arrives. The points
come from MOG2 on a near-vertical fall, where one frame landing on the
flagstick or a sprinkler head is ordinary -- and in sixteen points a
lone 40px outlier adds about 10px to the RMS by itself.

Measured on upload 909: the operator's descent was refused and the
produced clip opened the green view with ~0.5s of nothing before the
tracer appeared, which is the hole this function exists to fill.

THE SHAPE OF THE TEST. A clean flight, then the same flight with one
point knocked sideways, then with two. One must survive; two must
still refuse, because a set needing two thrown away is not one flight
and refusing is the right answer for it.

    python3 tools/green_leadin_outlier_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services import green_flight as gf  # noqa: E402

FPS = 60.0
W, H = 1920, 1080
F0, FL, CUT = 2406, 2421, 2376          # upload 909's real geometry
FAILED = []


def flight(bumps=()):
    """The real descent from 909, with `bumps` = [(index, dx, dy)]."""
    pts = []
    for i in range(16):
        u = i / 15.0
        pts.append({"frame": F0 + i,
                    "x": 1541 + (1578 - 1541) * u,
                    "y": 396 + (830 - 396) * (0.80 * u + 0.20 * u * u)})
    for i, dx, dy in bumps:
        pts[i]["x"] += dx
        pts[i]["y"] += dy
    return pts


def run(label, pts, want_lead, note=""):
    dbg = {}
    out = gf.lead_in(pts, CUT, FPS, W, H, landing_frame=FL, debug=dbg)
    d = dbg.get("lead_in", {})
    got = len(out) > 0
    ok = got == want_lead
    print("%-46s rms %5.1fpx  lead-in %2d  dropped=%s  %s"
          % (label, d.get("rms_px", -1), d.get("n", 0),
             d.get("dropped_frame"), "ok" if ok else "FAIL"))
    if note:
        print("    %s" % note)
    if not ok:
        FAILED.append(label)
    return out, d


print("upload 909 geometry: f%d..f%d, cut f%d (%.2fs hole to fill)"
      % (F0, FL, CUT, (F0 - CUT) / FPS))
print()

out, d = run("clean flight", flight(), True)
assert out and out[0]["frame"] == CUT, "a clean flight should reach the cut"
print("    reaches the cut at f%d — nothing empty" % out[0]["frame"])

# One point knocked far enough sideways to break the old gate.
one = flight(bumps=[(7, 46.0, -46.0)])
out, d = run("one 65px outlier (was refused outright)", one, True)
if out:
    print("    dropped f%s, still fills %d frame(s) back to f%d"
          % (d.get("dropped_frame"), len(out), out[0]["frame"]))

two = flight(bumps=[(5, 46.0, -46.0), (11, -46.0, 46.0)])
run("two outliers — must still refuse", two, False,
    "a set needing two thrown away is not one flight")

# The drop must not rescue genuine noise.
import random
r = random.Random(3)
noisy = flight()
for p in noisy:
    p["x"] += r.uniform(-40, 40)
    p["y"] += r.uniform(-40, 40)
run("40px noise on every point — must refuse", noisy, False,
    "dropping one point cannot make scatter into a flight")

# And a clean flight with too few points is still refused.
run("3 points only", flight()[:3], False,
    "below LEADIN_MIN_POINTS=%d" % gf.LEADIN_MIN_POINTS)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
