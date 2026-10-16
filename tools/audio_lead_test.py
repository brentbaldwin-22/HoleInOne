#!/usr/bin/env python3
"""Is the audio's lead measured over the RECORDING, or over the teardown too?

lead_seconds answers "how long after we started did the first sample
arrive", and the caller ADDS it to the delay that pushes audio later in
the clip. So anything wrongly counted as lead shifts the sound late.

The bug: `elapsed` was read at the bottom of stop(), after terminate()
and a wait() allowed five seconds. That time is spent at the END of the
recording and was being attributed to the FRONT. On the tee -- a Pi
decoding 1080p60 while it shuts an ffmpeg down -- it put three hand
claps a second or two behind the picture.

The clock here is a counter, not the wall, so the numbers are exact and
the test is instant.

    python3 tools/audio_lead_test.py
"""
from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import common  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Clock:
    """A monotonic clock we drive by hand."""

    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += float(dt)


class SlowProc:
    """An ffmpeg that takes `teardown` seconds to die."""

    def __init__(self, clock, teardown):
        self.clock, self.teardown = clock, teardown

    def terminate(self):
        self.clock.advance(self.teardown)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def wav_of(path: Path, seconds: float, rate=16000, channels=1):
    """A WAV whose SIZE says it is `seconds` long -- which is how the
    recorder measures duration (PCM is a flat 2 bytes/sample)."""
    n = int(round(seconds * rate * channels * 2))
    path.write_bytes(b"\0" * 44 + b"\0" * n)
    return path


def lead_for(window: float, captured: float, teardown: float) -> float:
    """lead_seconds after recording `captured`s of audio inside a
    `window`s recording, with ffmpeg taking `teardown`s to exit."""
    clock = Clock()
    real = common.time.monotonic
    common.time.monotonic = clock
    try:
        with tempfile.TemporaryDirectory() as td:
            rec = common.RtspAudioRecorder(
                work_dir=Path(td), device="rtsp://x/y",
                sample_rate=16000, channels=1,
            )
            rec._t0 = clock.t
            rec._wav_path = wav_of(Path(td) / "a.wav", captured)
            rec._proc = SlowProc(clock, teardown)
            rec._err_fh = None
            clock.advance(window)          # the recording itself
            rec.stop()                     # teardown happens inside
            return round(rec.lead_seconds, 3)
    finally:
        common.time.monotonic = real


# THE BUG. A perfect recording -- every second of the window captured --
# with a slow shutdown. The lead must be 0: nothing was missed at the
# front. Before the fix this returned 2.0 and shoved the audio 2s late.
check("a perfect capture with a slow teardown has NO lead",
      lead_for(window=10.0, captured=10.0, teardown=2.0), 0.0)

# CONTROL: a real connect delay must still be measured, or audio that
# genuinely started late plays early.
check("a genuine 1.5s connect delay is still reported",
      lead_for(window=10.0, captured=8.5, teardown=0.0), 1.5)

# ...and both at once: only the front gap counts.
check("connect delay is reported, teardown is not",
      lead_for(window=10.0, captured=8.5, teardown=3.0), 1.5)

# CONTROL: never negative. A tail flush can make the WAV longer than
# the window we measured, and a negative lead would pull audio early.
check("a WAV longer than the window clamps to zero, never negative",
      lead_for(window=10.0, captured=10.4, teardown=0.0), 0.0)

# The configured fudge still rides on top, for a rig that needs it.
def lead_with_extra(extra):
    clock = Clock()
    real = common.time.monotonic
    common.time.monotonic = clock
    try:
        with tempfile.TemporaryDirectory() as td:
            rec = common.RtspAudioRecorder(
                work_dir=Path(td), device="rtsp://x/y",
                sample_rate=16000, channels=1, extra_lead_seconds=extra,
            )
            rec._t0 = clock.t
            rec._wav_path = wav_of(Path(td) / "a.wav", 10.0)
            rec._proc = SlowProc(clock, 2.0)
            rec._err_fh = None
            clock.advance(10.0)
            rec.stop()
            return round(rec.lead_seconds, 3)
    finally:
        common.time.monotonic = real


check("extra_lead_seconds is still added on top", lead_with_extra(0.25), 0.25)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
