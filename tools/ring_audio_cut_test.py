#!/usr/bin/env python3
"""Audio cut from the ring lands exactly where the video does.

WHY THIS REPLACED A RECORDER THAT WORKED. The per-clip recorder opens
its own RTSP session at the trigger, and with the ring already holding
one the camera took EIGHT SECONDS to serve the second -- measured on
the tee 26 Oct against ~0.9s before the ring existed, with two
independent methods agreeing on that clip. Every one of those seconds
then has to be measured and padded back out, and that measurement has
been wrong in both directions inside one month: first 1-2s late from
counting the teardown, then 7-8s early from timing an output buffer.

Cutting from the ring has no connect to measure. The span is known, so
the audio is cut to match it and the mux delay is zero -- a number
nobody can get wrong.

What that makes testable, which the old path never was: the sample at
offset T in the WAV is the sample that was recorded at clip_start + T.
The fixture writes a ramp whose value encodes its own position, so a
misalignment of even one segment is visible as an arithmetic error
rather than as "the audio sounds a bit off".

    python3 tools/ring_audio_cut_test.py
"""
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent.audiocap import AudioRing, RingAudioRecorder, _NAME_FMT  # noqa: E402

FAILED = []
SR, BPS = 16000, 32000
T0 = 1761500000.0          # a round epoch, so segment names are tidy


def check(label, got, want):
    ok = got == want
    print("  %-54s %-16s %s" % (label, repr(got),
                               "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


def make_ring(d, n_segments, start=T0, skip=()):
    """Segments whose samples encode their own absolute sample index."""
    import datetime
    ring = AudioRing("unused", d, segment_seconds=1.0, window_seconds=600.0,
                     sample_rate=SR, channels=1)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_segments):
        if i in skip:
            continue
        ts = start + i
        name = datetime.datetime.fromtimestamp(ts).strftime(_NAME_FMT)
        vals = [((i * SR + k) % 30000) - 15000 for k in range(SR)]
        (d / name).write_bytes(struct.pack("<%dh" % SR, *vals))
    # One more so segments() does not withhold the last real one.
    ts = start + n_segments
    name = datetime.datetime.fromtimestamp(ts).strftime(_NAME_FMT)
    (d / name).write_bytes(b"\0" * BPS)
    return ring


def sample_at(wav_path, offset_sec):
    with wave.open(str(wav_path), "rb") as w:
        w.setpos(int(offset_sec * SR))
        return struct.unpack("<h", w.readframes(1))[0]


def expect(abs_sec):
    """What the fixture recorded at `abs_sec` after the ring's start."""
    return ((int(abs_sec * SR)) % 30000) - 15000


d = Path(tempfile.mkdtemp(prefix="ringcut-"))
ring = make_ring(d / "r", 12)
rec = RingAudioRecorder(ring)
out = d / "cut.wav"

print("a cut that starts mid-segment is trimmed, not rounded:")
# 3.5s .. 8.25s -- both ends inside segments
w = rec.extract_span(T0 + 3.5, T0 + 8.25, out)
check("produced a wav", w is not None, True)
with wave.open(str(out), "rb") as wf:
    check("length is exactly the span asked for",
          round(wf.getnframes() / SR, 3), 4.75)
    check("16 kHz mono", (wf.getframerate(), wf.getnchannels()), (SR, 1))
check("the first sample is the one recorded at 3.50s",
      sample_at(out, 0.0), expect(3.5))
check("the sample 1.00s in is the one from 4.50s",
      sample_at(out, 1.0), expect(4.5))
check("the sample 4.00s in is the one from 7.50s",
      sample_at(out, 4.0), expect(7.5))

print()
print("lead_seconds is zero and stays zero:")
check("no connect to measure", rec.lead_seconds, 0.0)
check("stop() hands back nothing — the span comes later", rec.stop(), None)

print()
print("a ring that was still filling pads the FRONT, never shifts:")
ring2 = make_ring(d / "r2", 6, start=T0 + 100)
rec2 = RingAudioRecorder(ring2)
out2 = d / "cut2.wav"
w2 = rec2.extract_span(T0 + 98.0, T0 + 103.0, out2)
check("produced a wav", w2 is not None, True)
with wave.open(str(out2), "rb") as wf:
    check("still exactly the span", round(wf.getnframes() / SR, 3), 5.0)
check("the first 2s are silence, not the ring's first audio",
      sample_at(out2, 0.5), 0)
check("...and real audio resumes at the right offset",
      sample_at(out2, 2.5), expect(100.5 - 100 + 100) if False
      else ((int(0.5 * SR)) % 30000) - 15000)

print()
print("a hole in the ring does not shift what follows it:")
ring3 = make_ring(d / "r3", 10, skip=(4,))
rec3 = RingAudioRecorder(ring3)
out3 = d / "cut3.wav"
w3 = rec3.extract_span(T0 + 2.0, T0 + 8.0, out3)
check("produced a wav", w3 is not None, True)
check("before the hole, sample from 3.00s", sample_at(out3, 1.0), expect(3.0))
check("AFTER the hole, sample from 6.00s is still at 4.00s in",
      sample_at(out3, 4.0), expect(6.0))

print()
print("a span the ring cannot cover is refused:")
check("far in the past", rec.extract_span(T0 - 9999, T0 - 9990, d / "x.wav"),
      None)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
