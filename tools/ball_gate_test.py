#!/usr/bin/env python3
"""How bright a ball must be is this hole's setting, not a constant.

On one hole the ball scored 28 grey levels over its own turf against
`ADDRESS_MIN_CONTRAST` of 30, and the check reported "no ball
identified" on a clip where the operator could see it. Lowering the
constant would have moved every hole to suit one hole's light, and
what the bar keeps out is bright patches of mown grass -- a false
"the ball was hit" on a clip where nobody swung.

So it joins the per-hole gates, with zone overrides, like the audio
ones and for the same reason: it is a property of distance, light and
turf, which is what a hole is.

THE KNOB IS CHECKED BY TURNING IT. A gate that is stored, validated
and rendered but changes no answer is the failure this file is
actually guarding against -- the audio gates were added to the spec
and shown by nobody, and nothing said so.

    python3 tools/ball_gate_test.py
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
from app.services.ball_vanish import (  # noqa: E402
    ball_in_frames, ADDRESS_MIN_CONTRAST, MIN_LOCAL_CONTRAST,
)

KEY = "ball_min_contrast"

print("\n-- it is a gate, with the old constant as its default")
check("the gate exists", KEY in GATE_SPEC, True)
_d, _lo, _hi, _int = GATE_SPEC[KEY]
check("  the default is the constant it replaced",
      _d, ADDRESS_MIN_CONTRAST)
# THE FLOOR IS THE DETECTOR'S OWN. `_detections` applies
# MIN_LOCAL_CONTRAST before this bar is consulted, so a setting under
# it buys nothing and would read as a knob that does not work.
check("  it cannot be set below the detector's floor",
      _lo, MIN_LOCAL_CONTRAST)
check("  and it is a real number, not an int", _int, False)
check("a hole that tunes nothing gets the old behaviour",
      resolve_gates({})[KEY], ADDRESS_MIN_CONTRAST)
check("a tuned hole gets its own", resolve_gates({KEY: 24})[KEY], 24.0)
check("  nonsense is dropped, not stored",
      resolve_gates({KEY: "1.o"})[KEY], ADDRESS_MIN_CONTRAST)
check("  and a value under the floor is clamped to it",
      resolve_gates({KEY: 2})[KEY], float(MIN_LOCAL_CONTRAST))
# PER ZONE, because a back tee and a forward one are different light.
check("a zone can differ from the hole",
      resolve_gates({KEY: 30, KEY + "@2": 22}, zone=2)[KEY], 22.0)
check("  and another zone keeps the hole's",
      resolve_gates({KEY: 30, KEY + "@2": 22}, zone=1)[KEY], 30.0)
check("  the key parses", split_gate_key(KEY + "@2"), (KEY, 2))


print("\n-- AND TURNING IT CHANGES THE ANSWER")
# The operator's exact case: a ball a couple of grey levels under the
# default bar. Found at 24, not found at 30, same frames.
W, H = 320, 240
ANCHOR = (170.0, 120.0)
OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)
BALL = (190, 160)


def frames(present):
    out = []
    for p in present:
        f = np.full((H, W, 3), 60, np.uint8)
        f[::7, ::7] = 72
        if p:
            cv2.circle(f, BALL, 3, (60 + 28, 60 + 28, 60 + 28), -1)
        out.append(f)
    return out


_shots = frames([1, 1, 1, 0, 0, 0])
_hi_bar = ball_in_frames(_shots, offsets=OFFS, anchor=ANCHOR,
                         min_contrast=30.0)
_lo_bar = ball_in_frames(_shots, offsets=OFFS, anchor=ANCHOR,
                         min_contrast=24.0)
check("at the default bar the ball is missed",
      _hi_bar["state"], "NO_BALL_DETECTED")
check("  and the near miss names the number it wanted",
      (_hi_bar.get("near") or {}).get("best_in_band") is not None, True)
check("at the hole's tuned bar the SAME frames find it",
      _lo_bar["state"], "BALL_PRESENT_DISAPPEARED")
check("  at the spot the ball was drawn",
      _lo_bar["spot"] and abs(_lo_bar["spot"][0] - BALL[0]) <= 3, True)
# AND THE SENTENCE REPORTS THE BAR IT USED, so the operator can see
# which setting was in force without reading the database.
check("the reported bar follows the setting",
      _hi_bar["limits"]["min contrast"], 30.0)
check("  both ways", _lo_bar["limits"]["min contrast"], 24.0)


print("\n-- and the run that judges cuts asks for the hole's value")
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the test-cut ball check resolves the gate",
      '"ball_min_contrast"' in ADM, True)
# WITH THIS CUT'S OWN ZONE -- and this line used to assert the bug.
# It checked for `zone=cut_entry.get("gate")`, which is not a zone:
# it is a sentence from the pre-judge, and it is not even set until
# two hundred lines after the gates are resolved. So the override an
# operator typed for one tee had never once applied, and the test
# said it had.
check("  with this cut's own zone, from the trigger",
      "_bzone = _trigger_zone(db, row)" in ADM, True)
check("    which is the source the audio cutter uses too",
      "_az = _trigger_zone(db, row)" in ADM, True)
check("    and not the pre-judge's reason string",
      'zone=cut_entry.get("gate")' in ADM, False)
check("  and passes it in", "min_contrast=float(_bmc)" in ADM, True)
# A NameError HERE WOULD HAVE KILLED THE WHOLE BALL CHECK SILENTLY:
# the call sits inside a swallow. `_d3` is imported per-function in
# that module, not at its top.
check("  naming a module it actually imported",
      "from ..services import debug3 as _bd3" in ADM, True)


print("\n-- and the operator can find it")
JSX = (ROOT / "frontend" / "src" / "components"
       / "GateTuning.jsx").read_text(encoding="utf-8")
# A GATE NOBODY CAN FIND IS A GATE NOBODY TUNES. The editor groups by
# PREFIX and anything ungrouped was invisible until "Other" existed.
check("the editor groups the ball gates", 'startsWith("ball_")' in JSX,
      True)
check("  under their own heading", "Ball — sitting on the tee" in JSX,
      True)
check("  and strips the prefix from the label",
      "ascent|descent|audio|ball" in JSX, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
