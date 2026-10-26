"""A rolling window of the camera's audio, on disk, next to the video.

WHY A SECOND RING. The video ring (copycap) can carry the camera's
audio track -- `-c:a copy` into each segment -- and on this hardware it
does not work: the Hanwha advertises an AAC PID on the combined
session and sends no frames, so every segment probes as
`aac, 0 channels, 0 Hz` and the join drops the stream. Measured on the
tee rig 26 Oct, with a microphone that was by then WORKING: an
audio-only session on the same URL returns real sound, and the
combined one returns an empty PID. That is why `in_segments` is off,
and the earlier note blaming the concat demuxer was testing the wrong
thing -- five one-second segments cut from a stream that does have
audio join perfectly well.

So: a second ffmpeg, `-allowed_media_types audio`, which is the exact
invocation already producing sound in every clip today. A continuous
version of something known to work beats repairing something that
does not.

WHAT THIS BUYS BEYOND THE OBVIOUS. The per-clip recorder has to be
started at the trigger, which means it misses the pre-roll (the silent
head on every clip) and has to have its RTSP connect delay measured
and padded -- the source of two sync bugs this month, in opposite
directions. A ring is always running: there is no connect to measure,
the pre-roll has sound, and a moment is addressed by wall clock in
exactly the way a video segment is. Alignment becomes a filename
lookup instead of arithmetic.

CHEAP. 16 kHz mono PCM is 32 kB/s, so a 185-second window is ~6 MB
against the video ring's ~1 GB. Nothing here decodes.

RAW PCM, NOT WAV. Each segment is headerless s16le, so a span is the
concatenation of its files and nothing has to parse or repair a length
field that a killed ffmpeg never wrote.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("golfreelz_agent.audiocap")

# Same shape as copycap's, deliberately: both rings name a segment by
# the wall clock it began at, so "the audio under this video" is a
# filename comparison.
_NAME_GLOB = "aud-*.pcm"
_NAME_FMT = "aud-%Y%m%d-%H%M%S.pcm"
_NAME_RE = re.compile(r"^aud-(\d{8}-\d{6})\.pcm$")

_STALL_FACTOR = 6.0
_STALL_FLOOR = 10.0


def _scrub(text: str) -> str:
    """Any rtsp://user:secret@host in this text, with the secret gone."""
    return re.sub(r"(rtsp[s]?://[^:/@\s]+):[^@/\s]*@", r"\1:***@", text)


def _seg_start(path: Path) -> Optional[float]:
    """Epoch seconds this segment began, from its own name."""
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    # -strftime writes LOCAL time, matching copycap and the agent's own
    # time.time(). The two rings MUST agree about this or a span lines
    # up against the wrong video.
    return dt.replace(tzinfo=None).timestamp()


class AudioRing:
    """A rolling window of raw PCM, written by one long-lived ffmpeg."""

    def __init__(
        self,
        source: str,
        work_dir: Path | str,
        *,
        segment_seconds: float = 1.0,
        window_seconds: float = 90.0,
        sample_rate: int = 16000,
        channels: int = 1,
        rtsp_transport: str = "tcp",
        ffmpeg: str = "ffmpeg",
        input_args: Optional[list[str]] = None,
    ) -> None:
        self.source = source
        self.dir = Path(work_dir)
        self.segment_seconds = max(0.25, float(segment_seconds))
        self.window_seconds = max(10.0, float(window_seconds))
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.rtsp_transport = rtsp_transport
        self.ffmpeg = ffmpeg
        self._input_args = input_args
        self._proc: Optional[subprocess.Popen] = None
        self._err_fh = None
        self._last_seen_name: Optional[str] = None
        self._last_seen_at = 0.0
        self._restarts = 0

    # ---- bytes <-> seconds -------------------------------------------

    @property
    def bytes_per_sec(self) -> int:
        return self.sample_rate * self.channels * 2

    # ---- process -----------------------------------------------------

    def _cmd(self) -> list[str]:
        if self._input_args is not None:
            inp = [*self._input_args, "-i", self.source]
        else:
            inp = [
                "-rtsp_transport", self.rtsp_transport,
                # ONLY THE AUDIO TRACK. This is the difference that
                # makes it work at all: the combined session returns an
                # empty AAC PID on this camera, and asking for audio
                # alone returns real frames.
                "-allowed_media_types", "audio",
                "-i", self.source,
            ]
        return [
            self.ffmpeg, "-nostdin", "-loglevel", "error", "-y",
            *inp,
            "-vn",
            "-ac", str(self.channels),
            "-ar", str(self.sample_rate),
            "-acodec", "pcm_s16le",
            "-f", "segment",
            "-segment_time", f"{self.segment_seconds:g}",
            # Headerless, so a span is a concatenation and a segment
            # cut short by a kill is still readable to its last sample.
            "-segment_format", "s16le",
            "-reset_timestamps", "1",
            "-strftime", "1",
            str(self.dir / _NAME_FMT),
        ]

    def start(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        self.dir.mkdir(parents=True, exist_ok=True)
        err_path = self.dir / "audiocap.log"
        try:
            self._err_fh = open(err_path, "ab")
            self._proc = subprocess.Popen(
                self._cmd(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=self._err_fh,
            )
        except FileNotFoundError:
            log.warning("audiocap: ffmpeg not installed — no audio ring")
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("audiocap: could not start (%s)", _scrub(str(exc)))
            return False
        self._last_seen_at = time.monotonic()
        log.info("audio ring: %gs segments, %gs window, %d Hz mono (~%.1f MB)",
                 self.segment_seconds, self.window_seconds, self.sample_rate,
                 self.window_seconds * self.bytes_per_sec / 1e6)
        return True

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p is None or p.poll() is not None:
            return
        try:
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
            p.terminate()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._kill()
        if self._err_fh is not None:
            try:
                self._err_fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._err_fh = None

    # ---- the window ---------------------------------------------------

    def segments(self, include_current: bool = False) -> list[Path]:
        """Finished segments, oldest first.

        The newest file is still being written, so it is left out
        unless asked for: reading it gives a partial second whose end
        is wherever ffmpeg happened to have flushed.
        """
        try:
            segs = sorted((p for p in self.dir.glob(_NAME_GLOB)
                           if _seg_start(p) is not None),
                          key=lambda p: p.name)
        except OSError:
            return []
        if not include_current and segs:
            segs = segs[:-1]
        return segs

    def span(self) -> tuple[Optional[float], Optional[float]]:
        segs = self.segments()
        if not segs:
            return None, None
        return (_seg_start(segs[0]),
                (_seg_start(segs[-1]) or 0.0) + self.segment_seconds)

    def sweep(self) -> int:
        """Delete segments older than the window. Returns how many."""
        cutoff = time.time() - self.window_seconds
        n = 0
        for p in self.segments(include_current=True):
            ts = _seg_start(p)
            if ts is not None and ts + self.segment_seconds < cutoff:
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    def healthy(self) -> bool:
        """Is a new segment still arriving? Restarts the ring if not."""
        segs = self.segments(include_current=True)
        newest = segs[-1].name if segs else None
        now = time.monotonic()
        if newest != self._last_seen_name:
            self._last_seen_name = newest
            self._last_seen_at = now
            return True
        stall = max(_STALL_FLOOR, _STALL_FACTOR * self.segment_seconds)
        if now - self._last_seen_at < stall:
            return True
        self._restarts += 1
        log.warning("audio ring: no new segment for %.0fs — restarting "
                    "(restart %d)", now - self._last_seen_at, self._restarts)
        self._kill()
        self.start()
        self._last_seen_at = now
        return False

    def tick(self) -> None:
        """Call from the agent's loop: sweep, and restart if stalled."""
        self.sweep()
        self.healthy()

    # ---- reading ------------------------------------------------------

    def read(self, start_ts: float, end_ts: float) -> dict:
        """The PCM covering [start_ts, end_ts], rounded out to segments.

        Returns `start` -- when the bytes ACTUALLY begin, which is the
        segment edge at or before what was asked for. A caller lining
        audio up against video must use that and not its own request,
        for the same reason copycap.extract returns one.

        A hole in the ring is reported rather than papered over: the
        samples are what exists, and `missing_seconds` says how much of
        the span is not in them. Silently returning a short buffer
        would shift every sample after the hole earlier in time, which
        is the whole class of bug this ring exists to avoid.
        """
        if end_ts < start_ts:
            start_ts, end_ts = end_ts, start_ts
        chosen = []
        for p in self.segments():
            s = _seg_start(p)
            if s is None:
                continue
            if s + self.segment_seconds > start_ts and s < end_ts:
                chosen.append((s, p))
        if not chosen:
            first, last = self.span()
            return {"ok": False, "why": (
                f"no audio covering {start_ts:.0f}..{end_ts:.0f}; "
                f"ring holds {first}..{last}")}
        actual_start = chosen[0][0]
        actual_end = chosen[-1][0] + self.segment_seconds
        # FILL THE HOLES, DO NOT CLOSE THEM. Concatenating only the
        # segments that exist makes everything after a gap butt up
        # against what came before it, so every sample past the hole
        # lands early by the length of the hole -- which is the exact
        # failure this ring exists to avoid, and the first version of
        # this function merely REPORTED it while doing it. Measured on
        # a fixture whose samples encode their own position: with one
        # second missing, the sample four seconds into the span was the
        # one recorded at seven seconds.
        #
        # Each slot is also padded or truncated to its nominal length.
        # A segment's NAME is what says when it began, so a short one
        # has a gap after it, and honouring the name keeps every later
        # segment at the offset its own name implies.
        step = self.segment_seconds
        bps = self.bytes_per_sec
        seg_bytes = int(round(step * bps)) // 2 * 2
        by_slot = {}
        for st, p in chosen:
            by_slot[int(round((st - actual_start) / step))] = p
        n_slots = max(1, int(round((actual_end - actual_start) / step)))
        buf = bytearray()
        got = 0
        for k in range(n_slots):
            p = by_slot.get(k)
            data = b""
            if p is not None:
                try:
                    data = p.read_bytes()
                    got += 1
                except OSError:
                    # Swept from under us mid-read. Silence for this
                    # slot, counted as missing.
                    data = b""
            if len(data) < seg_bytes:
                data = data + b"\0" * (seg_bytes - len(data))
            elif len(data) > seg_bytes:
                data = data[:seg_bytes]
            buf += data
        expected = n_slots
        return {
            "ok": True,
            "pcm": bytes(buf),
            "start": actual_start,
            "end": actual_end,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "segments": got,
            "missing_seconds": max(0, expected - got) * self.segment_seconds,
        }


def _wav_header(n_bytes: int, sample_rate: int, channels: int) -> bytes:
    import struct
    byte_rate = sample_rate * channels * 2
    return (b"RIFF" + struct.pack("<I", 36 + n_bytes) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                          byte_rate, channels * 2, 16)
            + b"data" + struct.pack("<I", n_bytes))


class RingAudioRecorder:
    """The per-clip audio recorder's interface, served from the ring.

    WHY THIS EXISTS. The recorder it replaces opens its own RTSP
    session at the trigger, and that has three costs that are really
    one cost:

      * A SECOND SESSION TO THE SAME CAMERA. With the ring already
        holding one, the Hanwha took EIGHT SECONDS to serve the second
        -- measured on the tee 26 Oct, against ~0.9s before the ring
        existed, and confirmed by two independent methods agreeing on
        that clip (7.98s watched, 8.38s inferred).
      * A CONNECT DELAY THAT HAS TO BE MEASURED. Every clip's audio is
        then padded by that measurement, and getting it wrong puts
        every strike out of sync. It has been got wrong in both
        directions inside one month.
      * A SILENT HEAD. The ring holds footage from BEFORE the trigger
        and a recorder started AT the trigger has no sound for it, so
        every clip opens with five seconds of correctly-aligned
        nothing.

    A ring has no connect, nothing to measure, and already holds the
    pre-roll. Audio is cut from it by wall clock, the same way the
    video is, so the two line up by construction and the mux delay is
    zero -- not a number anybody has to get right.
    """

    def __init__(self, ring: AudioRing):
        self.ring = ring
        self.enabled = True
        self.lead_seconds = 0.0
        self._session: Optional[str] = None

    # The recorder interface, such as it is.
    def start(self, session_id: str):
        self._session = session_id
        return None

    def stop(self):
        # Nothing to stop and nothing to hand back: the caller asks for
        # a SPAN once it knows what the clip actually covers, which it
        # does not at the moment the recording ends.
        return None

    def extract_span(self, start_ts: float, end_ts: float, out_path):
        """A WAV covering exactly [start_ts, end_ts], or None.

        Exactly: the ring is cut on segment edges, so the span it
        returns begins at or before what was asked for, and the extra
        is trimmed here. A caller muxing this needs NO delay -- that is
        the whole point, and an `audio_delay_seconds` applied on top
        would shift it back out again.
        """
        out_path = Path(out_path)
        r = self.ring.read(start_ts, end_ts)
        if not r.get("ok"):
            log.info("audio: the ring cannot cover this clip (%s)",
                     r.get("why"))
            return None
        bps = self.ring.bytes_per_sec
        pcm = r["pcm"]
        # Byte-align to a whole sample, or every sample after the trim
        # is half of one and half of the next.
        off = int(round((start_ts - r["start"]) * bps)) // 2 * 2
        if off >= 0:
            pcm = pcm[off:]
            pad = 0
        else:
            # The ring starts LATER than the clip -- it had not been
            # running long enough. Pad the front rather than shift the
            # audio earlier, which would misalign everything after it.
            pad = -off
            pcm = b"\0" * pad + pcm
        want = int(round((end_ts - start_ts) * bps)) // 2 * 2
        if len(pcm) > want:
            pcm = pcm[:want]
        elif len(pcm) < want:
            pcm += b"\0" * (want - len(pcm))
        try:
            with open(out_path, "wb") as fh:
                fh.write(_wav_header(len(pcm), self.ring.sample_rate,
                                     self.ring.channels))
                fh.write(pcm)
        except OSError as exc:
            log.warning("audio: could not write %s (%s)", out_path.name, exc)
            return None
        log.info(
            "audio: %.1fs cut from the ring for %.1f..%.1f — no connect, "
            "no pad%s%s",
            len(pcm) / float(bps), start_ts, end_ts,
            ", %.1fs of front padding (the ring was still filling)"
            % (pad / float(bps)) if pad else "",
            ", %.1fs missing" % r["missing_seconds"]
            if r.get("missing_seconds") else "",
        )
        return out_path
