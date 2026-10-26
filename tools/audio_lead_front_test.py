#!/usr/bin/env python3
"""The audio lead must be measured at the FRONT, not inferred at the back.

THE BUG THIS PINS DOWN. `lead_seconds` was `elapsed - duration`: the
window ffmpeg was alive for, minus how much audio came out of it, with
the remainder blamed on the connect. That holds only if nothing is
captured outside the window -- and `duration` is read from the FINISHED
file, so it includes everything ffmpeg flushed out of its RTSP buffer
after the SIGTERM that `elapsed` stops at. Each flushed sample
subtracts straight from the lead; `max(0.0, ...)` then clamps it to
zero, the camera's whole RTSP negotiation goes unaccounted for, the
audio is padded short, and it plays EARLY by about the connect time --
roughly a second on this camera.

It is the mirror of the teardown bug already fixed in this file, which
pushed audio LATE by the shutdown time ("three claps heard a second or
two after they were seen"). Both come from measuring one end and
blaming the other.

The fixture fakes an RTSP recorder whose WAV appears after a known
delay and keeps growing past the stop, which is exactly the shape that
defeated the subtraction.

    python3 tools/audio_lead_front_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent.common import RtspAudioRecorder  # noqa: E402

FAILED = []
SR, CH = 16000, 1
BPS = SR * CH * 2


class FakeProc:
    """Writes a WAV like ffmpeg does: nothing, then a header, then
    samples -- and keeps writing for a moment after terminate(), the
    way a buffer flush does."""

    def __init__(self, wav: Path, connect_sec: float, flush_sec: float):
        self.wav, self.connect, self.flush = wav, connect_sec, flush_sec
        self.t0 = time.monotonic()
        self._stop = None
        self._returncode = None
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        time.sleep(self.connect)
        self.wav.write_bytes(b"\0" * 44)          # header only
        while self._stop is None:
            time.sleep(0.02)
            n = int((time.monotonic() - self.t0 - self.connect) * BPS)
            self.wav.write_bytes(b"\0" * (44 + max(0, n)))
        # the post-SIGTERM flush: more audio, after `elapsed` has stopped
        n = int((self._stop - self.t0 - self.connect + self.flush) * BPS)
        self.wav.write_bytes(b"\0" * (44 + max(0, n)))
        self._returncode = 0

    def poll(self):
        return self._returncode

    def terminate(self):
        self._stop = time.monotonic()

    def wait(self, timeout=None):
        for _ in range(200):
            if self._returncode is not None:
                return 0
            time.sleep(0.01)
        return 0

    def kill(self):
        self._stop = time.monotonic()


def run(label, connect, flush, hold, want_lead, tol=0.12):
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="leadfront-"))
    r = RtspAudioRecorder(work_dir=d, device="rtsp://x/y", sample_rate=SR,
                          channels=CH, enabled=True)
    wav = d / "s.wav"
    r._wav_path = wav
    r._t0 = time.monotonic()
    r._t_first = None
    r._proc = FakeProc(wav, connect, flush)
    import threading
    threading.Thread(target=r._watch_first_sample, args=(wav,),
                     daemon=True).start()
    time.sleep(hold)
    r.stop()
    got = r.lead_seconds
    ok = abs(got - want_lead) <= tol
    print("  %-50s lead %.2fs  (want ~%.2fs)  %s"
          % (label, got, want_lead, "ok" if ok else "FAIL"))
    if not ok:
        FAILED.append(label)


print("a camera that takes 1.0s to start streaming, then flushes 0.8s")
print("after the stop -- the shape that clamped the old estimate to zero:")
run("1.0s connect, 0.8s flush", 1.0, 0.8, 2.2, 1.0)
print()
print("controls:")
run("1.0s connect, no flush", 1.0, 0.0, 2.2, 1.0)
run("instant connect, 0.8s flush", 0.05, 0.8, 2.2, 0.05, tol=0.15)
run("0.5s connect, 0.5s flush", 0.5, 0.5, 2.0, 0.5)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
