#!/usr/bin/env python3
"""The sound is late, and the ball check is what it costs.

THE AUDIO PEAK IS NOT THE IMPACT. It is the impact plus whatever the
microphone, the encoder and the ring buffer add, and three separate
comments in this codebase say so. Nothing measured it or did anything
about it.

It lands hardest on the ball check. Six tiles are cut around the
peak, the first three at -1.20, -0.60 and -0.20, and the ball must be
in the same spot in TWO of them:

    lag 0.00..0.50s    two or three of those are pre-strike
    lag 0.60s and up   only -1.20 is, and the rule CANNOT be met

which is exactly the report from the field -- "the ball is only
visible in the first frame". The check does not miss the first frame.
It finds the ball there and then has nowhere to find it twice.

AND THE BALL IS WHAT MEASURES IT. It sits motionless for as long as
anybody looks and then in one frame is gone, so the last tile holding
it and the first tile without it bracket the real strike.

    python3 tools/audio_lag_test.py
"""
import sys
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
    GATE_SPEC, GATE_DEFAULTS, resolve_gates, split_gate_key,
)
from app.services.ball_vanish import ball_in_frames  # noqa: E402

KEY = "audio_lag_sec"
OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)
BALL = (190, 160)
ANCHOR = (170.0, 120.0)


def shots_for(lag):
    """Six tiles around a peak that is `lag` seconds LATE."""
    out = []
    for t in OFFS:
        f = np.full((240, 320, 3), 60, np.uint8)
        f[::7, ::7] = 72
        if t < -lag - 1e-9:            # the strike is at -lag
            cv2.circle(f, BALL, 3, (245, 245, 245), -1)
        out.append(f)
    return out


print("\n-- it is a per-hole gate, defaulting to today's behaviour")
check("the gate exists", KEY in GATE_SPEC, True)
_d, _lo, _hi, _int = GATE_SPEC[KEY]
check("  default 0, so nothing changes until it is set", _d, 0.0)
check("  it cannot be negative", _lo, 0.0)
check("  and is capped at something sane", _hi, 2.0)
check("a tuned hole gets its own", resolve_gates({KEY: 0.7})[KEY], 0.7)
check("  nonsense is dropped", resolve_gates({KEY: "soon"})[KEY], 0.0)
check("  and it takes zone overrides",
      resolve_gates({KEY: 0.2, KEY + "@3": 0.8}, zone=3)[KEY], 0.8)
check("  the key parses", split_gate_key(KEY + "@3"), (KEY, 3))


print("\n-- THE CLIFF, walked: 0.60s is where the rule stops being met")
for _lag, _want in ((0.00, "BALL_PRESENT_DISAPPEARED"),
                    (0.20, "BALL_PRESENT_DISAPPEARED"),
                    (0.40, "BALL_PRESENT_DISAPPEARED"),
                    (0.50, "BALL_PRESENT_DISAPPEARED"),
                    # ONE HEAD FRAME LEFT. The ball is plainly there
                    # in the first tile and cannot be found twice.
                    (0.60, "NO_BALL_DETECTED"),
                    (1.00, "NO_BALL_DETECTED")):
    _r = ball_in_frames(shots_for(_lag), offsets=OFFS, anchor=ANCHOR)
    check("lag %+.2fs" % _lag, _r["state"], _want)
# AND THE FAILURE IS THE ONE THE OPERATOR DESCRIBED: the ball IS in
# the first tile. Asserted so the diagnosis cannot be mistaken for
# "the detector cannot see it".
_r = ball_in_frames(shots_for(0.80), offsets=OFFS, anchor=ANCHOR)
check("  at 0.80s the first tile still holds the ball",
      (_r["frames"][0]["kept"] or 0) > 0, True)
check("    and the second does not", _r["frames"][1]["kept"], 0)


print("\n-- correcting for it puts the frames back on the strike")
# `ball_at_address` shifts the CENTRE, so the six moments keep their
# spacing and simply aim earlier. Reproduced here by cutting the
# tiles the way it would.
def shots_corrected(lag, correction):
    out = []
    for t in OFFS:
        f = np.full((240, 320, 3), 60, np.uint8)
        f[::7, ::7] = 72
        if (t - correction) < -lag - 1e-9:
            cv2.circle(f, BALL, 3, (245, 245, 245), -1)
        out.append(f)
    return out


for _lag in (0.60, 0.80, 1.00):
    _r = ball_in_frames(shots_corrected(_lag, _lag), offsets=OFFS,
                        anchor=ANCHOR)
    check("lag %+.2fs, corrected -> found again" % _lag,
          _r["state"], "BALL_PRESENT_DISAPPEARED")
check("an over-correction does not quietly become a wrong answer",
      ball_in_frames(shots_corrected(0.2, 1.4), offsets=OFFS,
                     anchor=ANCHOR)["left"] is True, False)


print("\n-- and the ball measures the lag it was corrected for")
for _lag, _want in ((0.40, 0.4), (0.20, 0.4), (0.0, 0.1)):
    _r = ball_in_frames(shots_for(_lag), offsets=OFFS, anchor=ANCHOR)
    check("lag %+.2fs reads back as %s" % (_lag, _want),
          _r.get("lag_implied_sec"), _want)
# THE RESOLUTION IS THE TILE SPACING, and saying so matters: 0.20 and
# 0.40 both read 0.4 because the tiles cannot tell them apart. It is
# a reading off one cut, which is why the gate is set from several.
_r = ball_in_frames(shots_for(0.40), offsets=OFFS, anchor=ANCHOR)
check("  and it says which tiles held the ball",
      _r.get("seen_at"), [-1.2, -0.6])
check("a check that found no ball offers no reading",
      ball_in_frames(shots_for(1.0), offsets=OFFS,
                     anchor=ANCHOR).get("lag_implied_sec"), None)


print("\n-- the wiring")
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the test cut resolves the gate", '"audio_lag_sec"' in ADM, True)
check("  and passes it to the ball check",
      "lag_sec=float(_blag)" in ADM, True)
# THE ZOOMED STRIP MUST AGREE WITH THE SEARCH. A box strip cut around
# the uncorrected peak would show different frames from the ones the
# verdict came from.
check("  and cuts the zoomed strip around the same instant",
      "peak_time_sec - float(_blag)" in ADM, True)
check("  the reading reaches the page",
      'cut_entry["ball_lag_implied"]' in ADM, True)
JSX = (ROOT / "frontend" / "src" / "components"
       / "CutVerdicts.jsx").read_text(encoding="utf-8")
check("the panel shows it", "ball_lag_implied" in JSX, True)
GT = (ROOT / "frontend" / "src" / "components"
      / "GateTuning.jsx").read_text(encoding="utf-8")
check("and the editor explains the one gate that needs it",
      "audio_lag_sec" in GT, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
