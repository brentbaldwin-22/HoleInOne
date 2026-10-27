"""Cut a swing from the rings because it was HEARD, not because
somebody stood still long enough.

HOW THE PIECES SIT. Three things already exist and this joins them:

    copycap.SegmentRing   a rolling window of the camera's H.264
    audiocap.AudioRing    a rolling window of its sound
    swingaudio.SwingListener  impacts, fed a second at a time

and one thing the tee loop already records: WHERE a person was
standing, and when. A crack plus a golfer on that tee at that moment
is a swing; a crack on its own is a car door, a neighbouring group, or
somebody dropping a bag.

WHY BOTH, AND NOT EITHER ALONE. Measured on this course:

  * MOTION CANNOT DO IT. Eight clips, twenty swings: the best signal
    found 13 of 20 and no settings pair was stable. A foursome walking
    and pulling clubs is the noise floor and it is high.
  * SOUND CANNOT TELL WHOSE SWING IT IS. Nothing in a crack says which
    tee it came from, and the camera watches more than one. At ratio 8
    talking is rejected on level with about a 1.3x margin, which is
    thin enough that one loud conversation would cost a clip.

The zone check is what makes the thin margin survivable: a false peak
has to coincide with a golfer standing in the named zone to cost
anything.

SHADOW MODE IS THE DEFAULT and it is not a formality. This decides
what to upload; a detector that is wrong in a way nobody has measured
yet would silently stop clips reaching the backend. In shadow it
writes the decision it WOULD have made to the journal and changes
nothing, so a day of running it can be compared against what the dwell
actually recorded before anything depends on it.

NOTHING HERE CAN LOSE A SWING, in either mode. The dwell keeps
recording and uploading exactly as it does today; this adds cuts, it
does not gate them. Turning it to live only adds clips.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from . import tee_roi

log = logging.getLogger("golfreelz_agent.audiotrigger")

DEFAULT_BEFORE_SEC = 4.5
DEFAULT_AFTER_SEC = 9.0
# How far from the crack a golfer may have last been seen. The ROI
# detector runs every few hundred ms at best and a player at address is
# barely moving, so this is generous on purpose: it is answering "was
# this tee occupied", not "was somebody moving at that instant".
DEFAULT_CONFIRM_TOLERANCE_SEC = 6.0
# Below this the envelope's median is digital silence, not a quiet
# course. Measured ambient on this course sits around 0.0008-0.004 of
# full scale after the high-pass; a dead microphone reads 0.
_SILENCE_FLOOR = 1e-5


class AudioTrigger:
    """Feed it the rings; it decides which swings to cut."""

    def __init__(
        self,
        ring,
        listener,
        *,
        sightings: Callable[[], list],
        video_span: Optional[Callable[[], tuple]] = None,
        confirm_zones: Optional[list] = None,
        confirm_tolerance_sec: float = DEFAULT_CONFIRM_TOLERANCE_SEC,
        before_sec: float = DEFAULT_BEFORE_SEC,
        after_sec: float = DEFAULT_AFTER_SEC,
        shadow: bool = True,
        on_cut: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.ring = ring
        self.listener = listener
        self.sightings = sightings
        self.video_span = video_span
        # None means "do not ask about geography". NOT the same as [],
        # which names no zone and could never be satisfied -- the same
        # trap that has already cost this codebase one bug with
        # person_times.
        self.confirm_zones = confirm_zones
        self.confirm_tolerance_sec = float(confirm_tolerance_sec)
        self.before_sec = float(before_sec)
        self.after_sec = float(after_sec)
        self.shadow = bool(shadow)
        self.on_cut = on_cut
        self._fed: Optional[str] = None
        self._fed_at = 0.0
        self.heard = 0
        self.confirmed = 0
        self.cut_count = 0

    # ---- the loop ------------------------------------------------------

    def tick(self) -> list:
        """Feed whatever audio is new, and judge whatever it hears.

        AND KEEP THE RING ITSELF ALIVE. AudioRing.tick says "call from
        the agent's loop" and nothing ever did, so for as long as the
        audio trigger has existed the ring has neither swept nor
        restarted: `window_seconds` bounded nothing, the directory grew
        at 32 KB/s -- 1.4 GB a day, never cleared, across every restart
        -- and an ffmpeg that died stayed dead with no log line, since
        the restart lives in the same uncalled method.

        Here rather than in the agent loop because this is already the
        one thing the loop calls on the audio path, and a second call
        site is a second thing to forget.
        """
        try:
            self.ring.tick()
        except Exception as exc:  # noqa: BLE001
            # Housekeeping must not cost the listening it serves.
            log.warning("audio ring: tick failed (%s)", exc)
        out = []
        for seg in self._new_segments():
            from .audiocap import _seg_start
            ts = _seg_start(seg)
            if ts is None:
                continue
            try:
                pcm = seg.read_bytes()
            except OSError:
                # Swept from under us mid-read. The listener's own gap
                # handling covers the hole; skipping is right.
                continue
            self._fed = seg.name
            self._fed_at = __import__("time").monotonic()
            for hit in self.listener.feed(pcm, ts):
                out.append(self._judge(hit))
        return out

    def healthy(self) -> bool:
        """Is this actually listening to something right now?

        THE QUESTION THE DWELL NEEDS ANSWERED before it stands aside.
        "Is the ring running" is not enough: a camera whose Audio in
        got unchecked streams a perfectly healthy session of digital
        silence, which produces segments at the usual rate and would
        pass any liveness check that only counted files. That is not
        hypothetical -- it is exactly the state the tee was in until
        25 Oct and the state the green is in now.

        So both: segments still arriving, AND a noise floor that
        implies a microphone. A median at zero is a dead mic; the real
        floor on this course sits far above it.
        """
        import time as _t
        if self._fed_at <= 0.0 or _t.monotonic() - self._fed_at > 20.0:
            return False
        return float(getattr(self.listener, "median", 0.0)) > _SILENCE_FLOOR

    def _new_segments(self) -> list:
        segs = self.ring.segments()
        if self._fed is None:
            # START AT THE LIVE EDGE, not at the oldest thing on disk.
            # A restart would otherwise replay the whole window and
            # report every swing in it as if it had just happened --
            # including ones already uploaded.
            if segs:
                self._fed = segs[-1].name
            return []
        return [p for p in segs if p.name > self._fed]

    # ---- the decision ---------------------------------------------------

    def _judge(self, hit: dict) -> dict:
        at = float(hit["at"])
        self.heard += 1
        d = {
            "at": at,
            "ratio": round(float(hit.get("ratio") or 0.0), 1),
            "attack_ms": round(float(hit.get("attack_sec") or 0.0) * 1000, 1),
            "start": at - self.before_sec,
            "end": at + self.after_sec,
            "confirmed": False,
            "why": None,
            "cut": False,
            # Which tee the movement was on, for saying so afterwards.
            # None whenever it cannot be said -- see zone_seen_in.
            "zone": None,
        }
        seen = self.sightings() if self.sightings else None
        if seen is None:
            # No detector ran. Do not gate -- a camera whose person
            # detector has stopped must not also silently stop cutting.
            d["confirmed"] = True
            d["why"] = "no person detector; not gating"
        elif tee_roi.seen_in_zones(seen, self.confirm_zones, at,
                                   self.confirm_tolerance_sec):
            d["confirmed"] = True
            d["zone"] = tee_roi.zone_seen_in(
                seen, self.confirm_zones, at, self.confirm_tolerance_sec)
            d["why"] = ("person in zone %d" % d["zone"]
                        if d["zone"] is not None else "person in zone")
        else:
            d["why"] = ("nobody in %s within %.0fs"
                        % ("any zone" if self.confirm_zones is None
                           else "zone(s) %s" % self.confirm_zones,
                           self.confirm_tolerance_sec))

        if d["confirmed"] and self.video_span is not None:
            first, last = self.video_span()
            if first is None or last is None:
                d["confirmed"] = False
                d["why"] = "the video ring is empty"
            elif d["start"] < first or d["end"] > last:
                # Not a rejection of the SWING -- the sound was real,
                # the footage is simply not all there. Said plainly,
                # because "no swing found" and "the ring had aged out
                # from under it" need different fixes.
                d["confirmed"] = False
                d["why"] = ("the video ring holds %.1f..%.1f, this needs "
                            "%.1f..%.1f" % (first, last, d["start"], d["end"]))

        if not d["confirmed"]:
            log.info("audio trigger: heard x%.1f at %.3f — not cutting (%s)",
                     d["ratio"], at, d["why"])
            return d

        self.confirmed += 1
        if self.shadow:
            log.info(
                "audio trigger [SHADOW]: would cut %.1f..%.1fs around the "
                "impact at %.3f — x%.1f, attack %.0f ms, %s",
                d["start"], d["end"], at, d["ratio"], d["attack_ms"], d["why"])
            return d
        d["cut"] = True
        self.cut_count += 1
        log.info("audio trigger: cutting %.1f..%.1fs around %.3f — x%.1f, %s",
                 d["start"], d["end"], at, d["ratio"], d["why"])
        if self.on_cut is not None:
            try:
                self.on_cut(d)
            except Exception as exc:  # noqa: BLE001
                log.warning("audio trigger: the cut failed (%s)", exc)
                d["cut"] = False
        return d


def build(cfg: dict, work_dir, runner, video_span=None, on_cut=None):
    """An AudioTrigger from config, or None when it is not asked for.

    Returning None rather than a disabled object is deliberate: every
    call site stays `if t is not None`, and a rig that has not opted in
    never starts a second RTSP session to its camera.
    """
    a = (cfg.get("audio_trigger") or {})
    if not a.get("enabled", False):
        return None
    cam = (cfg.get("camera") or {})
    device = str(cam.get("device") or "")
    if not device.startswith(("rtsp://", "rtsps://")):
        log.warning("audio trigger: needs an rtsp:// camera — not starting")
        return None
    import os
    device = device.replace(
        "{password}", os.environ.get("GOLFREELZ_CAM_PASSWORD", ""))

    from pathlib import Path
    from .audiocap import AudioRing
    from .swingaudio import SwingListener

    sr = int(a.get("sample_rate", 16000))
    ring = AudioRing(
        device, Path(work_dir) / "audioring",
        segment_seconds=float(a.get("segment_seconds", 1.0)),
        window_seconds=float(a.get("window_seconds", 185.0)),
        sample_rate=sr, channels=1,
    )
    if not ring.start():
        return None
    listener = SwingListener(
        sr,
        highpass_hz=float(a.get("highpass_hz", 1500.0)),
        min_peak_ratio=float(a.get("min_ratio", 8.0)),
        max_attack_sec=float(a.get("max_attack_ms", 30.0)) / 1000.0,
        min_separation_sec=float(a.get("min_separation_sec", 6.0)),
        baseline_sec=float(a.get("baseline_sec", 30.0)),
    )
    # ZONES ARE A LIST OR ABSENT. Absent means "any zone"; a list names
    # them by index, in the order the camera's boxes are stored. An
    # EMPTY list names none and could never be satisfied, so it is read
    # as absent rather than as a camera that can never fire.
    zones = a.get("confirm_zones")
    if isinstance(zones, (list, tuple)):
        zones = [int(z) for z in zones] or None
    else:
        zones = None
    t = AudioTrigger(
        ring, listener,
        sightings=lambda: getattr(runner, "_person_seen_at", None),
        video_span=video_span,
        confirm_zones=zones,
        confirm_tolerance_sec=float(
            a.get("confirm_tolerance_sec", DEFAULT_CONFIRM_TOLERANCE_SEC)),
        before_sec=float(a.get("before_sec", DEFAULT_BEFORE_SEC)),
        after_sec=float(a.get("after_sec", DEFAULT_AFTER_SEC)),
        shadow=bool(a.get("shadow", True)),
        on_cut=on_cut,
    )
    log.info(
        "audio trigger: %s, ratio x%.1f, zones %s, window %.0f..+%.0fs",
        "SHADOW (deciding, not cutting)" if t.shadow else "LIVE",
        listener.min_peak_ratio,
        "any" if zones is None else zones, t.before_sec, t.after_sec,
    )
    return t


def stop(t) -> None:
    """Shut the ring down. Safe on None and on a half-built trigger."""
    if t is None:
        return
    try:
        t.ring.stop()
    except Exception as exc:  # noqa: BLE001
        log.debug("audio trigger: stop failed (%s)", exc)
