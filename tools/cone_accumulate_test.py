#!/usr/bin/env python3
"""Finding the climb by integrating, where thresholding cannot.

THE SITUATION THIS IS FOR. Below the skyline every leaf is a mover, so
being a mover does not distinguish the ball and no per-frame threshold
separates them. The operator put it exactly: "just too much noise
below the skyline... I can see the ball with my eyes if i go frame by
frame". The ball is in the pixels; the per-frame DECISION is what is
impossible.

So the fixture is built to be unwinnable frame by frame: a swaying
treeline whose leaves are the same size and brighter than the ball,
hundreds of them per frame, and one faint ball crossing. Any
single-frame ranking buries the ball. The accumulator is asked to find
it anyway, from the straight line it travels and nothing else.

The control matters as much as the test: run the SAME noise with no
ball, and the winner must not stand out. A detector that always
returns a confident answer has told you nothing.

    python3 tools/cone_accumulate_test.py
"""
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


W, H = 960, 640
APEX = (300, 560)
TRUE_DEG, TRUE_V = 58.0, 11.0
N = 34


def clip(path, with_ball=True, seed=5):
    """A busy treeline plus (optionally) one faint ball."""
    rng = np.random.default_rng(seed)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    # Static "houses": bright, large, unmoving. These must not win.
    houses = [(int(rng.integers(320, 900)), int(rng.integers(300, 470)))
              for _ in range(7)]
    leaves = [(int(rng.integers(300, 950)), int(rng.integers(250, 555)))
              for _ in range(240)]
    for i in range(N + 6):
        fr = np.full((H, W, 3), 38, dtype=np.uint8)
        for (hx, hy) in houses:
            cv2.rectangle(fr, (hx, hy), (hx + 46, hy + 34),
                          (235, 235, 235), -1)
        # leaves oscillate IN PLACE, a pixel or two, every frame
        for (lx, ly) in leaves:
            jx = int(round(2.0 * math.sin(i * 0.9 + lx)))
            jy = int(round(2.0 * math.cos(i * 1.1 + ly)))
            cv2.circle(fr, (lx + jx, ly + jy), 3, (205, 205, 205), -1)
        if with_ball and 1 <= i <= N:
            r = math.radians(TRUE_DEG)
            x = int(round(APEX[0] + math.sin(r) * TRUE_V * i))
            y = int(round(APEX[1] - math.cos(r) * TRUE_V * i))
            if 0 <= x < W and 0 <= y < H:
                # FAINTER AND SMALLER THAN THE LEAVES, deliberately.
                cv2.circle(fr, (x, y), 2, (150, 150, 150), -1)
        vw.write(fr)
    vw.release()


def run(with_ball=True, seed=5, lo=35.0, hi=77.0, **kw):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.mp4"
        clip(p, with_ball=with_ball, seed=seed)
        return d3.cone_accumulate(
            p, apex=APEX, f0=0, n_frames=N,
            cone_lo=lo, cone_hi=hi, **kw)


print("the frame-by-frame detector is swamped, as the operator says:")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "c.mp4"
    clip(p)
    dets = d3.detect_movers_by_diff(p, 0, N, sens=3, per_frame=40,
                                    roi=(250, 240, 700, 340), cap=200000)
    near = [d for d in dets
            if abs(d["x"] - (APEX[0] + math.sin(math.radians(TRUE_DEG))
                             * TRUE_V * d["frame"])) < 6
            and abs(d["y"] - (APEX[1] - math.cos(math.radians(TRUE_DEG))
                              * TRUE_V * d["frame"])) < 6]
    print("     %d detections, %d of them on the ball"
          % (len(dets), len(near)))
    check("the noise really does swamp it (<25%% are the ball)",
          len(near) < max(1, len(dets)) * 0.25)

print()
print("the accumulator finds the ball anyway:")
got = run(True)
check("it ran", got.get("ok"), True)
best = got.get("best") or {}
check("  the bearing is right to within 3 degrees",
      abs(float(best.get("bearing_deg", 0)) - TRUE_DEG) <= 3.0)
check("  the speed is right to within 15%%",
      abs(float(best.get("speed_px_per_frame", 0)) - TRUE_V)
      <= 0.15 * TRUE_V)
check("  it used the whole window", (best.get("n_samples") or 0) >= 20)
check("  and it is well clear of the field (z > 6)",
      (got.get("margin_z") or 0) > 6.0)

print()
print("a static white house must never win — only movers count:")
# Every house sits inside the cone and is far brighter than the ball.
_hit = best.get("points") or []
check("the winner travels (it is not parked on a house)",
      len(_hit) > 2
      and (abs(_hit[-1]["x"] - _hit[0]["x"])
           + abs(_hit[-1]["y"] - _hit[0]["y"])) > 100)

print()
print("THE CONTROL — same noise, no ball, no confident answer:")
none = run(False)
check("it still ran", none.get("ok"), True)
check("  but nothing stands out (z < 6)",
      (none.get("margin_z") or 0) < 6.0)
print("     with a ball z=%s, without z=%s"
      % (got.get("margin_z"), none.get("margin_z")))
check("  and the two are not close",
      (got.get("margin_z") or 0) > 2.0 * (none.get("margin_z") or 0.01))

print()
print("and it declines cleanly rather than guessing:")
bad = run(True, lo=10.0, hi=10.0)
check("a cone with no width -> declines", bad.get("ok"), False)
check("  and says so", "no width" in (bad.get("why") or ""))
short = d3.cone_accumulate(Path("/nonexistent.mp4"), apex=APEX, f0=0,
                           n_frames=N, cone_lo=35.0, cone_hi=77.0)
check("an unopenable clip -> declines", short.get("ok"), False)
check("  and says so", "would not open" in (short.get("why") or ""))

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — integration finds what thresholding cannot")

# ── THE WIRING, not just the maths ───────────────────────────────────
#
# An algorithm that works and is not reachable is worth nothing, which
# this session has now demonstrated three separate ways: a picture
# nothing rendered, a picture behind a closed fold, and a gate the
# search resolved blind to its zone.
# ── A LOITERER MUST NOT WIN ─────────────────────────────────────────
#
# THE FAILURE THIS PINS, from the field. Scoring by lit count rewards
# a path for being lit on many samples, and the paths lit on the most
# samples are the SLOW ones that never leave the treeline -- a clip
# came back with a winner at 3.6 px/frame on a 50 degree bearing,
# which is 2.3 px of climb per frame and needs a hundred frames to
# reach a skyline forty-four frames away. A struck ball clears the
# trees; that is physical, and free.
print()
print("a slow path through dense clutter must not beat the ball:")


def clip_loiter(path, n=40):
    """Thick clutter just above the apex, and a real ball leaving."""
    rng = np.random.default_rng(3)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for i in range(n + 4):
        fr = np.full((H, W, 3), 38, dtype=np.uint8)
        # a churning thicket right over the ball, where a slow path
        # would sit and be lit on every single frame
        for _ in range(300):
            x = int(rng.integers(APEX[0] - 10, APEX[0] + 120))
            y = int(rng.integers(APEX[1] - 130, APEX[1]))
            cv2.circle(fr, (x, y), 3, (215, 215, 215), -1)
        if 1 <= i <= n:
            r = math.radians(TRUE_DEG)
            x = int(round(APEX[0] + math.sin(r) * TRUE_V * i))
            y = int(round(APEX[1] - math.cos(r) * TRUE_V * i))
            if 0 <= x < W and 0 <= y < H:
                cv2.circle(fr, (x, y), 2, (150, 150, 150), -1)
        vw.write(fr)
    vw.release()


with tempfile.TemporaryDirectory() as td:
    p2 = Path(td) / "loiter.mp4"
    clip_loiter(p2)
    # the band floor sits above the thicket: the ball clears it, a
    # path that stays in the thicket cannot.
    _SKY = APEX[1] - 160
    loose = d3.cone_accumulate(p2, apex=APEX, f0=0, n_frames=40,
                               cone_lo=35.0, cone_hi=77.0)
    tight = d3.cone_accumulate(p2, apex=APEX, f0=0, n_frames=40,
                               cone_lo=35.0, cone_hi=77.0, clear_y=_SKY)
    print("     without the floor: %.1f px/frame · with it: %.1f"
          % ((loose.get("best") or {}).get("speed_px_per_frame", 0),
             (tight.get("best") or {}).get("speed_px_per_frame", 0)))
    check("with the floor it still runs", tight.get("ok"), True)
    check("  it reports the bar it applied",
          (tight.get("min_climb_px_per_frame") or 0) > 0)
    check("  it excluded the slow candidates",
          (tight.get("n_too_slow") or 0) > 0)
    check("  and the winner actually climbs out",
          (tight.get("best") or {}).get("speed_px_per_frame", 0)
          * math.cos(math.radians((tight.get("best") or {})
                                  .get("bearing_deg", 0)))
          >= tight.get("min_climb_px_per_frame", 0))
    # NOT "the unfloored run picks something slower" any more, and
    # that is the statistic improving rather than the test weakening.
    # Scoring by the RUN OUT OF THE BALL already disfavours a path
    # that loiters: a loiterer is lit scattered along its length, not
    # consecutively from the tee. The floor is now a second line of
    # defence, so what has to hold is that it never ADMITS a ball
    # that cannot leave -- which is what the check above measures.
    check("  both runs now find a ball that leaves the tee",
          min((loose.get("best") or {}).get("speed_px_per_frame", 0),
              (tight.get("best") or {}).get("speed_px_per_frame", 0))
          >= 8.0)

# ── A BALL THAT VANISHES AFTER TEN FRAMES ───────────────────────────
#
# THE FAILURE THIS PINS, measured against the wizard's own plot of the
# swing: the real ascent ran 48.6 degrees at 28 px/frame, and the
# accumulator answered 36.9 at 11.7. The wizard only plots eight
# frames of it -- against open sky the ball gets smaller and fainter
# every frame and then it is simply not there.
#
# So a true track and a noise track finish with about the SAME number
# of lit frames, and COUNTING them cannot separate the two. What
# separates them is where those frames are: the ball lights 1,2,3...
# consecutively out of the tee, clutter lights a scattered few
# anywhere along the line.
print()
print("a ball visible for only its first frames is still found:")
TRUE2_DEG, TRUE2_V, VISIBLE = 48.6, 28.0, 10


def clip_fading(path, n=44):
    rng = np.random.default_rng(17)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for i in range(n + 2):
        fr = np.full((H, W, 3), 38, dtype=np.uint8)
        # clutter EVERYWHERE in the cone, all window long, so a slow
        # scattered path can rack up lit frames for the whole window
        for _ in range(260):
            x = int(rng.integers(APEX[0] - 20, W))
            y = int(rng.integers(120, APEX[1]))
            cv2.circle(fr, (x, y), 3, (210, 210, 210), -1)
        # the ball: bright at first, gone after VISIBLE frames
        if 1 <= i <= VISIBLE:
            r = math.radians(TRUE2_DEG)
            x = int(round(APEX[0] + math.sin(r) * TRUE2_V * i))
            y = int(round(APEX[1] - math.cos(r) * TRUE2_V * i))
            if 0 <= x < W and 0 <= y < H:
                cv2.circle(fr, (x, y), 2, (150, 150, 150), -1)
        vw.write(fr)
    vw.release()


with tempfile.TemporaryDirectory() as td:
    p3 = Path(td) / "fade.mp4"
    clip_fading(p3)
    fade = d3.cone_accumulate(p3, apex=APEX, f0=0, n_frames=44,
                              cone_lo=35.0, cone_hi=77.0,
                              clear_y=APEX[1] - 200)
    b = fade.get("best") or {}
    print("     best: %.1f deg at %.1f px/frame, lead %s, lit %s/%s"
          % (b.get("bearing_deg", 0), b.get("speed_px_per_frame", 0),
             b.get("lead_frames"), b.get("n_lit"), b.get("n_samples")))
    check("it ran", fade.get("ok"), True)
    check("  the bearing is right to within 4 degrees",
          abs(float(b.get("bearing_deg", 0)) - TRUE2_DEG) <= 4.0)
    check("  the speed is right to within 20%",
          abs(float(b.get("speed_px_per_frame", 0)) - TRUE2_V)
          <= 0.20 * TRUE2_V)
    # THE POINT OF THE STATISTIC: a run out of the ball, not a tally.
    check("  it found a run out of the ball",
          (b.get("lead_frames") or 0) >= VISIBLE - 2)
    check("  which is most of the frames the ball was visible for",
          (b.get("lead_frames") or 0) <= VISIBLE + 2)

print()
print("it is reachable from the find, and gated:")
from app.services.debug3 import GATE_DEFAULTS  # noqa: E402

check("the gate exists", "ascent_cone_accumulate" in GATE_DEFAULTS)
check("  and is off by default",
      GATE_DEFAULTS["ascent_cone_accumulate"], 0)
check("the confidence bar is tunable",
      "ascent_cone_accum_min_z" in GATE_DEFAULTS)

_adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
check("the find calls it", "_ascent_cone_accumulate(" in _adm)
check("  with the zone, so a per-tee gate is seen",
      "_zone_for_cone, fps)" in _adm)
check("  and hands the result to the picture",
      "accum=rep.get(\"cone_accum\")" in _adm)
check("the picture draws the runners-up too",
      'candidates") or [])[1:]' in _adm)

_prod = (ROOT / "frontend" / "src" / "pages"
         / "AdminProduction.jsx").read_text()
check("the page shows the answer", "cone_accum" in _prod)
check("  and shows it even when below the bar",
      "BELOW the bar" in _prod)
check("  and says why it did not run",
      "Accumulator did not run" in _prod)

if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — and reachable")
