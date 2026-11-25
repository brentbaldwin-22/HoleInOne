#!/usr/bin/env python3
"""Two peak ratios, one name, and only one of them is gated.

THE DETECTOR'S (det1), in ai_tracer.detect_swings_from_audio:
  22050 Hz, HIGH-PASSED at 1500 Hz, 10 ms envelope, and the
  denominator is THE MEDIAN OF THE WHOLE CLIP. One number for every
  peak in the recording.

audio1's, in audio1.measure_peak:
  the camera's own rate, UNFILTERED, 2 ms envelope, and the
  denominator is a LOCAL floor -- the median of the 500 ms ending
  50 ms before THIS peak.

FOUR THINGS DIFFER AT ONCE, so neither reads as a correction of the
other: band, sample rate, envelope length, and the denominator. The
last is the one that moves the number: on a clip that is quiet apart
from a few strikes they agree closely; on a clip with a mower running
through part of it the local floor rises beside the mower and the
clip median barely moves, so audio1's ratio collapses where the
detector's does not.

AND `min peak ratio` ENFORCES THE DETECTOR'S. It is passed to the
detector as min_peak_ratio, so it decides which peaks are found at
all; audio1's ratio is never gated on. Both are now in the table and
the CSV so the two can be scored against labels independently.

    python3 tools/two_ratios_test.py
"""
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.services import audio1 as a1  # noqa: E402
from app.services.ai_tracer import detect_swings_from_audio  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SR = 16000


def write(path, x):
    wav = path.with_suffix(".wav")
    w = wave.open(str(wav), "w")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "aac", "-b:a", "96k", str(path)], check=True)


def strike(x, at):
    i = int(at * SR)
    nd = int(SR * 0.045 * 4)
    t = np.arange(nd) / SR
    env = np.exp(-t / 0.045)
    x[i:i + nd] += (0.45 * env * (np.sin(2 * np.pi * 2600 * t)
                                  + 0.7 * np.sin(2 * np.pi * 5200 * t))
                    ).astype(np.float32)


def ratios(src):
    dbg = {}
    hits = detect_swings_from_audio(src, fps=30.0, min_peak_ratio=1.3,
                                    min_separation_sec=2.0,
                                    max_attack_sec=0.030, debug=dbg) or []
    h = next((x for x in hits if abs(x["peak_time_sec"] - 10.0) < 0.3), None)
    out = a1.analyse(src, [{"time_sec": 10.0, "passed_gate": True}])
    m = (out.get("peaks") or [{}])[0]
    return (h or {}).get("ratio"), m.get("peak_ratio"), m, dbg


print(__doc__.strip().splitlines()[0])

print("\n1. both ratios reach the CSV, under names that tell them apart")
names = [c[0] for c in a1.CSV_COLUMNS]
check("the detector's is exported", "detector_ratio" in names)
check("audio1's is exported", "peak_ratio" in names)
_lbl = dict(a1.CSV_COLUMNS)
check("and the headings say which is which",
      "detector" in _lbl["detector_ratio"]
      and "audio1" in _lbl["peak_ratio"], True)
row = a1.flat_row({"peak_time_sec": 1.0, "detector_ratio": 7.0,
                   "peak_ratio": 3.2})
check("a row carries both", (row["detector_ratio"], row["peak_ratio"]),
      (7.0, 3.2))
check("and a missing one stays missing, not zero",
      a1.flat_row({"peak_time_sec": 1.0})["detector_ratio"], None)

print("\n2. the table shows both, under the file's det1 convention")
PANEL = (ROOT / "frontend/src/components/Audio1Panel.jsx").read_text()
check("the peak group carries three values",
      'subs: ["amp", "det¹", "a1"]' in PANEL)
check("the detector's", "p.detector_ratio" in PANEL)
check("and audio1's", "p.peak_ratio" in PANEL)

print("\n3. both are documented, and the doc says which one is gated")
_how = {h["name"]: h for h in a1.HOW_MEASURED}
_det = next(k for k in _how if "DETECTOR" in k)
_a1 = next(k for k in _how if "audio1" in k and "ratio" in k)
check("the detector's is from ai_tracer",
      "ai_tracer" in _how[_det]["where"])
check("...and says the denominator is the whole clip",
      "WHOLE CLIP" in _how[_det]["how"])
check("...and that the gate enforces it",
      "audio_min_ratio" in _how[_det].get("gate", ""))
check("audio1's is from measure_peak",
      "measure_peak" in _how[_a1]["where"])
check("...and says the denominator is local",
      "500 ms ending" in _how[_a1]["how"])
check("...and explains why they differ",
      "why_it_differs" in _how[_a1])

print("\n4. the same strike, two surroundings: only one ratio moves")
with TemporaryDirectory() as tmp:
    rng = np.random.default_rng(5)
    quiet = Path(tmp) / "quiet.mp4"
    x = rng.normal(0, 0.004, SR * 20).astype(np.float32)
    strike(x, 10.0)
    write(quiet, x)

    mower = Path(tmp) / "mower.mp4"
    y = rng.normal(0, 0.004, SR * 20).astype(np.float32)
    i0, i1 = int(5 * SR), int(15 * SR)
    t = np.arange(i1 - i0) / SR
    y[i0:i1] += (0.05 * np.sin(2 * np.pi * 180 * t)
                 + rng.normal(0, 0.02, i1 - i0)).astype(np.float32)
    strike(y, 10.0)
    write(mower, y)

    dq, aq, mq, gq = ratios(quiet)
    dm, am, mm, gm = ratios(mower)
    print("    quiet: det1 %.1f  audio1 %.1f" % (dq, aq))
    print("    mower: det1 %.1f  audio1 %.1f" % (dm, am))

    check("the detector's barely moves", abs(dm - dq) / dq < 0.3, True)
    # THE POINT: the local floor rose elevenfold beside the mower and
    # the clip median hardly moved, because the mower is only half the
    # clip and a median is not an average.
    check("audio1's collapses", am < aq / 5.0, True)
    check("the local floor is the reason it did",
          mm["background_level"] > 5 * mq["background_level"], True)
    check("...while the clip median is not",
          gm["median_envelope"] < 2 * gq["median_envelope"], True)
    # And they differ even on the quiet clip -- the high-pass alone
    # moves the denominator, so they are never interchangeable.
    check("they disagree even with nothing else happening",
          abs(dq - aq) > 1.0, True)

print("\n5. each ratio has its own gate, reading its own measurement")
from app.routers.admin import _audio1_enabled_gates  # noqa: E402
from app.services import debug3 as d3  # noqa: E402

check("the detector's gate reads the detector's ratio",
      d3.AUDIO_GATE_FIELDS["audio_min_ratio"][1], "detector_ratio")
check("the local gate reads audio1's",
      d3.AUDIO_GATE_FIELDS["audio_min_local_ratio"][1], "peak_ratio")
check("and their labels tell them apart",
      d3.AUDIO_GATE_FIELDS["audio_min_ratio"][2]
      != d3.AUDIO_GATE_FIELDS["audio_min_local_ratio"][2], True)

_live = d3.resolve_gates({"audio_min_ratio@1": 3.0, "audio1_armed@1": 1.0},
                         zone=1)
check("audio_min_ratio resolves", _live["audio_min_ratio"], 3.0)
check("but is NOT an audio1 post-filter",
      "audio_min_ratio" in _audio1_enabled_gates(_live), False)
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
check("it is handed to the detector instead",
      'min_peak_ratio=float(_ag["audio_min_ratio"])' in SRC, True)
check("the local one IS a post-filter",
      "audio_min_local_ratio" in _audio1_enabled_gates(
          d3.resolve_gates({"audio_min_local_ratio@1": 6.0}, zone=1)), True)
check("...and is never handed to the detector",
      "min_peak_ratio=float(_ag[\"audio_min_local_ratio\"])" in SRC, False)

print("\n6. the new gate is OFF until somebody sets it")
_d = d3.resolve_gates({}, zone=1)
check("default is zero", _d["audio_min_local_ratio"], 0.0)
check("...which is off", "audio_min_local_ratio" in _audio1_enabled_gates(_d),
      False)
check("no tee preset turns it on",
      any("audio_min_local_ratio" in p["gates"]
          for p in d3.AUDIO_TEE_PRESETS.values()), False)

print("\n7. and it is configurable per tee box, like every other gate")
_ov = {"audio_min_local_ratio@1": 6.0}
check("zone 1 gets it", d3.resolve_gates(_ov, zone=1)["audio_min_local_ratio"],
      6.0)
check("zone 0 does not", d3.resolve_gates(_ov, zone=0)["audio_min_local_ratio"],
      0.0)
_both = {"audio_min_ratio@1": 1.3, "audio_min_local_ratio@1": 6.0,
         "audio_min_ratio@0": 3.5}
_z1 = d3.resolve_gates(_both, zone=1)
_z0 = d3.resolve_gates(_both, zone=0)
check("the two ratios are set independently per tee",
      [_z1["audio_min_ratio"], _z1["audio_min_local_ratio"],
       _z0["audio_min_ratio"], _z0["audio_min_local_ratio"]],
      [1.3, 6.0, 3.5, 0.0])

print("\n8. the detector-gate set is named once, not copied")
check("debug3 holds it", "audio_min_ratio" in d3.DETECTOR_AUDIO_GATES)
check("...with attack and separation",
      sorted(d3.DETECTOR_AUDIO_GATES),
      ["audio_max_attack_ms", "audio_min_ratio", "audio_min_separation_sec"])
check("the local ratio is NOT one of them",
      "audio_min_local_ratio" in d3.DETECTOR_AUDIO_GATES, False)
check("and admin reads that set rather than its own copy",
      SRC.count('"audio_min_ratio", "audio_max_attack_ms"'), 0)
check("...using it in several places", SRC.count("DETECTOR_AUDIO_GATES") >= 4,
      True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
