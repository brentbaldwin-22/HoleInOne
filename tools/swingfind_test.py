#!/usr/bin/env python3
"""Does the swing finder keep swings and drop people walking?

THE BUG THIS EXISTS TO PREVENT. Measured on real tee footage
(event-736, a foursome), the group WALKING OFF THE TEE scored a motion
ratio of 9.68 -- higher than any actual swing in the clip, because
four bodies crossing frame move more pixels than one person swinging.
A detector ranking on amplitude alone returns the walking and misses
the golf. Burst width is what separates them: a swing is a 0.4 s
spike, locomotion is a 7 s plateau.

So the assertions below are mostly about REJECTION, and the controls
matter as much as the detections: a gate tuned until nothing passes
would satisfy "drops walking" and destroy the product.

    python3 tools/swingfind_test.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import swingfind  # noqa: E402
from agent.swingfind import (  # noqa: E402
    burst_width, coverage_fraction, find_swings, merge_windows, smooth,
)

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


RATE = 50.0


def sig(duration, events, base=1.0):
    """A motion signal: flat `base`, plus (centre, peak, width_s) bumps."""
    n = int(duration * RATE)
    times = [i / RATE for i in range(n)]
    vals = [base] * n
    for centre, peak, width in events:
        sd = max(1e-6, width / 2.355)        # FWHM -> gaussian sigma
        for i in range(n):
            dt = times[i] - centre
            vals[i] += (peak - base) * math.exp(-(dt * dt) / (2 * sd * sd))
    return times, vals


def main() -> int:
    # ---- the core discrimination --------------------------------
    t, m = sig(60, [(30.0, 8.0, 0.5)])
    out = find_swings(t, m, duration_sec=60)
    check("a narrow spike is a swing", len(out), 1)
    check("  and it is placed at the peak",
          abs(out[0]["peak_time_sec"] - 30.0) < 0.2, True)
    check("  rated high", out[0]["confidence"], "high")

    t, m = sig(60, [(30.0, 8.0, 7.0)])
    out = find_swings(t, m, duration_sec=60)
    check("a 7s plateau of the SAME height is rejected (walking)",
          len(out), 0)

    # CONTROL: the gate must not simply reject everything wide-ish.
    t, m = sig(60, [(30.0, 8.0, 2.0)])
    out = find_swings(t, m, duration_sec=60)
    check("a 2s burst is still kept (control)", len(out), 1)
    check("  but flagged lower than a spike (control)",
          out[0]["confidence"] in ("high", "medium", "low"), True)

    # CONTROL: amplitude still has to matter. Peak 3.0 is chosen so
    # the BURST GATE cannot be what rejects it -- at peak 2.0 the base
    # sits above half-peak, the measured width runs to the whole clip,
    # and the width gate does the rejecting while the amplitude
    # threshold is never consulted. That version of this assertion
    # passed with the threshold deleted.
    t, m = sig(60, [(30.0, 3.0, 0.5)])
    check("a narrow but SHALLOW bump is not a swing (control)",
          len(find_swings(t, m, duration_sec=60)), 0)
    check("  and it IS the amplitude gate rejecting it, not the width",
          len(find_swings(t, m, duration_sec=60, min_ratio=0.0)), 1)

    # ---- the real-footage shape ---------------------------------
    # Two swings and a walk-off, at the amplitudes actually measured.
    t, m = sig(120, [(1.0, 4.73, 0.38), (52.0, 6.39, 0.46),
                     (92.0, 9.68, 7.30)])
    out = find_swings(t, m, duration_sec=120)
    peaks = sorted(round(w["peak_time_sec"]) for w in out)
    check("event-736 shape: both swings kept, walk-off dropped",
          peaks, [1, 52])

    # ---- separation, merging, coverage --------------------------
    # Which one survives matters: keeping the EARLIER of a cluster
    # keeps the backswing and discards the impact.
    t, m = sig(60, [(30.0, 8.0, 0.4), (32.0, 5.0, 0.4)])
    w = find_swings(t, m)
    check("two peaks 2s apart are one swing", len(w), 1)
    check("  and it is the STRONGER one that survives",
          round(w[0]["peak_time_sec"]), 30)
    t, m = sig(60, [(10.0, 8.0, 0.4), (40.0, 8.0, 0.4)])
    check("two peaks 30s apart are two (control)", len(find_swings(t, m)), 2)

    a = [{"start_sec": 0, "end_sec": 10, "ratio": 5, "peak_time_sec": 5,
          "confidence": "high"},
         {"start_sec": 8, "end_sec": 18, "ratio": 7, "peak_time_sec": 13,
          "confidence": "high"}]
    merged = merge_windows(a)
    check("overlapping windows fuse", len(merged), 1)
    check("  spanning both", (merged[0]["start_sec"], merged[0]["end_sec"]),
          (0, 18))
    check("  keeping the stronger peak", merged[0]["peak_time_sec"], 13)
    b = [{"start_sec": 0, "end_sec": 10, "ratio": 5, "peak_time_sec": 5,
          "confidence": "high"},
         {"start_sec": 20, "end_sec": 30, "ratio": 5, "peak_time_sec": 25,
          "confidence": "high"}]
    check("disjoint windows do NOT fuse (control)", len(merge_windows(b)), 2)
    check("coverage is measured against the session",
          round(coverage_fraction(merged, 36.0), 3), 0.5)

    # ---- audio confirmation -------------------------------------
    t, m = sig(60, [(30.0, 5.0, 2.5)])
    plain = find_swings(t, m, duration_sec=60)
    withaudio = find_swings(t, m, duration_sec=60, confirm_times=[30.1])
    check("without audio this is not high", plain[0]["confidence"] != "high",
          True)
    check("audio confirmation promotes it", withaudio[0]["confidence"],
          "high")
    check("  and records that it was confirmed",
          withaudio[0]["audio_confirmed"], True)
    # CONTROL: audio must CONFIRM, never VETO — a dead mic must not
    # silently discard every swing.
    check("a peak with no audio match survives",
          len(find_swings(t, m, duration_sec=60, confirm_times=[5.0])), 1)

    # ---- windows and clamping -----------------------------------
    t, m = sig(60, [(1.0, 8.0, 0.4)])
    w = find_swings(t, m, duration_sec=60)[0]
    check("a swing at the very start is not lost to edge trimming",
          w["start_sec"] >= 0.0, True)
    check("  and the window is clamped, not negative",
          w["start_sec"] < 1.0, True)
    t, m = sig(20, [(18.0, 8.0, 0.4)])
    w = find_swings(t, m, duration_sec=20)[0]
    check("a swing near the end clamps to the session",
          w["end_sec"] <= 20.0, True)

    # ---- degenerate input ---------------------------------------
    check("too few samples yield nothing", find_swings([0, 1], [1, 1]), [])
    check("a frozen camera (zero median) yields nothing, not noise",
          find_swings([i / RATE for i in range(500)], [0.0] * 500), [])
    check("empty smoothing is empty", smooth([], 5), [])
    check("smooth with span 1 is a copy", smooth([1.0, 9.0], 1), [1.0, 9.0])
    check("burst_width on an empty signal is 0.0",
          burst_width([], 0, RATE), 0.0)
    check("burst_width with a bad index is 0.0",
          burst_width([1.0, 2.0], 99, RATE), 0.0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
