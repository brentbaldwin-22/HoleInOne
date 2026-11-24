#!/usr/bin/env python3
"""Windowed energy, concentration ratios, and why a decay runs long.

CONCENTRATION IS THE IDEA: a struck ball puts nearly all of its energy
into a few milliseconds, and a voice, a mat and a gust do not. The
ratios make that a number — but only if the arithmetic is right in
three specific ways, each of which would otherwise produce a
plausible-looking figure that means something else:

  INTEGRATED ENERGY, NOT AVERAGE POWER. Asked for by name. Mean power
  over 10 ms against mean power over 100 ms is mostly a statement
  about window lengths; the sums say what fraction is in the middle.

  BACKGROUND-ADJUSTED, so the same strike measures the same in a
  loud room and a quiet one. The background's energy PER SAMPLE times
  the window length comes off each window — scaling with the window,
  which a flat subtraction would not.

  THE HF COPY IS FILTERED, NOT RE-SUMMED. Windows of five different
  lengths have five different bin spacings, so summing each one's
  spectrum above 1500 Hz puts a length-dependent error exactly where
  the ratio is read.

AND THE DECAY INVESTIGATION. Four synthetic cases, which between them
are every way the number goes wrong.

    python3 tools/audio1_energy_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.services.audio1 import (  # noqa: E402
    Audio1Config, HOW_MEASURED, _energy, highpass_slice, measure_peak,
    usable_bands,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


CFG = Audio1Config()
SR = 48000
B = usable_bands(CFG, SR / 2.0)


def clip(events, sec=4.0, floor=0.003, seed=1):
    """A synthetic clip, reproducible ON ITS OWN.

    SEEDED PER CALL, not from one generator shared down the file. With
    a shared generator every clip depends on how many random numbers
    the sections above it happened to draw, so adding an assertion
    anywhere silently changes the noise under every case below it —
    which is exactly how the decay case here started failing while
    the behaviour it tests was correct.
    """
    rng = np.random.default_rng(seed)
    x = rng.normal(0, floor, int(SR * sec)).astype(np.float32)
    for at, amp, dec, f in events:
        i = int(at * SR)
        t = np.arange(int(SR * dec * 4)) / SR
        x[i:i + t.size] += (amp * np.exp(-t / dec)
                            * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return x


print("\n1. energy is integrated, not averaged")
# Two windows of a constant signal: twice the samples, twice the sum.
flat = np.ones(1000, np.float32)
check("doubling the window doubles the energy",
      round(_energy(flat[:200]) / _energy(flat[:100]), 6), 2.0)
check("...which averaging would have made 1.0",
      round(float(np.mean(flat[:200] ** 2))
            / float(np.mean(flat[:100] ** 2)), 6), 1.0)

print("\n2. the windows nest, so the ratios are concentrations")
m = measure_peak(clip([(1.5, 0.5, 0.05, 2400)]), SR, 1.5, CFG, B)
e = m["energy"]
check("all five windows measured",
      sorted(e.keys()), ["w10", "w100", "w20", "w200", "w50"])
check("each is at least as big as the one inside it",
      all(e["w%d" % a] <= e["w%d" % b] + 1e-9
          for a, b in ((10, 20), (20, 50), (50, 100), (100, 200))), True)
c = m["concentration"]
check("every ratio is between 0 and 1",
      all(0.0 <= v <= 1.0 for v in c.values()), True)
check("the three the request named",
      sorted(c.keys()),
      ["e10_over_e100", "e20_over_e200", "e50_over_e200"])
check("and the HF copy has the same three",
      sorted(m["concentration_hf"].keys()), sorted(c.keys()))

print("\n3. A STRIKE IS CONCENTRATED; A VOICE IS NOT")
v = np.random.default_rng(42).normal(
    0, 0.003, SR * 4).astype(np.float32)
_t = np.arange(int(SR * 0.45)) / SR
v[int(1.5 * SR):int(1.5 * SR) + _t.size] += (
    0.25 * np.sin(np.pi * _t / 0.45) ** 2
    * np.sin(2 * np.pi * 700 * _t)).astype(np.float32)
mv = measure_peak(v, SR, 1.72, CFG, B)
check("the strike holds a third of 200 ms inside its middle 20 ms",
      c["e20_over_e200"] > 0.28, True)
check("the sustained sound holds far less",
      mv["concentration"]["e20_over_e200"] < 0.2, True)
check("...and the strike beats it by a clear margin",
      c["e20_over_e200"] > 1.5 * mv["concentration"]["e20_over_e200"],
      True)

print("\n4. background adjustment makes the room irrelevant")
quiet_room = measure_peak(clip([(1.5, 0.3, 0.05, 2400)], floor=0.001),
                          SR, 1.5, CFG, B)
loud_room = measure_peak(clip([(1.5, 0.3, 0.05, 2400)], floor=0.01),
                         SR, 1.5, CFG, B)
check("the energies are within a few percent across a 10x floor",
      abs(quiet_room["energy"]["w20"] - loud_room["energy"]["w20"])
      / quiet_room["energy"]["w20"] < 0.08, True)
check("the subtraction scales with the window, so the 200 ms one too",
      abs(quiet_room["energy"]["w200"] - loud_room["energy"]["w200"])
      / quiet_room["energy"]["w200"] < 0.15, True)
check("and it never goes negative",
      all(x >= 0 for x in loud_room["energy"].values()), True)

print("\n5. the HF copy is a filtered signal, not a re-summed spectrum")
x = clip([(1.5, 0.4, 0.05, 2400)])
seg = x[int(1.4 * SR):int(1.6 * SR)]
hf = highpass_slice(seg, SR, 1500.0)
check("the filter returns the same length", hf.size, seg.size)
low = clip([(1.5, 0.4, 0.05, 300)])
mlow = measure_peak(low, SR, 1.5, CFG, B)
check("a 300 Hz sound has almost no HF energy",
      mlow["energy_hf"]["w50"] < 0.02 * mlow["energy"]["w50"], True)
check("a 2400 Hz sound is nearly all HF",
      m["energy_hf"]["w50"] > 0.8 * m["energy"]["w50"], True)

print("\n6. the post-impact intervals, and their shape")
check("the four the request named", sorted(m["post"].keys()),
      ["ms0_20", "ms100_200", "ms20_50", "ms50_100"])
sh = m["post_shape"]
check("a strike empties out, interval by interval",
      sh["ms0_20"] >= sh["ms20_50"] >= sh["ms50_100"] >= sh["ms100_200"],
      True)
shv = mv["post_shape"]
check("a sustained sound does NOT — it holds up or climbs",
      shv["ms20_50"] > shv["ms0_20"], True)
# A SECOND SOUND SHOWS UP AS A LATE BUMP.
two = measure_peak(clip([(1.5, 0.5, 0.05, 2400),
                         (1.65, 0.25, 0.05, 900)]), SR, 1.5, CFG, B)
check("a second sound 150 ms later lifts the last interval back up",
      two["post_shape"]["ms100_200"] > two["post_shape"]["ms50_100"], True)

print("\n7. THE DECAY INVESTIGATION — all four cases, each named")
cases = {
    "clean": clip([(1.5, 0.5, 0.05, 2400)]),
    "second sound +150ms": clip([(1.5, 0.5, 0.05, 2400),
                                 (1.65, 0.25, 0.05, 900)]),
    "unrelated +600ms": clip([(1.5, 0.5, 0.05, 2400),
                              (2.1, 0.3, 0.30, 500)]),
    "quiet on a noisy floor": clip([(1.5, 0.05, 0.05, 2400)], floor=0.02),
}
got = {k: measure_peak(v, SR, 1.5, CFG, B) for k, v in cases.items()}
check("a clean strike settles first time",
      got["clean"]["decay_end_reason"].startswith("fell and stayed"), True)
check("...in well under the suspect length",
      got["clean"]["decay_ms"] < CFG.decay_suspect_ms, True)
check("A LATER SOUND IS NAMED AS ONE, not reported as decay",
      "later sound" in got["second sound +150ms"]["decay_end_reason"], True)
check("...and the inflated number is flagged in the notes",
      any("later sound" in n
          for n in got["second sound +150ms"]["notes"]), True)
check("...and it really is inflated",
      got["second sound +150ms"]["decay_ms"] > got["clean"]["decay_ms"],
      True)
check("a sound AFTER the hold completed changes nothing",
      got["unrelated +600ms"]["decay_end_reason"].startswith("fell and"),
      True)
# A QUIET STRIKE OVER A NOISY FLOOR GOES ONE OF TWO WAYS, and which
# depends only on where the noise happens to fall: either it finally
# finds 20 ms of quiet after a second and a half (a huge number), or
# it never does (no number at all). Both are the SAME finding — the
# 10% floor is sitting inside the ambience — and the request named
# both halves: "exceed 1,000ms or return no value". Both wordings are
# exercised here rather than whichever one this seed produces.
for _seed, _which in ((1, "never settles"), (7, "settles very late")):
    _n = measure_peak(clip([(1.5, 0.05, 0.05, 2400)], floor=0.02,
                           seed=_seed), SR, 1.5, CFG, B)
    _r = _n["decay_end_reason"] or ""
    _settled = _n["decay_ms"] is not None
    check("  noisy floor (%s): no usable decay" % _which,
          (not _settled) or _n["decay_ms"] > CFG.decay_suspect_ms, True)
    check("  ...and the reason names the ambience, not a decay",
          ("not a decay" in _r) or ("never stayed under" in _r)
          or ("never fell under" in _r), True)
    check("  ...identified by how little quiet there was to find",
          (_n["decay_longest_quiet_ms"] or 0)
          < CFG.decay_quiet_floor_mult * CFG.decay_hold_ms, True)
    check("  ...and it is in the notes, not only in a field",
          any(_r[:30] in _x for _x in _n["notes"]), True)
check("...while the clean strike had seconds of quiet available",
      got["clean"]["decay_longest_quiet_ms"] > 1000, True)
# THE BUG THE FIRST VERSION HAD: envelope jitter counted as sounds.
check("a clean strike reports ZERO sounds after it",
      got["clean"]["decay_resets"], 0)
check("...even though its envelope touched the floor several times",
      got["clean"]["decay_crossings"] > 1, True)

print("\n8. every one of them is documented, from the code")
names = " ".join(h["name"] for h in HOW_MEASURED)
for want in ("original attack", "SNR", "HF SNR", "isolation", "decay"):
    check("  %r is documented" % want, want in names, True)
check("the documentation says which signal each is taken off",
      all("signal" in h or "how" in h for h in HOW_MEASURED), True)
check("...and the 1500 Hz high-pass trap is called out",
      any("1500" in (h.get("signal") or "") for h in HOW_MEASURED), True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
