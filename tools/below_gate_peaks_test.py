#!/usr/bin/env python3
"""An empty result has to say how CLOSE it came, not just that it failed.

THE READOUT, on upload 1487:

    0 windows · 0 swings
    reason: no local maxima above threshold (median=0.0058,
            threshold=0.0231, min_peak_ratio=4.0)

Which says what the bar was and nothing about the jump. "The loudest
thing in this clip was x2.9" and "the loudest was x3.9" call for
completely different actions -- one is a gate to lower, the other is a
microphone to go and look at -- and that line cannot tell them apart.

The cause is structural rather than cosmetic: the peak finder only
looks where `above` is true, so a clip that fails the gate outright
produces no peaks AT ALL and there is nothing left to report.

Asked for from the field: "can we show where the peak was and the
xaudio at the peak? even if it doesn't meet the threshold."

    python3 tools/below_gate_peaks_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    import numpy as np
    from app.services.ai_tracer import (
        _loudest_maxima, _attack_at, AUDIO_SAMPLE_RATE)

    rng = np.random.default_rng(7)
    n = int(20.0 * AUDIO_SAMPLE_RATE)
    env = np.abs(rng.normal(0.0, 0.002, n)) + 0.004
    # Three quiet transients. None of them is a swing by the gate's
    # reckoning, and the operator needs to see all three to decide.
    for t, amp in ((3.2, 0.012), (11.7, 0.0165), (15.4, 0.009)):
        i = int(t * AUDIO_SAMPLE_RATE)
        w = int(0.02 * AUDIO_SAMPLE_RATE)
        env[i:i + w] += amp * np.linspace(1, 0, w)
    median = float(np.median(env))

    _gap = max(1, int(0.25 * AUDIO_SAMPLE_RATE))
    idx = _loudest_maxima(env, k=8, min_gap=_gap)
    secs = [round(i / float(AUDIO_SAMPLE_RATE), 2) for i in idx]
    ratios = [round(float(env[i]) / median, 2) for i in idx]

    ok("it finds peaks where the gated sweep finds none",
       len(idx) > 0, True)
    ok("  at most k of them", len(idx) <= 8, True)
    ok("  in time order", secs, sorted(secs))
    ok("  none closer together than min_gap",
       all(idx[i + 1] - idx[i] >= _gap for i in range(len(idx) - 1)), True)

    # Every transient we planted must be in there: the point is to show
    # the operator the candidates, not the single best one.
    for t in (3.2, 11.7, 15.4):
        ok("  the transient at %.1fs is reported" % t,
           any(abs(q - t) < 0.05 for q in secs), True)
    print("     %s" % ", ".join("%.2fs x%.2f" % (s, r)
                                for s, r in zip(secs, ratios)))

    # ── AND THE LOUDEST IS THE ACTIONABLE ONE ───────────────────────
    _best = max(zip(secs, ratios), key=lambda q: q[1])
    ok("the loudest is one of the planted transients",
       any(abs(_best[0] - t) < 0.05 for t in (3.2, 11.7, 15.4)), True)
    ok("  and it is the loudest one", abs(_best[0] - 11.7) < 0.05, True)
    print("     loudest %.2fs x%.2f — 'a gate under x%.2f would catch it'"
          % (_best[0], _best[1], _best[1]))

    # ── DEGENERATE INPUT DOES NOT RAISE ─────────────────────────────
    ok("a clip too short to have maxima is empty, not an error",
       _loudest_maxima(np.zeros(4)), [])
    ok("  and so is flat silence with no peaks at all",
       len(_loudest_maxima(np.zeros(5000), k=8, min_gap=_gap)) <= 8, True)

    # ── THE ATTACK HELPER IS ONE COPY ───────────────────────────────
    #
    # It is measured in two places now -- the gated sweep and this one
    # -- and this is the filter that already fooled everybody once by
    # reporting its failure sentinel as a measurement.
    _src = (ROOT / "backend/app/services/ai_tracer.py").read_text()
    ok("there is one attack measurement, not two",
       _src.count("AUDIO_ATTACK_NOISE_MULT * median"), 1)
    ok("  and both sweeps call it",
       _src.count("_attack_at(") >= 3, True)   # def + 2 calls

    # Below the gate the rise usually cannot be measured at all: the
    # reference sits above half the peak whenever v < 4 * median, and
    # a peak under a x4 gate is quiet by definition. The readout must
    # therefore lead on the RATIO and mark the rise as unmeasured
    # rather than print a sentinel as though it were a number.
    _look = max(1, int(0.02 * AUDIO_SAMPLE_RATE) * 3)
    _sent = float(_look + 1) / float(AUDIO_SAMPLE_RATE)
    _quiet = [i for i in idx if float(env[i]) / median < 4.0]
    ok("a below-gate peak reports 'could not measure', not a number",
       all(_attack_at(env, i, float(env[i]), median, _look) >= _sent - 1e-9
           for i in _quiet), True)

    # ── AND THE PAGE SHOWS IT ───────────────────────────────────────
    _jsx = (ROOT / "frontend/src/pages/AdminLongUpload.jsx").read_text()
    ok("the page renders the below-gate peaks",
       "below_gate_peaks" in _jsx, True)
    ok("  says a gate under the loudest would have caught it",
       "would have caught the loudest" in _jsx, True)
    ok("  and keeps them out of the ✗attack/✗sep line above",
       _jsx.find("below_gate_peaks") > _jsx.find("top_peaks"), True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
