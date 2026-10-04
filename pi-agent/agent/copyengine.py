"""The stream-copy capture engine, as an alternative to the decoder.

SELECTED IN CONFIG, OFF BY DEFAULT. `capture_engine: "copy"` turns it
on; anything else (including absent) leaves the agent on the path it
has always used. Both engines live in the tree at once on purpose —
this one has to be proven on a real course across real weather before
anything is retired, and a rig that misbehaves should be revertible by
editing one line of YAML rather than by rolling back code over a
cellular link that may not hold an SSH session.

WHAT CHANGES. The decode engine turns the camera's H.264 into raw
frames, re-encodes them with mp4v, and then re-encodes a third time for
upload. This one keeps a rolling window of the camera's own encoded
segments (see copycap.SegmentRing) and, when a trigger fires, copies
out the span that covers it. Measured on 22.2s of real 1080p60 footage
from the green camera: 0.42s to copy against 9.14s to re-encode, and
the copy is first-generation.

WHAT DOES NOT CHANGE. Detection. Without a substream configured there
is still only one stream worth decoding, so the capture thread and the
frame buffer go on exactly as before and the trigger logic is untouched
— this engine replaces the RECORDER, not the decision to record. That
is also why it is safe to switch on: if the ring is unhealthy the
engine says so and the runner falls back to the old path for that clip
rather than losing the swing.

THE PRE-ROLL ARRIVES FOR FREE. The decode engine keeps `buffer_seconds`
of raw frames in RAM — about 1.9 GB at 1080p60 — purely so that a
trigger can reach backwards. The ring has already written those seconds
to disk as a few MB of encoded video, so the pre-roll is just an
earlier timestamp to ask for. `buffer_seconds` still governs how far
back we ask, and is still needed by the decode path, so it is left
alone here.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .common import build_audio_recorder, mux_audio_into_video
from .copycap import SegmentRing

log = logging.getLogger("golfreelz_agent.copyengine")

# A clip is extracted only once the segment covering its last moment has
# been closed by ffmpeg, which happens when the NEXT one opens. Waiting
# is normally one segment; this is the bound on how long we will wait
# before taking what the ring has.
_CLOSE_WAIT_MAX = 8.0


def engine_name(cfg: dict) -> str:
    """Which capture engine this config asks for. 'decode' unless told."""
    name = str((cfg.get("camera") or {}).get("engine")
               or cfg.get("capture_engine") or "decode").strip().lower()
    return "copy" if name in ("copy", "stream-copy", "streamcopy") else "decode"


class CopyEngine:
    """Owns the ring and turns a trigger into an uploaded clip."""

    def __init__(self, source: str, work_dir: Path, *,
                 segment_seconds: float = 1.0,
                 window_seconds: float = 90.0) -> None:
        self.ring = SegmentRing(
            source=source,
            work_dir=Path(work_dir) / "ring",
            segment_seconds=segment_seconds,
            window_seconds=window_seconds,
        )
        self.clips_dir = Path(work_dir)
        self._extracted = 0
        self._failed = 0
        self._last: dict = {}

    # ---- construction --------------------------------------------------

    @classmethod
    def from_config(cls, cfg: dict, work_dir: Path) -> Optional["CopyEngine"]:
        """The engine this config asks for, or None to leave the agent
        on the decode path. Returns None rather than raising for every
        reason it cannot run, because a misconfigured experiment must
        not stop a camera recording golf."""
        if engine_name(cfg) != "copy":
            return None
        cam = cfg.get("camera") or {}
        device = str(cam.get("device") or "")
        if not device.startswith(("rtsp://", "rtsps://")):
            log.error(
                "capture_engine: copy needs an rtsp:// camera (this one is "
                "%r) — staying on the decode engine",
                device[:40],
            )
            return None
        import os
        url = device.replace(
            "{password}", os.environ.get("GOLFREELZ_CAM_PASSWORD", ""),
        )
        # The window has to span the pre-roll AND the longest clip we
        # might be asked for, or the front of a long session is swept
        # while its tail is still being recorded.
        preroll = float(cfg.get("buffer_seconds", 5))
        longest = float(cfg.get("max_clip_seconds", 120))
        seg = float(cfg.get("copy_segment_seconds", 1.0))
        window = float(cfg.get("copy_window_seconds", 0)) or (
            preroll + longest + 30.0)
        log.info(
            "capture engine: COPY (%.0fs segments, %.0fs window) — the "
            "camera's own H.264 is written to disk and cut out; nothing "
            "is decoded or re-encoded to make a clip",
            seg, window,
        )
        return cls(url, work_dir, segment_seconds=seg, window_seconds=window)

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.ring.start()

    def stop(self) -> None:
        self.ring.stop()

    def status(self) -> dict:
        st = self.ring.status()
        st.update({
            "engine": "copy",
            "clips_extracted": self._extracted,
            "clips_failed": self._failed,
            "last": dict(self._last),
        })
        return st

    def ready(self) -> bool:
        """Can this engine be trusted with the next swing?

        Asked BEFORE a trigger commits to it, so an unhealthy ring sends
        the runner back to the decode path for that clip instead of
        discovering the problem once the golfer has already hit.
        """
        if not self.ring.healthy():
            return False
        first, last = self.ring.span()
        return first is not None and last is not None and last > first

    # ---- recording -----------------------------------------------------

    def record_and_upload(
        self,
        runner,
        session_id: str,
        should_stop: Callable[[float], Optional[str]],
        *,
        on_recording_ended: Optional[Callable[[], None]] = None,
        fixed_seconds: Optional[float] = None,
        not_before: Optional[float] = None,
        tick: float = 0.2,
    ) -> str:
        """Wait out the swing, then cut it from the ring and upload it.

        `should_stop(now)` is the RUNNER'S policy, not ours — the tee
        ends a clip when nobody has been in the tee box for a while, the
        green when the tee tells it to. This engine only decides when
        there is enough video on disk and where to cut it, so the two
        runners keep their own very different reasons for stopping.

        Returns the same kind of reason string the decode path returns,
        so a caller can treat the two identically.

        `on_recording_ended` fires the moment the window closes, BEFORE
        the clip is cut. On a tee that is where /event-stop goes: the
        paired green is sitting in its own loop waiting to be told, and
        until it is told it records until its length cap. Extraction
        takes between one and seventeen seconds depending on the clip,
        and every one of those is a second the green keeps rolling, so
        this cannot wait until the end.
        """
        t_trigger = float(not_before or time.time())
        preroll = float(getattr(runner, "buffer_seconds", 5.0))
        longest = float(getattr(runner, "max_clip_seconds", 120.0))

        # AUDIO IS NOT IN THE RING. It is deliberately left out of the
        # segments (-an) because the Hanwhas' current profile carries
        # none, and a mic that hangs off the Pi could never be in them
        # anyway. So the engine does exactly what the decode path does:
        # bracket the recording with the same recorder, and fold the WAV
        # in at the end. Switching engines must not quietly produce
        # silent clips -- the server's own swing detector reads this
        # audio (detect_swings_from_audio), so losing it would break
        # something a long way from here.
        audio = None
        try:
            audio = build_audio_recorder(runner.cfg, self.clips_dir)
            audio.start(session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("copy: audio capture did not start (%s) — the clip "
                        "will be silent", exc)
            audio = None

        # NOTHING IS RECORDED HERE. The video is already being written
        # by the ring; this loop only watches for the end of the swing.
        deadline = t_trigger + (float(fixed_seconds) if fixed_seconds
                                else longest)
        reason = "unknown"
        stopping = getattr(runner, "stopping", None)
        while True:
            now = time.time()
            if stopping is not None and stopping.is_set():
                reason = "agent_stopping"
                break
            if now >= deadline:
                reason = ("fixed_seconds" if fixed_seconds
                          else "length_cap")
                break
            if fixed_seconds is None:
                try:
                    why = should_stop(now)
                except Exception as exc:  # noqa: BLE001
                    log.error("copy: stop policy raised (%s) — ending clip",
                              exc)
                    why = "policy_error"
                if why:
                    reason = why
                    break
            time.sleep(tick)

        t_end = time.time()
        t_start = max(0.0, t_trigger - preroll)

        # TELL THE PARTNER FIRST. Best-effort, exactly as the decode
        # path treats it: a green that records a few seconds too much
        # is a far smaller problem than one that stops too early, and a
        # failed call here must not cost us the clip we just recorded.
        if on_recording_ended is not None:
            try:
                on_recording_ended()
            except Exception as exc:  # noqa: BLE001
                log.warning("copy: could not signal the end of session=%s "
                            "(%s) — a paired green will record on until "
                            "its length cap", session_id, exc)

        # A LENGTH CAP IS A FAILURE TO HEAR THE END, not an ending. The
        # clip is as long as the rig is willing to make it, which on a
        # green is 150s and 200MB of cellular upload, so whatever the
        # stop policy was doing for all that time is worth one line.
        if reason == "length_cap":
            log.warning(
                "copy: session=%s ran to its length cap (%.0fs) — the stop "
                "signal never arrived. Stop policy: %s",
                session_id, t_end - t_trigger,
                getattr(should_stop, "stats", "no diagnostics"),
            )

        # WAIT FOR THE LAST SEGMENT TO CLOSE. The newest file is the one
        # ffmpeg is writing into, and the ring deliberately will not hand
        # it over mid-write — so the final second of a swing only becomes
        # available when the segment after it opens.
        waited_until = time.time() + _CLOSE_WAIT_MAX
        while time.time() < waited_until:
            _, last = self.ring.span()
            if last is not None and last >= t_end:
                break
            time.sleep(0.1)

        clip_path = self.clips_dir / f"copy-{session_id[:8]}-{uuid.uuid4().hex[:6]}.mp4"
        up = getattr(runner, "uploader", None)
        if up is not None:
            up.capture_ended()
        res = self.ring.extract(t_start, t_end, clip_path)
        if not res.get("ok"):
            if audio is not None:
                try:
                    audio.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._failed += 1
            self._last = {"ok": False, "why": res.get("why"),
                          "at": time.time()}
            log.error(
                "copy: could not cut session=%s from the ring (%s) — this "
                "swing is lost; check the ring's health",
                session_id, res.get("why"),
            )
            return "copy_extract_failed"

        # The clip really does begin at a segment edge, which is at or
        # before the pre-roll we asked for -- so the audio's lead is
        # measured from what we GOT, not from what we requested. The
        # decode path has to approximate this; here it is known.
        if audio is not None:
            try:
                wav = audio.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("copy: audio capture failed (%s)", exc)
                wav = None
            if wav is not None:
                lead = max(0.0, t_trigger - float(res["start"]))
                try:
                    if mux_audio_into_video(
                        clip_path, wav,
                        audio_delay_seconds=lead + audio.lead_seconds,
                    ):
                        log.info("copy: audio muxed in (delay=%.2fs)", lead)
                        res["bytes"] = clip_path.stat().st_size
                except Exception as exc:  # noqa: BLE001
                    log.warning("copy: muxing audio failed (%s) — uploading "
                                "the silent clip", exc)

        self._extracted += 1
        self._last = {
            "ok": True, "at": time.time(),
            "seconds": res["seconds"], "bytes": res["bytes"],
            "took": res["took"], "segments": res["segments"],
            "missing_seconds": res.get("missing_seconds", 0),
            "span_seconds": res.get("span_seconds"),
            "reason": reason,
        }
        log.info(
            "copy: session=%s cut %.1fs of video (%.1f MB) from %d segments "
            "in %.2fs — asked for %.1fs%s (%s)",
            session_id, res["seconds"], res["bytes"] / 1e6, res["segments"],
            res["took"], (t_end - t_start),
            (f", {res['missing_seconds']}s MISSING from the span"
             if res.get("missing_seconds") else ""),
            reason,
        )

        if up is not None:
            # real_fps is None ON PURPOSE. Re-clocking exists because the
            # decode path stamps a rate it guessed and then fills gaps to
            # match; these frames carry the camera's own timestamps and
            # were never re-timed, so there is nothing to correct.
            # precompressed likewise: this IS the camera's encode.
            up.enqueue(
                session_id, clip_path, res["start"],
                real_fps=None, precompressed=True,
            )
        return reason
