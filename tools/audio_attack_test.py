#!/usr/bin/env python3
"""Does the audio swing detector reject a QUIET impact for being quiet?

THE CLAIM UNDER TEST. `detect_swings_from_audio` measures attack time
by walking back from the peak to the most recent sample below
`0.10 * peak`. That reference is a fraction of the PEAK, while the
noise floor is absolute -- so for a peak only ~10x the median, the
reference lands AT the floor, the walk-back finds no crossing, and the
peak is scored `attack_lookback + 1` (90 ms) and dropped as
"sustained". A loud strike and a quiet strike of IDENTICAL SHAPE
therefore get opposite verdicts.

Observed on upload 1258 (back tees, swings at ~33 s and ~72 s): the
x30 strike measured 7-9 ms and was kept; the x9.7 strike measured
"90 ms" and was dropped. Every rejected peak in that run reported
exactly 90 ms -- one value, never a spread -- which is what a failure
sentinel looks like and what a real measurement does not.

The fixtures below hold SHAPE constant and vary only amplitude, so a
difference in verdict can only come from the amplitude.

    python3 tools/audio_attack_test.py
"""
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.ai_tracer import detect_swings_from_audio  # noqa: E402

SR = 22050
DUR = 80.0
RNG = np.random.default_rng(20261026)


def _impact(n, rise_ms=2.0, decay_ms=30.0):
    """A club-strike-shaped burst: fast rise, short decay, HF content.

    Carrier is well above the detector's 1.5 kHz high-pass so the
    filter does not eat the fixture.
    """
    t = np.arange(n) / SR
    rise = np.clip(t / (rise_ms / 1000.0), 0, 1)
    decay = np.exp(-t / (decay_ms / 1000.0))
    carrier = RNG.standard_normal(n)
    return (rise * decay * carrier).astype(np.float32)


def _sustained(n, rise_ms=150.0):
    """A slow-rising blob in the same band. Must be rejected.

    NOT A STAND-IN FOR TALKING, and do not read its rejection as
    evidence the attack filter removes speech. It does not. Measured
    on upload 1258, people talking attacked in 7.7-13.7 ms against
    real swings at 5.3-9.4 ms -- indistinguishable. The 1.5 kHz
    high-pass that runs first strips the vowels and leaves the
    consonants, so by the time this filter sees speech it is already
    impulsive. This fixture stands for rumble, wind and engine noise,
    which stay sustained in any band; amplitude is what separates a
    swing from a voice, and that is min_peak_ratio's job.
    """
    t = np.arange(n) / SR
    rise = np.clip(t / (rise_ms / 1000.0), 0, 1)
    hold = np.ones(n)
    env = np.minimum(rise, hold)
    carrier = RNG.standard_normal(n)
    return (env * carrier).astype(np.float32)


def build(path: Path, events):
    n = int(DUR * SR)
    buf = (RNG.standard_normal(n) * 0.004).astype(np.float32)   # floor
    for at_sec, amp, kind in events:
        seg_n = int(0.30 * SR)
        seg = _impact(seg_n) if kind == "impact" else _sustained(seg_n)
        seg = seg / (np.abs(seg).max() or 1.0) * amp
        i = int(at_sec * SR)
        buf[i:i + seg_n] += seg[: max(0, n - i)]
    buf = np.clip(buf, -1.0, 1.0)
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def run(path: Path, ratio=1.0):
    dbg: dict = {}
    out = detect_swings_from_audio(path, fps=60.0, min_peak_ratio=ratio,
                                   debug=dbg)
    return [round(s["peak_time_sec"], 1) for s in out], dbg


def main():
    tmp = Path(tempfile.mkdtemp(prefix="attacktest-"))
    wav = tmp / "fix.wav"
    # Same SHAPE at 33 s and 72 s -- only the amplitude differs, chosen
    # to land near the x30 / x9.7 of the real clip.
    build(wav, [
        (33.0, 0.30, "impact"),     # loud strike
        (52.0, 0.06, "sustained"),  # voice-ish, must stay rejected
        (72.0, 0.09, "impact"),     # QUIET strike, identical shape
    ])

    found, dbg = run(wav)
    print("detected swings:", found)
    print("median=%.5f  threshold=%.5f  raw=%s  after_attack=%s" % (
        dbg.get("median_envelope", 0), dbg.get("threshold", 0),
        dbg.get("n_raw_peaks"), dbg.get("n_after_attack")))
    if dbg.get("reason"):
        print("reason:", dbg["reason"])
    for p in (dbg.get("top_peaks") or [])[:12]:
        print("   %6.2fs  x%-6.2f %6.1fms  attack_ok=%s" % (
            p["peak_sec"], p["ratio"] or 0, p["attack_ms"] or 0,
            p["passes_attack"]))

    loud_ok = any(abs(f - 33.0) < 1.0 for f in found)
    quiet_ok = any(abs(f - 72.0) < 1.0 for f in found)
    voice_in = any(abs(f - 52.0) < 1.0 for f in found)

    print()
    print("loud  strike @33s caught :", loud_ok)
    print("quiet strike @72s caught :", quiet_ok)
    print("voice @52s wrongly kept  :", voice_in)
    print()
    if loud_ok and quiet_ok and not voice_in:
        print("ALL PASS")
        return 0
    print("FAIL — identical shapes, different verdicts" if loud_ok and not quiet_ok
          else "FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
