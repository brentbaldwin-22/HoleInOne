#!/usr/bin/env python3
"""The audio ring: does a span come back where it says it does?

WHY THIS MATTERS MORE THAN IT LOOKS. The point of the ring is that a
moment is addressed by wall clock, so audio and video line up by
filename rather than by the pad arithmetic that produced two sync bugs
this month in opposite directions. That only holds if `read()` is
honest about WHERE its bytes begin -- a segment edge at or before the
request, never the request itself -- and about holes, because a short
buffer silently shifts every sample after the hole earlier in time.

Runs ffmpeg against a generated tone instead of a camera, so it needs
no rig. The segment writing, the strftime naming, the sweep and the
span lookup are the same code either way.

    python3 tools/audio_ring_test.py
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent.audiocap import AudioRing, _seg_start  # noqa: E402

FAILED = []
SR = 16000


def check(label, got, want):
    ok = got == want
    print("  %-52s %-22s %s" % (label, repr(got),
                               "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


def near(label, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    print("  %-52s %-22s %s" % (label, "%.2f" % (got or -1),
                               "ok" if ok else "FAIL (want ~%.2f)" % want))
    if not ok:
        FAILED.append(label)


if shutil.which("ffmpeg") is None:
    print("ffmpeg not installed — skipping")
    sys.exit(0)

d = Path(tempfile.mkdtemp(prefix="audioring-"))
# A tone generator standing in for the camera. Same ffmpeg, same
# segment muxer, same -strftime naming.
ring = AudioRing(
    "sine=frequency=440:sample_rate=%d" % SR, d,
    segment_seconds=1.0, window_seconds=6.0, sample_rate=SR, channels=1,
    input_args=["-f", "lavfi", "-re"],
)
print("starting the ring against a generated tone...")
if not ring.start():
    print("could not start ffmpeg — skipping")
    sys.exit(0)
try:
    time.sleep(7.0)
    segs = ring.segments()
    print("  %d finished segment(s)" % len(segs))
    if len(segs) < 4:
        print("FAIL: the ring produced almost nothing")
        sys.exit(1)

    print()
    print("segment size matches the sample rate:")
    sz = segs[1].stat().st_size
    near("one second of 16 kHz mono s16le = 32000 bytes", float(sz),
         ring.bytes_per_sec, 2000)

    print()
    print("a span reports where it ACTUALLY starts, not what was asked:")
    s0 = _seg_start(segs[1])
    r = ring.read(s0 + 0.4, s0 + 2.4)     # deliberately mid-segment
    check("read succeeded", r["ok"], True)
    near("start is the segment edge at or before the request",
         r["start"], s0, 0.001)
    ok = r["start"] <= s0 + 0.4
    check("...and never later than the request", ok, True)
    near("bytes match the span it reports",
         len(r["pcm"]) / float(ring.bytes_per_sec),
         r["end"] - r["start"], 0.2)
    check("no hole reported on an intact ring", r["missing_seconds"], 0.0)

    print()
    print("a hole is reported, not papered over:")
    victim = segs[2]
    vstart = _seg_start(victim)
    victim.unlink()
    r2 = ring.read(_seg_start(segs[1]), _seg_start(segs[3]) + 1.0)
    check("still succeeds", r2["ok"], True)
    near("...and says a second is missing", r2["missing_seconds"], 1.0, 0.01)

    print()
    print("the sweep honours the window:")
    ring.window_seconds = 2.0
    n = ring.sweep()
    left = ring.segments(include_current=True)
    check("swept something", n > 0, True)
    check("...and kept the recent ones", len(left) > 0, True)

    print()
    print("a request outside the ring fails loudly:")
    r3 = ring.read(time.time() - 100000, time.time() - 99999)
    check("refused", r3["ok"], False)
    check("...with a reason", bool(r3.get("why")), True)
finally:
    ring.stop()
    shutil.rmtree(d, ignore_errors=True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
