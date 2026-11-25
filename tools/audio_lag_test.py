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


print("\n-- END TO END: the lag really moves which frames are read")
# THE PROPERTY THAT WAS BROKEN AND NOTHING TESTED. Every assertion
# above worked on tiles handed in directly, so all of it passed while
# the lag reached nothing on a real clip. The operator's report was
# "I changed the lag and the same picture comes back".
import subprocess  # noqa: E402
import tempfile  # noqa: E402

from app.services.ball_vanish import ball_at_address  # noqa: E402


def _have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


if _have_ffmpeg():
    _tmp = Path(tempfile.mkdtemp(prefix="lag-"))
    _fr = _tmp / "f"
    _fr.mkdir()
    _FPS, _IMPACT = 60, 2.00
    # The ball leaves at 2.00s; the peak we are handed is 0.60s LATE.
    _PEAK = _IMPACT + 0.60
    for _i in range(220):
        _f = np.full((240, 320, 3), 60, np.uint8)
        _f[::7, ::7] = 72
        if _i / _FPS < _IMPACT:
            cv2.circle(_f, BALL, 3, (245, 245, 245), -1)
        cv2.imwrite(str(_fr / ("f%04d.png" % _i)), _f)
    _vid = _tmp / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate",
                    str(_FPS), "-i", str(_fr / "f%04d.png"), "-c:v",
                    "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(_vid)], check=True, timeout=300)

    _no = ball_at_address(_vid, _PEAK, OFFS, anchor=ANCHOR, lag_sec=0.0)
    _yes = ball_at_address(_vid, _PEAK, OFFS, anchor=ANCHOR, lag_sec=0.6)
    check("with no correction the ball cannot be found twice",
          _no["state"], "NO_BALL_DETECTED")
    check("  and it IS in the first tile, which is the whole point",
          (_no["frames"][0]["kept"] or 0) > 0, True)
    check("with the hole's lag applied it is found",
          _yes["state"], "BALL_PRESENT_DISAPPEARED")
    check("  in two of the first three", _yes["seen_before"] >= 2, True)
else:
    print("  ffmpeg is not installed here — end to end unchecked")


print("\n-- the wiring")
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the test cut resolves the gate", '"audio_lag_sec"' in ADM, True)
check("  and passes it to the ball check",
      "lag_sec=float(_blag)" in ADM, True)
# THE ZOOMED STRIP MUST AGREE WITH THE SEARCH. A box strip cut around
# the uncorrected peak would show different frames from the ones the
# verdict came from.
# ONE INSTANT, USED EVERYWHERE. The six-frame strip the judge is
# shown, the zoomed box strip and the ball check must all be cut
# around the same moment, or the picture argues with the verdict --
# and the strip is the picture the operator actually looks at, so a
# lag that moved everything EXCEPT it read as doing nothing at all.
check("  there is one corrected instant", "_strike_sec = peak_time_sec"
      in ADM, True)
check("  the judge's six-frame strip is cut at it",
      "frames_strip(src_path, _strike_sec" in ADM, True)
check("  and so is the zoomed box strip",
      "src_path, _strike_sec, _bsp" in ADM, True)
check("  with nothing still cutting at the raw peak",
      "frames_strip(src_path, peak_time_sec" in ADM, False)
# THE ZONE HAS TO BE A ZONE. This read `cut_entry.get("gate")`, which
# is a sentence from the pre-judge and is not set until two hundred
# lines later -- so every zone override typed by an operator was
# silently ignored, twice over.
check("  the zone comes from the trigger, as the cutter's does",
      "_bzone = _trigger_zone(db, row)" in ADM, True)
check("  and not from the pre-judge's reason string",
      'zone=cut_entry.get("gate")' in ADM, False)
check("  the reading reaches the page",
      'cut_entry["ball_lag_implied"]' in ADM, True)
JSX = (ROOT / "frontend" / "src" / "components"
       / "CutVerdicts.jsx").read_text(encoding="utf-8")
check("the panel shows it", "ball_lag_implied" in JSX, True)
GT = (ROOT / "frontend" / "src" / "components"
      / "GateTuning.jsx").read_text(encoding="utf-8")
check("and the editor explains the one gate that needs it",
      "audio_lag_sec" in GT, True)

print("\n-- AND IT TOUCHES NOTHING ELSE")
#
# REPORTED FROM THE FIELD: after setting this gate the audio cutter
# "basically stopped working" -- 0 peaks over the gate, where it had
# been cutting. The suspicion was fair and the answer was no: the
# cutter's own gate was already at x8 on that hole and the loudest
# peak in the clip was x4.86, which the report said in as many words.
#
# But "a new gate cannot disturb an old one" is exactly the property
# nothing was checking, and it is worth a test rather than an
# argument. A gate is a shared, stored dict read by several
# detectors; the next one added deserves this guard too.
_BASE = {"audio_min_ratio": 8.0, "audio_min_separation_sec": 3.0,
         "audio_max_attack_ms": 20.0, "ball_min_contrast": 24.0}
_plain = resolve_gates(dict(_BASE))
for _lag in (0.0, 0.2, 0.6, 2.0, "nonsense", -5):
    _with = resolve_gates(dict(_BASE, **{KEY: _lag}))
    _moved = [k for k in _plain
              if k != KEY and _plain[k] != _with[k]]
    check("setting it to %-9s moves no other gate" % repr(_lag),
          _moved, [])
# AND A ZONE OVERRIDE OF IT IS JUST AS INERT.
_z = resolve_gates(dict(_BASE, **{KEY + "@2": 0.6}), zone=2)
check("a zone override of it moves no other gate",
      [k for k in _plain if k != KEY and _plain[k] != _z[k]], [])
# THE CUTTER NEVER READS IT. Two call sites, both the ball check.
import subprocess  # noqa: E402
_hits = subprocess.run(
    ["grep", "-rn", "audio_lag_sec", "--include=*.py",
     str(ROOT / "backend" / "app" / "routers"),
     str(ROOT / "backend" / "app" / "services")],
    capture_output=True, text=True).stdout.splitlines()
_code = [h for h in _hits
         if "debug3.py" not in h and not h.split(":", 2)[-1].strip()
         .startswith("#")]
# ── WHERE IT MAY BE READ, AND WHY THOSE TWO PLACES ─────────────────
#
# `admin.py`, because that is where the ball check turns a heard
# second into a struck frame.
#
# `production_flow.py`, because the flow STORES it on each cut row and
# PRINTS it in the run's gates sentence. Neither is detection: the cut
# keeps the lag it was made with so that changing the lag tomorrow
# cannot rewrite what an earlier cut was made with, and the sentence
# says the number out loud so that a lag of zero -- an assertion that
# the microphone's peak IS the strike -- stops being invisible.
#
# The guard that matters is unchanged and is the one below: the lag
# must never reach a detector's own module, where it could change what
# is FOUND rather than which frame a find is called.
_ALLOWED = ("admin.py", "production_flow.py")
_stray = [h for h in _code if not any(a in h for a in _ALLOWED)]
check("it is read only where the ball check runs, or where the "
      "flow records and reports it", _stray, [])
check("  and never in a detector's own module",
      any("d3.py" in h or "debug2.py" in h for h in _code), False)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
