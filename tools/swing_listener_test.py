#!/usr/bin/env python3
"""The streaming impact detector, fed a second at a time.

It is a port of the server's detect_swings_from_audio, so the things
worth testing are the ones the restructure could break: that feeding
in chunks gives the same answer as one pass, that the rolling baseline
behaves, that a gap in the feed does not shift every later timestamp,
and that the properties measured on real clips still hold --

    swing      x19 - x30   caught
    talking    x5  - x7    rejected on LEVEL (ratio 8), not on shape
    rumble                 rejected on SHAPE

    python3 tools/swing_listener_test.py
"""
import math
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent.swingaudio import SwingListener  # noqa: E402

SR = 16000
FAILED = []
RNG = random.Random(4)


def pcm(samples):
    return struct.pack("<%dh" % len(samples),
                       *(max(-32768, min(32767, int(s * 32767)))
                         for s in samples))


# AMPLITUDES CALIBRATED TO THE REAL CLIPS, not chosen by eye. Measured
# through this same listener at ratio 1, the values below land on the
# ratios the course actually produces:
#
#   strike 0.18  -> x27     (real swings, after the high-pass: x19-x30)
#   strike 0.062 -> x10     (the quietest real swing on 1258: x9.7)
#   voice  0.028 -> x6      (talking on 1258: x5-x7)
#
# The first draft of this file used 0.045 for the voice, which reaches
# x9.8 -- above the ratio-8 gate -- so "talking must be rejected" was
# asserting something untrue of the real signal as well as of the
# fixture. Calibrate against measurements, not intuition.
A_STRIKE, A_STRIKE_QUIET, A_VOICE = 0.18, 0.062, 0.028


def build(duration, events, floor=0.004):
    """events: (at_sec, amplitude, kind).

    kind:
      strike  sharp broadband burst -- what a club makes up close
      voice   AFTER a 1.5 kHz high-pass speech is consonants, so this
              is sharp too. It is rejected on LEVEL, never on shape:
              measured on 1258, talking attacks in 7.7-13.7 ms against
              swings at 5.3-9.4 ms, which no shape filter can split.
      rumble  wind and engine noise, which is LOW FREQUENCY. This is
              what the high-pass is for, and the fixture has to be
              low-frequency or it tests nothing.
      swell   a slow-rising tone ABOVE the high-pass. The only fixture
              the attack filter itself rejects, and the honest test of
              what that filter is worth.
    """
    n = int(duration * SR)
    buf = [RNG.gauss(0, floor) for _ in range(n)]
    for at, amp, kind in events:
        i = int(at * SR)
        if kind == "strike":
            ln, rise, dec, lo = int(0.05 * SR), 0.002, 0.012, None
        elif kind == "voice":
            ln, rise, dec, lo = int(0.12 * SR), 0.008, 0.050, None
        elif kind == "rumble":
            ln, rise, dec, lo = int(0.60 * SR), 0.250, 0.400, 120.0
        else:                                   # swell
            ln, rise, dec, lo = int(0.80 * SR), 0.300, 0.500, 3000.0
        for k in range(ln):
            if i + k >= n:
                break
            t = k / SR
            env = min(1.0, t / rise) * math.exp(-t / dec)
            carrier = (math.sin(2 * math.pi * lo * t) if lo is not None
                       else RNG.gauss(0, 1))
            buf[i + k] += env * amp * carrier
    return buf


def run(label, samples, want, tol=0.15, ratio=8.0, chunk=1.0, gap_at=None):
    li = SwingListener(SR, min_peak_ratio=ratio)
    hits, t = [], 0.0
    step = int(chunk * SR)
    i = 0
    while i < len(samples):
        blk = samples[i:i + step]
        if gap_at is not None and abs(t - gap_at) < 0.01:
            # a hole in the ring: skip a second of audio AND of clock
            i += step
            t += chunk
            continue
        hits += li.feed(pcm(blk), t)
        i += step
        t += chunk
    got = sorted(round(h["at"], 2) for h in hits)
    ok = len(got) == len(want) and all(
        any(abs(g - w) <= tol for g in got) for w in want)
    print("  %-46s %-26s %s" % (label, str(got),
                                "ok" if ok else "FAIL (want ~%s)" % want))
    if not ok:
        FAILED.append(label)
    return hits


print("each filter rejects the thing it is actually for:")
run("a swing", build(20, [(10.0, A_STRIKE, "strike")]), [10.0])
run("talking alone — rejected on LEVEL at ratio 8",
    build(20, [(6.0, A_VOICE, "voice")]), [])
run("...and the same talking IS found at ratio 4",
    build(20, [(6.0, A_VOICE, "voice")]), [6.0], ratio=4.0)
run("swing + talking — only the swing",
    build(20, [(6.0, A_VOICE, "voice"), (12.0, A_STRIKE, "strike")]), [12.0])
run("low-frequency rumble — removed by the HIGH-PASS",
    build(20, [(6.0, 0.40, "rumble")]), [], ratio=4.0)
run("a slow swell above the high-pass — rejected on SHAPE",
    build(20, [(6.0, 0.40, "swell")]), [], ratio=4.0)

print()
print("two swings, and the 6s separation:")
run("12s apart", build(30, [(8.0, A_STRIKE, "strike"),
                            (20.0, A_STRIKE, "strike")]), [8.0, 20.0])
run("2s apart — one survives",
    build(20, [(8.0, A_STRIKE, "strike"), (10.0, A_STRIKE, "strike")]), [8.0])

print()
print("chunking must not change the answer:")
base = build(24, [(9.0, A_STRIKE, "strike"), (17.0, A_STRIKE_QUIET, "strike")])
a = run("fed 1s at a time", base, [9.0, 17.0], chunk=1.0)
b = run("fed 0.5s at a time", base, [9.0, 17.0], chunk=0.5)
c = run("fed 2s at a time", base, [9.0, 17.0], chunk=2.0)
for lbl, x in (("0.5s", b), ("2s", c)):
    if a and x:
        d = abs(sorted(h["at"] for h in a)[0] - sorted(h["at"] for h in x)[0])
        ok = d < 0.05
        print("  %-46s %-26s %s" % ("...and agrees with 1s to <50ms",
                                    "%s: %.3fs apart" % (lbl, d),
                                    "ok" if ok else "FAIL"))
        if not ok:
            FAILED.append("chunk agreement %s" % lbl)

print()
print("a hole in the ring must not shift later timestamps:")
run("1s dropped at 5s, strike still at 12s",
    build(20, [(12.0, A_STRIKE, "strike")]), [12.0], gap_at=5.0)

print()
print("the quiet-strike rescue (the attack fix) holds here too:")
run("a x10 strike, not just a x30 one",
    build(20, [(10.0, A_STRIKE_QUIET, "strike")]), [10.0])

print()
print("envelope_max must answer in FILTERED units, not raw ones:")
# The probe reported raw peaks beside a filtered threshold, so a mower
# looked like a near-miss. This is the assertion that keeps the two
# apart: a rumble is LOUDER than a quiet strike in raw samples and far
# quieter after the high-pass.
_li = SwingListener(SR, baseline_sec=30.0)
_sig = build(20, [(5.0, 0.30, "rumble"), (12.0, A_STRIKE_QUIET, "strike")])
_raw, _env = {}, {}
for _k in range(20):
    _chunk = _sig[_k * SR:(_k + 1) * SR]
    _li.feed(pcm(_chunk), float(_k))
    _raw[_k] = max(abs(v) for v in _chunk)
    _env[_k] = _li.envelope_max(float(_k), float(_k) + 1.0)
_med = _li.median
for _label, _cond in (
    ("the rumble second is the loudest RAW second",
     _raw[5] > _raw[12]),
    ("...and is quieter than the strike once FILTERED",
     _env[5] < _env[12]),
    ("...and scores under the x8 gate",
     _med > 0 and _env[5] / _med < 8.0),
    ("the strike second scores over it",
     _med > 0 and _env[12] / _med >= 8.0),
    ("a quiet second sits near the floor",
     _med > 0 and _env[2] / _med < 4.0),
    ("the window bounds are honoured (asked after all 20s is in)",
     _li.envelope_max(2.0, 3.0) < _env[12] / 4.0),
    ("an empty window reports nothing",
     _li.envelope_max(1e9, 1e9 + 1.0) == 0.0),
):
    print("  %-52s %s" % (_label, "ok" if _cond else "FAIL"))
    if not _cond:
        FAILED.append(_label)
print("    rumble raw %.3f env x%5.2f | strike raw %.3f env x%5.2f"
      % (_raw[5], _env[5] / _med if _med else 0,
         _raw[12], _env[12] / _med if _med else 0))

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
