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

from .common import (
    audio_in_segments,
    build_audio_recorder,
    mux_audio_into_video,
)
from . import tee_roi
from .copycap import SegmentRing
from .swingfind import coverage_fraction, find_swings, merge_windows

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


def copy_configured(cfg: dict) -> bool:
    """Will the copy engine actually run on this config?

    Both gates, not just the name: `capture_engine: copy` on a camera
    that is not RTSP leaves the agent on the decode path. The tee has to
    know this BEFORE it sizes its pre-roll ring, which is long before
    from_config() is called, and a second hand-rolled version of the
    same test would drift from this one the first time either moved.
    """
    if engine_name(cfg) != "copy":
        return False
    device = str((cfg.get("camera") or {}).get("device") or "")
    return device.startswith(("rtsp://", "rtsps://"))


# WHAT THE SHORTER DEFAULT COSTS, stated plainly: if the segment ring is
# unhealthy at the moment of a swing, that clip opens 2s before the
# trigger instead of 5s. That is the downswing and impact but probably
# not the address. It applies only on the degraded path, which is
# already the "something is wrong" case -- and 1.9GB of RAM to hold a
# pre-roll nothing reads is the wrong price for it.
DEFAULT_COPY_FALLBACK_PREROLL = 2.0


def preroll_seconds(cfg: dict) -> float:
    """Seconds of DECODED frames to keep in RAM.

    `buffer_seconds` when the decode path is doing the recording, and
    the smaller fallback depth when the copy engine is. Pure, so the
    rule can be tested without standing up an agent.

    IT LIVES HERE SO BOTH ROLES GET IT. It was written in tee.py, which
    is where the copy engine was first switched on, and the green went
    on allocating `buffer_seconds * fps` frames whatever engine it ran
    -- 1.9GB at 1080p60 for a ring only the decode fallback ever reads,
    on a Pi that is also holding the segment ring on disk. The comment
    in common.py describing the startup estimate said as much, as a
    known asymmetry, which is how it survived: it was documented rather
    than fixed.

    The green has LESS use for the pre-roll than the tee, not more. It
    records a landing on a trigger the tee already fired, so there is
    no backswing of its own to catch.
    """
    if not copy_configured(cfg):
        return float(cfg.get("buffer_seconds", 5))
    return max(0.5, float(
        cfg.get("copy_fallback_preroll_seconds",
                DEFAULT_COPY_FALLBACK_PREROLL)))


# Below this, cutting is not worth it. Four swings in two minutes
# makes the windows nearly tile the session, and then several uploads
# with their overlaps cost MORE than the one clip -- plus every extra
# upload is another chance for the modem to drop mid-transfer. Measured
# on event-736 the saving was 74% and on event-859 51%; at 70% coverage
# there is nothing left to win.
_CUT_WORTH_IT_BELOW = 0.70

# Sub-cuts are stream copies of a stream copy: no re-encode, so the
# clip stays first-generation and a cut costs well under a second.
_SUBCUT_TIMEOUT = 120


def _subcut(src: Path, dst: Path, start: float, end: float) -> bool:
    """Copy [start, end) out of src. No re-encode; never raises."""
    import subprocess
    dur = max(0.1, end - start)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        # -ss BEFORE -i seeks the container, which is fast but lands
        # on a keyframe. That is exactly what we want: the camera is
        # on GOV 60 at 60 fps, so keyframes are one per second and the
        # cut lands within a second of the asked-for point, on a clean
        # frame, with no re-encode to make it exact.
        "-ss", f"{max(0.0, start):.3f}", "-i", str(src),
        "-t", f"{dur:.3f}", "-c", "copy", "-movflags", "+faststart",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_SUBCUT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("copy: sub-cut failed to run (%s)", exc)
        return False
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        log.warning("copy: sub-cut %.1f-%.1fs failed (rc=%s) %s",
                    start, end, r.returncode, (r.stderr or "")[:160])
        dst.unlink(missing_ok=True)
        return False
    return True


class CopyEngine:
    """Owns the ring and turns a trigger into an uploaded clip."""

    def __init__(self, source: str, work_dir: Path, *,
                 segment_seconds: float = 1.0,
                 window_seconds: float = 90.0,
                 min_free_mb: float = 1024.0,
                 window_max_mb: float = 0.0,
                 keep_audio: bool = False) -> None:
        self.ring = SegmentRing(
            source=source,
            work_dir=Path(work_dir) / "ring",
            segment_seconds=segment_seconds,
            window_seconds=window_seconds,
            keep_audio=keep_audio,
            min_free_mb=min_free_mb,
            window_max_mb=window_max_mb,
        )
        # Remembered so record_and_upload does not have to re-read the
        # config to know whether it still owes this clip a WAV.
        self.keep_audio = bool(keep_audio)
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
        if not copy_configured(cfg):
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
        # RETENTION IS BOUNDED BY DISK AS WELL AS BY TIME. The window
        # is seconds; the segments are whatever bitrate the camera is
        # sending. An hour is 1.8 GB at 4000 kbps and 7.2 GB at 16000,
        # and the upload spool shares this filesystem. So a free-space
        # floor is always on, and a byte cap is available for a rig
        # whose card is small.
        min_free = float(cfg.get("copy_min_free_mb", 1024))
        max_mb = float(cfg.get("copy_window_max_mb", 0))
        log.info(
            "capture engine: COPY (%.0fs segments, %.0fs window, keeping "
            "%.0f MB free%s) — the camera's own H.264 is written to disk "
            "and cut out; nothing is decoded or re-encoded to make a clip",
            seg, window, min_free,
            (", %.0f MB cap" % max_mb) if max_mb else "",
        )
        _keep_audio = audio_in_segments(cfg)
        if _keep_audio:
            log.info(
                "capture engine: the camera's audio rides in the segments, "
                "so the pre-roll has sound and no WAV is muxed in after",
            )
        return cls(url, work_dir, segment_seconds=seg, window_seconds=window,
                   min_free_mb=min_free, window_max_mb=max_mb,
                   keep_audio=_keep_audio)

    def _audio_recorder_for(self, runner):
        """The WAV recorder this clip needs, or None.

        THE LOCK, and it is a method so a test can ask the real code
        rather than restate the condition. The first version of this
        was an `if` inline in record_and_upload, and the test that
        "covered" it had copied the condition into itself -- so
        deleting the guard entirely changed nothing the test could see.

        None when the segments already carry the camera's audio: there
        is nothing a second recorder could add, and plenty it would
        destroy. mux_audio_into_video maps 0:v:0 and 1:a:0, so it would
        discard the camera's track and substitute its own WAV --
        silently, with the worse of the two winning.
        """
        if self.keep_audio:
            return None
        # PREFER THE RING, when one is running. A second RTSP session to
        # the same camera cost eight seconds to establish on the tee --
        # against ~0.9s before the ring existed -- and every one of
        # those seconds then has to be measured and padded back out.
        # The ring has already got the audio, including the pre-roll
        # the separate recorder can never have, so there is nothing a
        # second session could add.
        ring = getattr(getattr(runner, "_audio_trigger", None), "ring", None)
        if ring is not None:
            from .audiocap import RingAudioRecorder
            return RingAudioRecorder(ring)
        return build_audio_recorder(runner.cfg, self.clips_dir)

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
            audio = self._audio_recorder_for(runner)
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
            # CUT FROM THE RING INSTEAD, when the recorder can. The
            # span is known exactly here -- it is what the extract
            # actually gave us -- so the audio is cut to match and
            # needs NO delay. That is the point: a zero nobody has to
            # get right, rather than a measurement that has been wrong
            # in both directions inside one month.
            _span = getattr(audio, "extract_span", None)
            if _span is not None:
                _out = clip_path.with_suffix(".ring.wav")
                try:
                    wav = _span(float(res["start"]), float(res["end"]), _out)
                except Exception as exc:  # noqa: BLE001
                    log.warning("copy: ring audio failed (%s)", exc)
                    wav = None
                if wav is not None:
                    try:
                        if mux_audio_into_video(clip_path, wav,
                                                audio_delay_seconds=0.0):
                            log.info("copy: audio cut from the ring "
                                     "(delay=0, aligned by wall clock)")
                            res["bytes"] = clip_path.stat().st_size
                    except Exception as exc:  # noqa: BLE001
                        log.warning("copy: muxing ring audio failed (%s) — "
                                    "uploading the silent clip", exc)
                    finally:
                        _out.unlink(missing_ok=True)
                wav = None
            if wav is not None:
                lead = max(0.0, t_trigger - float(res["start"]))
                try:
                    if mux_audio_into_video(
                        clip_path, wav,
                        audio_delay_seconds=lead + audio.lead_seconds,
                    ):
                        # The applied delay is lead + the RTSP connect
                        # time; logging lead alone understated it by
                        # exactly the amount you'd be hunting for when
                        # a clap lands off its frame.
                        log.info(
                            "copy: audio muxed in (delay=%.2fs = preroll "
                            "%.2fs + connect %.2fs)",
                            lead + audio.lead_seconds, lead,
                            audio.lead_seconds,
                        )
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
            sent = self._enqueue_swings(
                runner, up, session_id, clip_path, res, t_trigger)
            if not sent:
                up.enqueue(
                    session_id, clip_path, res["start"],
                    real_fps=None, precompressed=True,
                )
        return reason

    # ---- audio-triggered cuts -------------------------------------------

    def cut_at_impact(self, runner, impact_ts: float, *,
                      before_sec: float, after_sec: float,
                      clock_offset_sec=None, detail=None) -> Optional[str]:
        """Cut a swing the microphone already heard. Session id, or None.

        BACKWARDS, WHICH IS THE POINT. A dwell says "somebody is here,
        start recording"; a crack says "the ball has already gone".
        Nothing can be started in time for that, so both rings are read
        backwards instead -- which is what a ring is for, and why this
        is a cut rather than a recording.

        The event is registered WITH the impact instant, so the paired
        green does the same thing to its own ring rather than starting
        a recorder it is seconds too late to start.

        Returns None on any failure, having uploaded nothing. The dwell
        is still running and still records; this adds clips, and a
        failure here costs a swing nothing.
        """
        t0 = float(impact_ts) - float(before_sec)
        t1 = float(impact_ts) + float(after_sec)
        first, last = self.ring.span()
        if first is None or t0 < first or t1 > last:
            log.info("copy: impact at %.1f needs %.1f..%.1f and the ring "
                     "holds %s..%s — not cutting", impact_ts, t0, t1,
                     first, last)
            return None

        session_id = str(uuid.uuid4())
        clip_path = self.clips_dir / f"{session_id}.mp4"
        res = self.ring.extract(t0, t1, clip_path)
        if not res.get("ok"):
            log.warning("copy: could not cut the impact at %.1f (%s)",
                        impact_ts, res.get("why"))
            return None

        # Audio for exactly the span the video actually covers. From
        # the ring, so there is no connect to measure and no pad to get
        # wrong -- see RingAudioRecorder.
        ring = getattr(getattr(runner, "_audio_trigger", None), "ring", None)
        if ring is not None and not self.keep_audio:
            from .audiocap import RingAudioRecorder
            _wav = clip_path.with_suffix(".ring.wav")
            try:
                if RingAudioRecorder(ring).extract_span(
                        float(res["start"]), float(res["end"]), _wav):
                    mux_audio_into_video(clip_path, _wav,
                                         audio_delay_seconds=0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("copy: ring audio failed (%s) — silent clip", exc)
            finally:
                _wav.unlink(missing_ok=True)

        _verdict = None
        _vconf = _vreason = None
        _t = getattr(runner, "_audio_trigger", None)
        if _t is not None and getattr(_t, "verify", False):
            try:
                from . import swingheat
                _crop = swingheat.zone_crop(
                    getattr(runner, "roi", None), tee_roi.boxes)
                _img = swingheat.build(
                    clip_path, float(impact_ts) - float(res["start"]),
                    before_sec=_t.verify_window_sec,
                    after_sec=_t.verify_window_sec,
                    crop=_crop,
                )
                if _img is not None:
                    _v = runner.client.verify_swing(_img)
                    _verdict = _v.get("is_swing")
                    _vconf, _vreason = _v.get("confidence"), _v.get("reason")
                    log.info("copy: swing check on %s — %s (%s) %s",
                             session_id,
                             {True: "SWING", False: "not a swing"}.get(
                                 _verdict, "could not tell"),
                             _vconf or "-", _vreason or "")
                    _img.unlink(missing_ok=True)
                else:
                    log.info("copy: no composite for %s — uploading unjudged",
                             session_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("copy: swing check failed (%s) — uploading", exc)
            if _verdict is False and _t.verify_drops:
                # SPOOLED, NOT DELETED, while this is young. A verdict
                # that is wrong costs a swing, and the cheapest
                # insurance against finding that out too late is the
                # clip still being on the card.
                _kept = clip_path.with_suffix(".notswing.mp4")
                try:
                    clip_path.rename(_kept)
                except OSError:
                    clip_path.unlink(missing_ok=True)
                log.info("copy: NOT UPLOADING %s — judged not a swing (%s). "
                         "Kept as %s", session_id, _vreason or "no reason",
                         _kept.name)
                if detail is not None:
                    detail["verdict"] = False
                return None
        if _verdict is True and _t is not None:
            try:
                _t.note_swing(float(impact_ts))
            except Exception:  # noqa: BLE001
                pass
        if detail is not None and _verdict is not None:
            detail["verdict"] = bool(_verdict)
            detail["verdict_confidence"] = _vconf
            detail["verdict_reason"] = _vreason

        # REGISTER ONLY ONCE IT IS WORTH REGISTERING -- the verdict
        # above has already run, because registration wakes the green
        # and a clip refused afterwards would leave an event with a
        # green half and no tee half for ever.
        #
        # Register before UPLOADING, so the green hears about it while
        # its own ring still holds the flight. Registration failing is
        # not fatal: the uploader's _recover_lost_event picks the
        # session up when the link returns, and the tee half survives
        # even though the green half cannot.
        try:
            runner.client.event_trigger(
                session_id, impact_at=float(impact_ts),
                clock_offset_sec=clock_offset_sec, detail=detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("copy: could not register the impact event (%s) — "
                        "the clip will be recovered later, but the green "
                        "half is lost", exc)

        up = getattr(runner, "uploader", None)
        if up is None:
            clip_path.unlink(missing_ok=True)
            return None
        up.enqueue(session_id, clip_path, res["start"],
                   real_fps=None, precompressed=True)
        log.info(
            "copy: IMPACT CUT session=%s — %.1fs (%.1f MB) around the "
            "impact at %.3f", session_id, res["seconds"],
            res["bytes"] / 1e6, impact_ts,
        )
        return session_id

    # ---- swing selection -----------------------------------------------

    def _enqueue_swings(self, runner, up, session_id, clip_path, res,
                        t_trigger) -> int:
        """Upload only the swings. Returns how many clips were sent.

        ZERO MEANS "SEND THE WHOLE THING", and every failure path
        returns zero. That is the whole safety story: this can decline,
        it can throw, ffmpeg can refuse, the tracker can be absent —
        and the caller still uploads the session exactly as it did
        before. Nothing here can lose a swing that was recorded; the
        worst it can do is fail to save bandwidth.

        Why selection is worth the trouble: a 120 s session at 4000
        kbps is 60 MB, eleven minutes on a link that re-enumerates
        every couple of minutes, and it usually does not finish. The
        same session as two 13.5 s cuts is 6.75 MB each, about seventy
        seconds, and those fit between the drops. The win is not the
        byte count — it is landing inside the gap.
        """
        tracker = getattr(runner, "motion", None)
        if tracker is None:
            return 0
        try:
            clip_start = float(res["start"])
            clip_secs = float(res["seconds"])
            times, motion = tracker.span(clip_start, clip_start + clip_secs)
            if len(times) < 10:
                log.info("copy: only %d motion samples for session=%s — "
                         "uploading the session whole", len(times), session_id)
                return 0

            # None and [] MEAN DIFFERENT THINGS and must not be
            # conflated. None is "no detector ran" -> do not gate, or a
            # green camera would reject every swing. [] is "the
            # detector ran and saw nobody" -> gate, and the session
            # falls back to uploading whole. Writing `if people else
            # None` collapsed the two and let cloud movement through on
            # a camera with no golfer in it.
            seen = getattr(runner, "_person_seen_at", None)
            people = (None if seen is None
                      else [t - clip_start for t in tee_roi.seen_times(seen)])

            dbg: dict = {}
            windows = merge_windows(find_swings(
                times, motion, duration_sec=clip_secs, person_times=people,
                debug=dbg))
            if not windows:
                # SAY HOW CLOSE IT CAME. "No swing found" is the same
                # message whether the best peak was 3.9 against a 4.0
                # threshold or 1.1 against it, and those are different
                # problems with different fixes -- one is a number to
                # lower, the other is a camera to re-aim.
                log.info(
                    "copy: no swing found in %.0fs for session=%s — "
                    "uploading whole (nothing is discarded). Best peak was "
                    "%.2f at t=%.1fs against a threshold of %.1f; %d "
                    "sample(s) cleared it, %d rejected for nobody in the "
                    "ROI (%s sighting(s) to check against), %d for "
                    "too wide a burst%s. %d samples at %.1f Hz.",
                    clip_secs, session_id, dbg.get("best_ratio", 0),
                    dbg.get("best_at") or -1, dbg.get("min_ratio", 0),
                    dbg.get("above_threshold", 0),
                    dbg.get("rejected_no_person", 0),
                    ("gate off" if dbg.get("n_person_times") is None
                     else dbg.get("n_person_times")),
                    dbg.get("rejected_too_wide", 0),
                    (" (widest %.1fs)" % dbg["widest_rejected_sec"]
                     if dbg.get("widest_rejected_sec") else ""),
                    dbg.get("samples", 0), dbg.get("rate_hz", 0),
                )
                return 0

            cov = coverage_fraction(windows, clip_secs)
            if cov >= _CUT_WORTH_IT_BELOW:
                log.info("copy: %d swing(s) cover %.0f%% of session=%s — "
                         "cutting would not save anything, uploading whole",
                         len(windows), cov * 100, session_id)
                return 0

            # CUT EVERYTHING FIRST, ENQUEUE NOTHING UNTIL ALL OF IT
            # WORKED. An enqueue cannot be taken back: handing the
            # uploader a clip and then deciding to send the whole
            # session instead means the backend receives both, and the
            # cut file gets deleted out from under a worker that is
            # about to read it. So the two phases stay separate.
            cuts = []
            for n, w in enumerate(windows, 1):
                dst = clip_path.with_name(
                    "%s-sw%d%s" % (clip_path.stem, n, clip_path.suffix))
                if not _subcut(clip_path, dst, w["start_sec"], w["end_sec"]):
                    break
                cuts.append((n, w, dst))

            if len(cuts) < len(windows):
                # A partial set silently loses the swings whose cuts
                # did not make it. Throw the lot away and let the
                # caller send the session whole.
                log.warning("copy: only %d of %d cuts succeeded for "
                            "session=%s — uploading whole instead so no "
                            "swing is lost", len(cuts), len(windows),
                            session_id)
                for _, _, dst in cuts:
                    dst.unlink(missing_ok=True)
                return 0

            for n, w, dst in cuts:
                # Each cut is its own session for the backend, derived
                # from the parent so the two can be matched up later.
                up.enqueue("%s-s%d" % (session_id, n), dst,
                           clip_start + w["start_sec"],
                           real_fps=None, precompressed=True)
                log.info(
                    "copy: swing %d/%d of session=%s — %.1f-%.1fs "
                    "(%.1fs, %.1f MB, ratio %.1f, %s)",
                    n, len(windows), session_id, w["start_sec"],
                    w["end_sec"], w["end_sec"] - w["start_sec"],
                    dst.stat().st_size / 1e6, w["ratio"], w["confidence"],
                )

            log.info("copy: session=%s sent as %d swing clip(s), %.0f%% of "
                     "the footage — %.0f%% less to upload",
                     session_id, len(cuts), cov * 100, (1 - cov) * 100)
            clip_path.unlink(missing_ok=True)
            return len(cuts)
        except Exception as exc:  # noqa: BLE001
            log.warning("copy: swing selection failed for session=%s (%s) — "
                        "uploading the session whole", session_id, exc)
            return 0
