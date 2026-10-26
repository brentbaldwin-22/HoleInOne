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
        buf = bytearray()
        got = 0
        for _, p in chosen:
            try:
                buf += p.read_bytes()
                got += 1
            except OSError:
                # Swept from under us. Keep going and report the hole.
                pass
        expected = max(1, int(round((actual_end - actual_start)
                                    / self.segment_seconds)))
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
