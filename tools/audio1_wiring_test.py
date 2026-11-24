#!/usr/bin/env python3
"""audio1 measures the detector's peaks — all of them — and cuts nothing.

THE TWO PROMISES THIS FILE HOLDS THE CODE TO:

  audio1 IS NOT A SECOND CUTTER. "Preserve the existing audio
  detection and clip-cutting behavior. Do not introduce new rejection
  thresholds yet." So it runs `detect_swings_from_audio` on the same
  knobs and the windows that come back are bit-for-bit the ones
  `audio` would have produced. A classification is only evidence
  about the detector that found the thing classified; measuring a
  second, differently-found set of peaks would make every label
  useless.

  THE REFUSED PEAKS ARE MEASURED TOO. A false positive shows up among
  the clips we cut. A false negative only ever shows up among the
  peaks we threw away — so a diagnostic that measures the accepted
  windows alone can never show one, which is half the problem and the
  harder half.

    python3 tools/audio1_wiring_test.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.routers import admin as A  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("\n1. every peak reaches the measurement pass, once each")
# The accepted windows and `top_peaks` describe the SAME sounds; an
# accepted peak appears in both and must not be measured twice.
peaks = A._audio1_peak_list(
    [{"peak_time_sec": 10.0, "ratio": 30.0, "attack_ms": 7.0},
     {"peak_time_sec": 42.5, "ratio": 12.0, "attack_ms": 9.0}],
    {"top_peaks": [
        {"peak_sec": 10.0, "ratio": 30.0, "kept": True},
        {"peak_sec": 20.2, "ratio": 8.0, "kept": False},
        {"peak_sec": 42.5, "ratio": 12.0, "kept": True},
     ],
     "below_gate_peaks": [{"peak_sec": 55.9, "ratio": 2.9}]})
check("four distinct sounds, not six rows",
      [round(p["time_sec"], 2) for p in peaks], [10.0, 20.2, 42.5, 55.9])
check("the two the detector cut are marked as cut",
      [p["time_sec"] for p in peaks if p["passed_gate"]], [10.0, 42.5])
check("THE REFUSED ONES ARE KEPT, which is where a miss would be",
      [p["time_sec"] for p in peaks if not p["passed_gate"]], [20.2, 55.9])
check("and each says where it came from",
      sorted({p["source"] for p in peaks}), ["below gate", "cut", "raw"])
# THE CUT ENTRY IS FINAL. `top_peaks` carries a ROUNDED copy of a peak
# the detector already reported in full; letting it overwrite the
# window's own numbers makes the diagnostic disagree with the run.
check("a cut peak keeps the detector's own ratio, not the rounded copy",
      [p["ratio"] for p in peaks if p["time_sec"] == 10.0], [30.0])
check("...and still reads as cut",
      [p["source"] for p in peaks if p["time_sec"] == 10.0], ["cut"])

print("\n2. an accepted sighting outranks a raw one for the same sound")
mixed = A._audio1_peak_list(
    [{"peak_time_sec": 7.0}],
    {"top_peaks": [{"peak_sec": 7.0, "kept": False}]})
check("one row", len(mixed), 1)
check("...marked cut, because it was", mixed[0]["passed_gate"], True)

print("\n3. a clip with no peaks at all measures nothing and says so")
check("no rows invented", A._audio1_peak_list([], {}), [])

print("\n4. end to end, on real audio through ffmpeg")
SR = 44100
rng = np.random.default_rng(5)
x = rng.normal(0, 0.003, SR * 5).astype(np.float32)
for at in (1.2, 3.4):
    i = int(at * SR)
    t = np.arange(int(SR * 0.2)) / SR
    x[i:i + t.size] += (0.5 * np.exp(-t / 0.05)
                        * np.sin(2 * np.pi * 2400 * t)).astype(np.float32)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    raw, wav = td / "a.raw", td / "a.wav"
    (np.clip(x, -1, 1) * 32767).astype(np.int16).tofile(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le",
                    "-ar", str(SR), "-ac", "1", "-i", str(raw), str(wav)],
                   check=True)
    out = A._audio1_measure(
        1, wav,
        [{"peak_time_sec": 1.2}, {"peak_time_sec": 3.4}],
        {"top_peaks": [{"peak_sec": 2.0, "ratio": 2.0, "kept": False}]})
    check("it ran", out["ok"], True)
    check("the real sample rate was read off the file, not assumed",
          out["probe"]["sample_rate"], SR)
    check("three peaks measured — two cut, one refused",
          (out["n_peaks"], out["n_cut"], out["n_refused"]), (3, 2, 1))
    byt = {round(p["peak_time_sec"], 1): p for p in out["peaks"]}
    check("the strikes are bright", byt[1.2]["pct_above"]["pct_above_1500"]
          > 80, True)
    check("they score, and the score is only a number",
          byt[1.2]["confidence"]["score"] is not None, True)
    check("the refused peak is measured just the same",
          byt[2.0]["snr_db"] is not None, True)
    check("...and still marked refused", byt[2.0]["passed_gate"], False)
    check("every peak got a picture",
          sum(1 for p in out["peaks"] if p.get("image_url")), 3)

print("\n5. a measurement pass that fails costs the measurements only")
bad = A._audio1_measure(1, Path("/nonexistent/none.mp4"), [], {})
check("it does not raise", isinstance(bad, dict), True)
check("it says it could not", bad["ok"], False)
check("with a reason", bool(bad.get("reason")), True)

print("\n6. audio1 asks nothing of the pictures")
# "we don't have to do any swing judgments. i just want the audio
# scores." The verify pass is a VISION CALL PER PEAK, and whether a
# golfer is visible has no bearing on what a waveform looks like.
# Forced off in the runner rather than defaulted off, so it cannot be
# switched on from the page by habit.
_src = (ROOT / "backend/app/routers/admin.py").read_text()
_r = _src[_src.index("def _test_cut_run("):]
_r = _r[:_r.index("\ndef ", 10)]
check("the runner turns it off for audio1",
      'if detector == "audio1" and (verify_swings or judge_with_frames):'
      in _r, True)
check("...both of them", "verify_swings = False" in _r
      and "judge_with_frames = False" in _r, True)
check("...before the verify gate is reached",
      _r.index("verify_swings = False")
      < _r.index("if verify_swings and peak_time_sec is not None:"), True)
check("and the run says it was skipped, not that it failed",
      '"judging_skipped": _judged_off,' in _r, True)
_jsx = (ROOT / "frontend/src/pages/AdminLongUpload.jsx").read_text()
check("the page does not send it either",
      'verifySwings[uploadId] && detector !== "audio1"' in _jsx, True)
check("...and does not offer the box", "no swing judging" in _jsx, True)

print("\n7. the detector branch is shared, not forked")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
check("audio1 runs the same detect_swings_from_audio call",
      'if detector in ("audio", "audio2", "audio1"):' in SRC, True)
check("...and only then measures",
      'if detector == "audio1":\n            debug["audio1"] = '
      '_audio1_measure(' in SRC, True)
check("there is no second windows= assignment for audio1",
      SRC.count("windows = detect_swings_from_audio("), 1)
# NOTHING IN THE SCORE REACHES A DECISION.
_run = SRC[SRC.index("def _test_cut_run("):]
_run = _run[:_run.index("\ndef ", 10)]
for _bad in ("confidence", "audio1_score", "min_confidence"):
    check("no %r gates anything in the cutter" % _bad,
          ("if %s" % _bad) in _run, False)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
