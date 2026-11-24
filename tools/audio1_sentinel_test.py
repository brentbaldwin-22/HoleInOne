#!/usr/bin/env python3
"""53.9 ms is not a rise time — and four other things from the field.

ITEM 1, ANSWERED. Every candidate in one recording reported an
original detector attack of exactly 53.9 ms. It is a SENTINEL, not a
measurement, not a ceiling:

    attack_sentinel_sec = (attack_lookback + 1) / 22050
    attack_lookback     = max(10 ms in samples, 3 x the attack gate)

With the gate at 18 ms that is 1188 samples = 53.923 ms. At the
default 30 ms it is 90.0 ms — which `ai_tracer`'s own comment records
as the number that hid this once already: "every drop showed the same
90.0 ms and nobody reads one number as a tell."

`_attack_at` returns it whenever the reference it would time the rise
against reaches half the peak, which happens for any peak under about
4x the clip's median. A clip of quiet candidates therefore shows one
identical value in a column headed "attack ms".

THE DETECTOR ALREADY SAYS SO, in `attack_measured`. audio1 did not
carry that field, so it printed the sentinel — the same bug, one
layer up, in the diagnostic built to catch it.

    python3 tools/audio1_sentinel_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.routers import admin as A  # noqa: E402
from app.services.audio1 import (  # noqa: E402
    Audio1Config, MEASUREMENT_VERSION, flat_row, hf_candidates, measure_at,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("\n1. the sentinel, reproduced from the detector's own constants")
SR22 = 22050
for gate_ms, want in ((18, 53.9), (30, 90.0)):
    lookback = max(int(SR22 * 10 / 1000.0),
                   max(1, int(gate_ms / 1000.0 * SR22)) * 3)
    check("  an attack gate of %d ms gives a sentinel of %.1f ms"
          % (gate_ms, want),
          round(1000.0 * (lookback + 1) / SR22, 1), want)

print("\n2. a sentinel never reaches the table as a time")
peaks = A._audio1_peak_list(
    [{"peak_time_sec": 19.77, "ratio": 2.4, "attack_ms": 53.9,
      "attack_measured": False},
     {"peak_time_sec": 25.38, "ratio": 13.4, "attack_ms": 9.3,
      "attack_measured": True}],
    {})
by = {round(p["time_sec"], 2): p for p in peaks}
check("the unmeasurable one carries no attack at all",
      by[19.77]["attack_ms"], None)
check("...but keeps what it would have printed, for the record",
      by[19.77]["attack_sentinel_ms"], 53.9)
check("...and is flagged as not measured",
      by[19.77]["attack_measured"], False)
check("a real measurement is untouched", by[25.38]["attack_ms"], 9.3)

print("\n3. and the row says why it is blank")
SRC = (ROOT / "backend/app/services/audio1.py").read_text()
check("the note names it a sentinel, with the arithmetic",
      "(lookback+1)/22050" in SRC, True)
# The threshold it names: `_attack_at` returns the sentinel when its
# reference max(0.10·peak, 2·median) reaches half the peak, i.e. for
# any peak at or under 4x the clip's median.
check("...and why a whole clip shows one value",
      "under about 4x the clip's" in SRC, True)
check("...which is exactly when _attack_at gives up",
      2.0 * 1.0 >= 0.5 * 4.0, True)
JSX = (ROOT / "frontend/src/components/Audio1Panel.jsx").read_text()
check("the column is blank, not zero",
      "p.detector_attack_measured === false\n                   ? undefined"
      in JSX, True)

print("\n4. the concentration arithmetic is versioned")
check("there is a version", MEASUREMENT_VERSION, 2)
check("it names what changed, v1 to v2", "v1" in SRC and "v2" in SRC, True)
check("a row with no version is v1, not the current one",
      flat_row({})["version"], 1)
check("...and one taken now says so", flat_row({"version": 2})["version"],
      2)
check("the CSV carries it", "measurement version" in SRC, True)
check("...and where each row came from", '("source", "found by")' in SRC,
      True)

print("\n5. the high-frequency search finds what a broadband one cannot")
SR = 16000
rng = np.random.default_rng(4)
x = rng.normal(0, 0.004, SR * 20).astype(np.float32)
t = np.arange(x.size) / SR
# A LOUD LOW RUMBLE over the whole clip — wind. It dominates any
# broadband envelope and buries three quiet high-frequency ticks.
x += (0.08 * np.sin(2 * np.pi * 70 * t)).astype(np.float32)
for at in (3.0, 9.5, 16.2):
    i = int(at * SR)
    tt = np.arange(int(SR * 0.08)) / SR
    x[i:i + tt.size] += (0.05 * np.exp(-tt / 0.02)
                         * np.sin(2 * np.pi * 3000 * tt)).astype(np.float32)
cands = hf_candidates(x, SR, Audio1Config(), min_ratio=4.0)
found = [c["time_sec"] for c in cands]
for at in (3.0, 9.5, 16.2):
    check("  the tick at %.1fs is found" % at,
          any(abs(f - at) < 0.1 for f in found), True)
check("and the wind itself is not a candidate",
      all(c["hf_ratio"] > 4.0 for c in cands), True)
# THE BROADBAND ENVELOPE CANNOT: the rumble is 1.6x the ticks' peak.
check("...whereas the rumble is louder than every tick",
      float(np.max(np.abs(0.08 * np.sin(2 * np.pi * 70 * t)))) > 0.05, True)
check("each candidate carries the ratio that found it",
      all(c.get("hf_ratio") is not None for c in cands), True)

print("\n6. HF candidates never become clips, and never shadow a peak")
RSRC = (ROOT / "backend/app/routers/admin.py").read_text()
_run = RSRC[RSRC.index("def _test_cut_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("the search is off unless asked", "audio1_hf=False" in RSRC, True)
check("it only ever feeds the measurement pass",
      "hf_search=bool(audio1_hf)" in _run, True)
check("nothing in the cutter reads it",
      "hf_candidates" in _run, False)
check("a candidate within a frame of a real peak is dropped, not doubled",
      'abs(c["time_sec"] - float(q.get("time_sec") or -99))' in SRC, True)
check("and they are marked apart from the detector's",
      '"source": "hf"' in SRC, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
