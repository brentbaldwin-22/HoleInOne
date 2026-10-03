"""Record by COPYING the camera's own H.264, instead of re-making it.

THE CASE FOR THIS, in the numbers that drove it. Today's capture path
decodes the camera's H.264 to raw frames, re-encodes them with mp4v,
then re-encodes a third time with ffmpeg for upload. Measured on one
22.2s clip off the green camera:

    stream-copy concat   0.42s   (this module)
    re-encode @2500kbps  9.14s   (what the Pi does now)

Twenty-two times the cost, to produce a SECOND-GENERATION copy of
something the camera's hardware encoder already made well. The agent's
own comment has said so for months:

    "The camera itself delivers a rock-steady 50 fps even while
     throttled — the SOFTWARE ENCODER is the pipeline's ceiling."

So stop encoding. Keep a rolling window of the camera's own encoded
segments on disk, and when a swing happens, concatenate the span that
covers it. No decode, no encode, no generation loss.

WHAT IT COSTS, because it is not free. The clip is whatever bitrate the
camera is sending, so the camera's rate control becomes the upload
bitrate — set the camera's profile to what the link can carry rather
than to its maximum. And a copy cannot cut mid-GOP, so the span is
rounded outward to segment boundaries; `extract` reports the span it
actually produced rather than pretending it hit the one requested.

MPEGTS, NOT MP4, for the ring. A segment is being written when the
power goes or the process is killed, and the two containers fail very
differently. Truncating one segment to half its bytes:

    mpegts   6.35s recovered of 7s   (reads up to the damage)
    mp4      6.00s — whole segment lost, "moov atom not found"

An mp4 segment is only valid once its moov atom is written at the end.
A transport stream is valid from any byte, which is the property that
matters for a file that is live at the moment things go wrong.

Deliberately NOT part of this module: detection. A tee decides when to
record by looking at frames, and those should come from the camera's
SUBSTREAM — a second, small profile the camera emits for free — so that
nothing in the recording path ever decodes the full-resolution stream.
This module only answers "give me the video between these two times".

Runs alongside the existing path, never instead of it. Nothing here is
imported by tee.py or green.py; the engine is selected in config and
defaults to the old one.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("golfreelz_agent.copycap")

# Segment filenames carry their own start time, which is what makes
# "the video between these two times" a filename lookup rather than a
# index to keep in sync. ffmpeg writes them with -strftime.
_NAME_GLOB = "seg-*.ts"
_NAME_FMT = "seg-%Y%m%d-%H%M%S.ts"
_NAME_RE = re.compile(r"^seg-(\d{8}-\d{6})\.ts$")

# How long without a NEW segment before we conclude the stream is gone.
# Generous relative to the segment length: a camera that hiccups for a
# beat is not a camera that has stopped, and tearing down the ring
# costs the whole pre-roll.
_STALL_FACTOR = 4.0
_STALL_FLOOR = 6.0


def _seg_start(path: Path) -> Optional[float]:
    """Epoch seconds this segment began, from its own name."""
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    # -strftime writes LOCAL time, which is what the agent's own
    # time.time() is compared against after the same conversion.
    return dt.replace(tzinfo=None).timestamp()


class SegmentRing:
    """A rolling window of the camera's encoded video, on disk.

    One ffmpeg process copies the stream into one-second transport
    stream segments; a sweeper deletes the ones that have aged out of
    the window. Nothing decodes, so the cost is roughly the cost of
    writing the bytes.
    """

    def __init__(
        self,
        source: str,
        work_dir: Path | str,
        *,
        segment_seconds: float = 1.0,
        window_seconds: float = 90.0,
        rtsp_transport: str = "tcp",
        input_args: Optional[list[str]] = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.source = source
        self.dir = Path(work_dir)
        self.segment_seconds = max(0.5, float(segment_seconds))
        # The window has to hold the pre-roll AND the longest clip, or
        # the oldest part of a swing is swept while it is being written.
        self.window_seconds = max(10.0, float(window_seconds))
        self.rtsp_transport = rtsp_transport
        self._input_args = input_args
        self.ffmpeg = ffmpeg

        self._proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_seen_name: Optional[str] = None
        self._last_seen_at = 0.0
        self._restarts = 0
        self._started_at = 0.0

    # -------- lifecycle ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="copycap-ring",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _cmd(self) -> list[str]:
        if self._input_args is not None:
            inp = list(self._input_args)
        else:
            inp = ["-rtsp_transport", self.rtsp_transport, "-i", self.source]
        return [
            self.ffmpeg, "-nostdin", "-loglevel", "error", "-y",
            *inp,
            # VIDEO ONLY, COPIED. -an because the pipeline has never
            # used audio and a copied audio track is bytes that would
            # ride the cellular link for nothing.
            "-an", "-c:v", "copy",
            "-f", "segment",
            "-segment_time", f"{self.segment_seconds:g}",
            "-segment_format", "mpegts",
            # Each segment restarts at zero so a concat of any subset
            # produces a clip that starts at zero too.
            "-reset_timestamps", "1",
            "-strftime", "1",
            str(self.dir / _NAME_FMT),
        ]

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p is None or p.poll() is not None:
            return
        try:
            p.send_signal(signal.SIGINT)   # lets ffmpeg close the segment
            try:
                p.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
            p.kill()
            p.wait(timeout=3)
        except Exception as exc:  # noqa: BLE001
            log.debug("copycap: killing ffmpeg failed: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    self._cmd(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                log.error("copycap: %s not found — ring cannot run",
                          self.ffmpeg)
                return
            log.info("copycap: ring started (%.0fs segments, %.0fs window)",
                     self.segment_seconds, self.window_seconds)
            self._watch_until_dead()
            if self._stop.is_set():
                break
            self._restarts += 1
            # A stream that drops is normal on a cellular rig; a stream
            # that drops in a tight loop is a config error, and the
            # backoff is what keeps the second case from filling the
            # journal.
            time.sleep(min(30.0, 2.0 * self._restarts))

    def _watch_until_dead(self) -> None:
        """Sweep the window and notice when the stream stops arriving."""
        stall_after = max(_STALL_FLOOR,
                          _STALL_FACTOR * self.segment_seconds)
        while not self._stop.is_set():
            p = self._proc
            if p is None or p.poll() is not None:
                err = b""
                try:
                    if p is not None and p.stderr is not None:
                        err = p.stderr.read() or b""
                except Exception:  # noqa: BLE001
                    pass
                rc = p.returncode if p is not None else None
                msg = err.decode("utf-8", "replace").strip()[:300]
                # EXIT CODE WHEN THERE IS NO MESSAGE. -loglevel error
                # means a clean exit says nothing at all, so "no
                # message" on its own cannot distinguish our own SIGINT
                # from a camera that hung up — and those call for
                # opposite responses. 255/-2 is ffmpeg taking an
                # interrupt; anything else arrived from outside.
                log.warning(
                    "copycap: ffmpeg exited rc=%s after %.0fs holding %d "
                    "segment(s)%s%s",
                    rc, time.time() - self._started_at,
                    len(self.segments(include_current=True)),
                    f": {msg}" if msg else " with no message",
                    ("" if self._stop.is_set() else
                     " — if this repeats, the camera may not allow a second "
                     "concurrent RTSP session (detection already holds one)"),
                )
                return
            self._sweep()
            if (self._last_seen_at
                    and time.time() - self._last_seen_at > stall_after):
                log.warning(
                    "copycap: no new segment in %.0fs — restarting the ring",
                    time.time() - self._last_seen_at,
                )
                self._last_seen_at = 0.0
                self._kill()
                return
            time.sleep(min(1.0, self.segment_seconds / 2.0))

    def _sweep(self) -> None:
        """Drop segments that have aged out; note the newest arrival."""
        segs = self.segments(include_current=True)
        if segs:
            newest = segs[-1]
            if newest.name != self._last_seen_name:
                self._last_seen_name = newest.name
                self._last_seen_at = time.time()
        cutoff = time.time() - self.window_seconds
        for p in segs[:-1]:            # never the one being written
            ts = _seg_start(p)
            if ts is not None and ts < cutoff:
                try:
                    p.unlink()
                except OSError:
                    pass

    # -------- reading --------------------------------------------------

    def segments(self, include_current: bool = False) -> list[Path]:
        """Every segment in the ring, oldest first.

        The newest file is the one ffmpeg is WRITING INTO, so it is held
        back by default: its last bytes are mid-flight and its duration
        is whatever has landed so far.
        """
        try:
            found = sorted(
                (p for p in self.dir.glob(_NAME_GLOB)
                 if _seg_start(p) is not None),
                key=lambda p: p.name,
            )
        except OSError:
            return []
        if include_current or not found:
            return found
        return found[:-1]

    def span(self) -> tuple[Optional[float], Optional[float]]:
        """The wall-clock range the ring can currently answer for."""
        segs = self.segments()
        if not segs:
            return (None, None)
        first = _seg_start(segs[0])
        last = _seg_start(segs[-1])
        if first is None or last is None:
            return (None, None)
        return (first, last + self.segment_seconds)

    def healthy(self) -> bool:
        p = self._proc
        if p is None or p.poll() is not None:
            return False
        if not self._last_seen_at:
            # Give a freshly started ring one window to produce its
            # first segment before calling it sick.
            return time.time() - self._started_at < max(
                _STALL_FLOOR, 4.0 * self.segment_seconds)
        return (time.time() - self._last_seen_at
                <= max(_STALL_FLOOR, _STALL_FACTOR * self.segment_seconds))

    def status(self) -> dict:
        first, last = self.span()
        segs = self.segments()
        try:
            bytes_held = sum(p.stat().st_size for p in segs)
        except OSError:
            bytes_held = 0
        return {
            "healthy": self.healthy(),
            "segments": len(segs),
            "seconds_held": round((last - first), 1) if first and last else 0.0,
            "bytes_held": bytes_held,
            "restarts": self._restarts,
            "segment_seconds": self.segment_seconds,
            "window_seconds": self.window_seconds,
        }

    # -------- extraction -----------------------------------------------

    def extract(self, start_ts: float, end_ts: float,
                out_path: Path | str, timeout: float = 60.0) -> dict:
        """Write the video covering [start_ts, end_ts] to `out_path`.

        ROUNDED OUTWARD, and the result says by how much. A copy cannot
        cut inside a GOP, so the clip begins at the start of the segment
        containing `start_ts` and ends at the end of the one containing
        `end_ts`. With one-second segments that is at most a second of
        extra footage at each end — which for a golf swing is a better
        trade than a re-encode, and the caller gets `start`/`end` back
        so nothing downstream has to assume it got what it asked for.
        """
        out_path = Path(out_path)
        if end_ts < start_ts:
            start_ts, end_ts = end_ts, start_ts
        segs = self.segments()
        if not segs:
            return {"ok": False, "why": "ring is empty"}

        chosen: list[Path] = []
        for p in segs:
            s = _seg_start(p)
            if s is None:
                continue
            e = s + self.segment_seconds
            if e > start_ts and s < end_ts:
                chosen.append(p)
        if not chosen:
            first, last = self.span()
            return {
                "ok": False,
                "why": (f"nothing covering {start_ts:.0f}..{end_ts:.0f}; "
                        f"ring holds {first and first:.0f}..{last and last:.0f}"),
            }

        actual_start = _seg_start(chosen[0]) or start_ts
        actual_end = (_seg_start(chosen[-1]) or end_ts) + self.segment_seconds

        # COPY THE SEGMENTS ASIDE FIRST. The sweeper is deleting from
        # under us on another thread and a concat list that names a file
        # removed mid-read fails the whole clip. They are small and the
        # copy is local.
        staging = out_path.parent / f".{out_path.stem}-parts"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            parts: list[Path] = []
            for i, p in enumerate(chosen):
                dst = staging / f"{i:04d}.ts"
                # A HARD LINK, NOT A COPY. The sweeper is unlinking from
                # under us on another thread, and a link pins the inode
                # just as well while costing nothing -- the first
                # version copied the bytes, which on a long clip meant
                # moving 200MB through an SD card twice and turned a
                # 0.4s extract into 17s. Falls back to copying if the
                # staging directory ever lands on another filesystem.
                try:
                    os.link(p, dst)
                except OSError:
                    try:
                        shutil.copy2(p, dst)
                    except OSError as exc:
                        log.debug("copycap: segment vanished mid-extract (%s)",
                                  exc)
                        continue
                parts.append(dst)
            if not parts:
                return {"ok": False, "why": "segments vanished before copy"}

            listing = staging / "list.txt"
            listing.write_text(
                "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8",
            )
            cmd = [
                self.ffmpeg, "-nostdin", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", "-movflags", "+faststart", str(out_path),
            ]
            t0 = time.time()
            r = subprocess.run(
                cmd, cwd=str(staging), capture_output=True, timeout=timeout,
            )
            took = time.time() - t0
            if r.returncode != 0 or not out_path.exists():
                return {
                    "ok": False,
                    "why": (r.stderr or b"").decode("utf-8", "replace")[:300],
                }
            # THE SPAN IS NOT THE DURATION when the ring dropped
            # segments. A restart leaves a hole, and reporting the
            # wall-clock distance between the first and last segment
            # claimed 156s for a clip that held 144s of video -- the
            # 12 missing seconds being exactly what two ring restarts
            # had cost. Count what is actually there and say when some
            # of it is missing, because a clip with holes in it is a
            # thing an operator needs told, not left to discover.
            span = actual_end - actual_start
            expected = max(1, int(round(span / self.segment_seconds)))
            missing = max(0, expected - len(parts))
            if missing:
                log.warning(
                    "copycap: %d of %d second(s) are missing from this clip "
                    "-- the ring was not recording for part of the span "
                    "(restarts so far: %d)",
                    missing, expected, self._restarts,
                )
            return {
                "ok": True,
                "path": str(out_path),
                "bytes": out_path.stat().st_size,
                "segments": len(parts),
                "start": actual_start,
                "end": actual_end,
                # What the clip actually holds.
                "seconds": round(len(parts) * self.segment_seconds, 3),
                # The wall-clock window it was cut from, and the gap
                # between the two.
                "span_seconds": round(span, 3),
                "missing_seconds": missing,
                # What the caller asked for, so a log can show the slop.
                "requested_start": start_ts,
                "requested_end": end_ts,
                "took": round(took, 3),
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "why": f"concat timed out after {timeout}s"}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
