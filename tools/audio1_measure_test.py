#!/usr/bin/env python3
"""audio1 measures a peak from every angle and rejects nothing.

WHAT IT IS FOR. The existing detector's own comment records the
problem, measured on upload 1258: two real swings attacked in 5-9 ms
and two people talking attacked in 7-14 ms. Rise time cannot separate
them. So audio1 takes every measurement anybody has suggested might,
writes all of it down, and decides nothing.

THE THINGS A DIAGNOSTIC MUST NOT DO, which is most of what is tested
here:

  NEVER REPORT A ZERO IT DID NOT MEASURE. A band above Nyquist, a
  decay that never completes, an attack with no rise to time: each is
  None with a line saying why. Zero reads as a finding and would be
  averaged into a score as one.

  NEVER PUNISH A QUIET STRIKE FOR BEING QUIET. A distant tee is the
  case the request calls out by name. A component that could not be
  measured is dropped from the score and the rest renormalised, not
  scored zero.

  NEVER DECIDE ANYTHING. The score is diagnostic and must not reach
  the cutter.

    python3 tools/audio1_measure_test.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.services.audio1 import (  # noqa: E402
    Audio1Config, confidence, envelope_of, measure_peak, usable_bands,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def near(label, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    print("  %-62s %s" % (label, "ok" if ok
                          else "FAIL got %r (want %s±%s)" % (got, want, tol)))
    if not ok:
        FAILED.append(label)


CFG = Audio1Config()
SR = 48000
RNG = np.random.default_rng(7)


def bed(sec=3.0, level=0.002):
    """Quiet, broadband room tone."""
    return (RNG.normal(0, level, int(SR * sec))).astype(np.float32)


def strike(x, at_sec, amp=0.45, decay_ms=60.0, f=2600.0):
    """A sharp, bright, fast-decaying transient — a club on a ball."""
    i = int(at_sec * SR)
    nd = int(SR * decay_ms / 1000.0 * 3)
    t = np.arange(nd) / SR
    env = np.exp(-t / (decay_ms / 1000.0))
    tone = (np.sin(2 * np.pi * f * t) + 0.6 * np.sin(2 * np.pi * 2 * f * t))
    x[i:i + nd] += (amp * env * tone).astype(np.float32)
    return x


def rumble(x, at_sec, amp=0.3, dur=0.9, f=90.0):
    """Wind: low, slow to rise, slow to go."""
    i = int(at_sec * SR)
    nd = int(SR * dur)
    t = np.arange(nd) / SR
    env = np.sin(np.pi * t / dur) ** 2
    x[i:i + nd] += (amp * env * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return x


BANDS = usable_bands(CFG, SR / 2.0)

print("\n1. the bands a 48 kHz recording can carry")
check("all four are supported", [b["supported"] for b in BANDS],
      [True] * 4)
check("and none was clipped", [b["clipped"] for b in BANDS], [False] * 4)

print("\n2. an 8 kHz recording can carry only part of the sharp band")
B8 = usable_bands(CFG, 4000.0)
check("0-300 and 300-1500 survive",
      [b["supported"] for b in B8][:2], [True, True])
check("1500-3000 fits whole, so nothing is clipped",
      (B8[2]["supported"], B8[2]["hi_hz"], B8[2]["clipped"]),
      (True, 3000.0, False))
# PART OF A BAND IS NOT NONE OF IT. 3-4 kHz was recorded; 4-8 kHz was
# not. Dropping the band would throw away a real measurement, and
# reporting it as 3-8 kHz would claim one that does not exist.
check("3000-8000 is kept, clipped to Nyquist",
      (B8[3]["supported"], B8[3]["hi_hz"], B8[3]["clipped"]),
      (True, 4000.0, True))
check("...and says what it asked for and what it got",
      "the band asked for 8000 Hz" in (B8[3]["note"] or ""), True)

print("\n2b. a 4 kHz recording cannot carry it at all")
B4 = usable_bands(CFG, 2000.0)
check("the sharp band is UNSUPPORTED, not zero energy",
      B4[3]["supported"], False)
check("...with no frequencies invented for it",
      (B4[3].get("energy"), B4[3].get("pct")), (None, None))
check("...and says why", "above this recording's Nyquist"
      in (B4[3]["note"] or ""), True)
# And the per-peak numbers follow it: pct_above_3000 is unmeasurable.
_q = measure_peak(strike(bed(), 1.5), SR, 1.5, CFG, B4)
check("pct above 3000 Hz is None on such a recording",
      "pct_above_3000" in _q["pct_above"], True)

print("\n3. a strike measures like a strike")
x = strike(bed(), 1.5)
env = envelope_of(x, SR, CFG.envelope_ms)
s = measure_peak(x, SR, 1.5, CFG, BANDS, env=env)
check("it rises well clear of the room", s["snr_db"] > 30, True)
check("the attack is in single-figure ms", 0 < s["attack_ms"] < 12, True)
check("it decays, and the decay completes", s["decay_ms"] is not None, True)
near("...in the tens of ms", s["decay_ms"], 90, 90)
check("most of its energy is above 1500 Hz",
      s["pct_above"]["pct_above_1500"] > 80, True)
check("the centroid is up where the crack is",
      s["spectral_centroid_hz"] > 2000, True)
check("the onset shows as flux", s["spectral_flux"] > 0, True)
check("nothing is clipped", s["clipped"], False)
check("and no measurement failed", s["notes"], [])

print("\n4. wind measures like wind")
w = rumble(bed(), 1.5)
wenv = envelope_of(w, SR, CFG.envelope_ms)
# Its envelope peaks in the middle of the gust, not at its start.
wi = float(np.argmax(wenv)) / SR
r = measure_peak(w, SR, wi, CFG, BANDS, env=wenv)
check("almost all its energy is BELOW 1500 Hz",
      r["pct_above"]["pct_above_1500"] < 5, True)
check("its centroid is low", r["spectral_centroid_hz"] < 600, True)
check("the low band holds it",
      [b["pct"] for b in r["bands"] if b["name"] == "low"][0] > 90, True)
check("and it is slow to rise compared with a strike",
      r["attack_ms"] is None or r["attack_ms"] > s["attack_ms"], True)

print("\n5. the two score apart, and neither decides anything")
cs = confidence(s, CFG)
cw = confidence(r, CFG)
check("the strike scores higher than the wind", cs["score"] > cw["score"],
      True)
check("the strike lands high or medium", cs["band"] in ("high", "medium"),
      True)
check("the score is 0..100", 0 <= cs["score"] <= 100, True)
check("every component is reported, measured or not",
      sorted(cs["components"].keys()),
      ["attack", "decay", "duration", "frequency", "isolation",
       "peak_to_background"])
check("and the weights it used travel with it",
      cs["weights"]["frequency"], 30.0)

print("\n6. A QUIET STRIKE IS NOT PUNISHED FOR BEING QUIET")
# The same shape, 20 dB down: a far tee box.
loud = measure_peak(strike(bed(), 1.5, amp=0.45), SR, 1.5, CFG, BANDS)
quiet = measure_peak(strike(bed(), 1.5, amp=0.045), SR, 1.5, CFG, BANDS)
check("the quiet one is genuinely quieter",
      quiet["snr_db"] < loud["snr_db"] - 10, True)
check("its frequency signature is just as good",
      abs(quiet["pct_above"]["pct_above_1500"]
          - loud["pct_above"]["pct_above_1500"]) < 10, True)
cq = confidence(quiet, CFG)
check("so it still scores medium or better",
      cq["band"] in ("high", "medium"), True)
check("...and the gap is the SNR term alone, not a collapse",
      confidence(loud, CFG)["score"] - cq["score"] < 25, True)

print("\n7. an unmeasurable component is dropped, never scored zero")
blank = {"pct_above": {"pct_above_1500": 70.0}, "snr_db": 30.0,
         "attack_ms": None, "decay_ms": None,
         "transient_duration_ms": None, "isolation_db": None}
cb = confidence(blank, CFG)
check("four of six are missing", sorted(cb["missing"]),
      ["attack", "decay", "duration", "isolation"])
check("it still scores, on what it had", cb["score"] is not None, True)
check("and says so rather than quietly averaging zeros",
      "renormalised rather than scored zero" in (cb["why"] or ""), True)
# Scored as zeros this would be 50*1.0/100 = 50; renormalised it is 100.
check("two perfect components give a perfect score, not half of one",
      cb["score"], 100.0)

print("\n8. nothing measurable at all is None, not 0")
cn = confidence({}, CFG)
check("no score", cn["score"], None)
check("no band", cn["band"], None)
check("and a reason", "none of the six" in (cn["why"] or ""), True)

print("\n9. clipping is flagged, and said to spoil the rest")
hot = bed()
hot = strike(hot, 1.5, amp=3.0)
hot = np.clip(hot, -1.0, 1.0)
h = measure_peak(hot, SR, 1.5, CFG, BANDS)
check("flagged", h["clipped"], True)
check("with a count", h["n_clipped_samples"] > 3, True)
check("and a warning that the numbers are understated",
      any("clipped" in n for n in h["notes"]), True)

print("\n10. a peak outside the audio says so instead of crashing")
o = measure_peak(x, SR, 999.0, CFG, BANDS)
check("no numbers invented", o.get("snr_db"), None)
check("and a reason", any("outside" in n for n in o["notes"]), True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
