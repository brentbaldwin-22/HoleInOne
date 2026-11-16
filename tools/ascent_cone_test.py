#!/usr/bin/env python3
"""Where a ball from THIS tee can go, and the frame it left.

TWO THINGS A TEE KNOWS THAT THE DETECTOR DID NOT.

The first is the launch direction. `ascent_max_tilt_deg` measures
atan2(|vx|, |vy|) -- absolute values -- so it asks "within N degrees
of vertical, either side" and cannot know that from this tee the ball
goes up and to the RIGHT. A cart crossing left to right at the same
lean is indistinguishable to it.

The second is the instant. The six judged tiles are 0.20s apart, so
the best they can say is "between these two"; the BALL can do better,
because it sits still and then in one frame is gone.

THE CONE IS PER ZONE AND THAT COSTS NOTHING: the gate resolver
already honours "name@N", so the back tee and the forward tee on one
hole aim differently with no new storage. That is why it is a gate
rather than a column on the zone.

    python3 tools/ascent_cone_test.py
"""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


import numpy as np  # noqa: E402
import cv2  # noqa: E402

from app.services.debug3 import (  # noqa: E402
    GATE_SPEC, GATE_DEFAULTS, resolve_gates, split_gate_key, find_ascents,
)

LO, HI = "ascent_cone_from_deg", "ascent_cone_to_deg"
WIN = "ascent_window_frames"

print("\n-- the cone is a gate, and so is the window")
for _k in (LO, HI, WIN):
    check("%s exists" % _k, _k in GATE_SPEC, True)
# A HALF TURN WIDE BY DEFAULT: it refuses nothing that is going up at
# all, so a hole that sets neither behaves exactly as it does today.
check("  the default cone refuses nothing",
      (GATE_DEFAULTS[LO], GATE_DEFAULTS[HI]), (-90.0, 90.0))
check("  and it takes zone overrides",
      resolve_gates({LO: -90, LO + "@2": 5}, zone=2)[LO], 5.0)
check("  the key parses", split_gate_key(LO + "@2"), (LO, 2))
check("the window is an int gate", GATE_SPEC[WIN][3], True)


print("\n-- the angle convention, which the operator has to type in")
# 0 IS STRAIGHT UP THE PICTURE, positive leans right. Asserted rather
# than described, because a sign error here points the cone at the
# wrong half of the hole and refuses every real ball.
def _bearing(vx, vy):
    return round(math.degrees(math.atan2(vx, -vy)), 1)


for _lbl, _v, _want in (("straight up", (0, -10), 0.0),
                        ("up and right", (7, -7), 45.0),
                        ("up and left", (-7, -7), -45.0),
                        ("flat to the right", (10, 0), 90.0),
                        ("flat to the left", (-10, 0), -90.0)):
    check("  %-18s -> %+.0f deg" % (_lbl, _want), _bearing(*_v), _want)


def have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


if not have_ffmpeg():
    print("\nffmpeg is not installed here — the search itself is unchecked")
    print()
    if FAILED:
        print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all good")
    sys.exit(0)

print("\n-- and a real search, with a ball going one way and a cart the other")
TMP = Path(tempfile.mkdtemp(prefix="cone-"))
W, H, FPS = 640, 360, 60
IMPACT, TEE = 30, (300, 300)


def clip(dx, dy, name):
    """A bright dot leaving the tee on a fixed heading."""
    d = TMP / name
    d.mkdir()
    for i in range(110):
        f = np.full((H, W, 3), 40, np.uint8)
        f[:] = (60, 80, 60)
        rng = np.random.default_rng(i)
        f += rng.integers(0, 6, (H, W, 3), dtype=np.int16).astype(np.uint8)
        if i >= IMPACT:
            _k = i - IMPACT
            _x = int(TEE[0] + dx * _k)
            _y = int(TEE[1] + dy * _k)
            if 0 <= _x < W and 0 <= _y < H:
                cv2.circle(f, (_x, _y), 4, (250, 250, 250), -1)
        cv2.imwrite(str(d / ("f%04d.png" % i)), f)
    out = TMP / (name + ".mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate",
                    str(FPS), "-i", str(d / "f%04d.png"), "-c:v",
                    "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                    str(out)], check=True, timeout=300)
    return out


# A ball up and to the RIGHT, and one up and to the LEFT. Same speed,
# same rise, mirror headings -- so only the cone can tell them apart.
RIGHT = clip(5, -7, "right")
LEFT = clip(-5, -7, "left")
_want = round(math.degrees(math.atan2(5, 7)), 0)
print("      the two flights leave at %+.0f and %+.0f degrees"
      % (_want, -_want))

_open = {LO: -90, HI: 90}
_rightward = {LO: 5, HI: 55}
for _lbl, _vid, _gates, _ok in (
        ("rightward ball, no cone", RIGHT, _open, True),
        ("rightward ball, rightward cone", RIGHT, _rightward, True),
        # THE WHOLE POINT. Identical in every other measurable way.
        ("LEFTWARD ball, rightward cone", LEFT, _rightward, False),
        ("leftward ball, no cone", LEFT, _open, True)):
    _r = find_ascents(_vid, float(FPS), IMPACT, TEE, gates=_gates)
    check("  %-34s -> %s" % (_lbl, "found" if _ok else "refused"),
          bool(_r["ok"]), _ok)
    if not _ok:
        # AND IT SAYS WHY, in the operator's own units.
        _cons = _r.get("considered") or []
        _said = [c.get("why") or "" for c in _cons]
        check("    naming the cone and the bearing",
              any("cone" in w and "off vertical" in w for w in _said), True)

print("\n-- the window is honoured when the hole sets one")
_r = find_ascents(RIGHT, float(FPS), IMPACT, TEE,
                  gates=dict(_open, **{WIN: 8}))
_far = find_ascents(RIGHT, float(FPS), IMPACT, TEE, gates=_open)
check("a short window sees fewer points than a long one",
      (_r.get("n_points") or 0) <= (_far.get("n_points") or 0), True)

print("\n-- the impact frame comes from the ball when the ball went")
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the test cut asks ball_departure", "ball_departure(" in ADM, True)
check("  only when the ball was seen to go",
      '_bl.get("left") is True and _bl.get("spot")' in ADM, True)
check("  and records which answer it used",
      'cut_entry["impact_from"]' in ADM, True)
check("  defaulting to the 0.00 frame", '"the 0.00 frame"' in ADM, True)

print("\n-- and the operator can set the cone per tee")
JSX = (ROOT / "frontend" / "src" / "components"
       / "GateTuning.jsx").read_text(encoding="utf-8")
check("the cone is in the per-zone table", "const perZone" in JSX, True)
check("  and out of the single-value ascent column",
      "!CONE.includes(k)" in JSX, True)
check("  with the sign convention spelled out",
      "degrees off straight up" in JSX, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
