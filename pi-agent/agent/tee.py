"""Tee-side capture agent. Reads camera frames continuously into a
ring buffer, runs MediaPipe Pose at N fps on downsampled copies,
triggers a recording when a person dwells in the configured tee-box
ROI for `trigger_dwell_seconds`.

If MediaPipe isn't importable (lighter Pi OS install, ARMv7, etc.)
the agent falls back to a coarse motion-density detector — much less
selective, but lets the operator validate the network plumbing
without the full ML stack."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .common import (
    agent_build_id,
    BackendClient,
    BackendUnreachable,
    BackgroundUploader,
    ClipWriter,
    FrameBuffer,
    HeartbeatThread,
    build_audio_recorder,
    clock_offset,
    mux_audio_into_video,
    drain_camera_settings,
    handle_camera_command,
    open_camera,
    link_info_fields,
    read_throttled,
    reclock_fps,
    write_fps_for,
    stream_info_fields,
    DeliveryMeter,
    TriggerGate,
)
from . import audiotrigger
from . import tee_roi
from .focus_meter import FocusMeter
from .livestream import LiveStreamer
from .copyengine import (
    CopyEngine,
    DEFAULT_COPY_FALLBACK_PREROLL,
    copy_configured,
    preroll_seconds,
)
from .swingfind import MotionTracker

log = logging.getLogger("golfreelz_agent.tee")

# Touch this file to force one capture without a person in the ROI:
#     touch /tmp/golfreelz-trigger
# Exists so the record + upload path can be exercised over SSH from
# anywhere — the alternative is someone standing in front of the camera,
# which is a site visit every time you want to test an upload fix.
FORCE_TRIGGER_PATH = Path("/tmp/golfreelz-trigger")


_last_trigger_mtime = 0.0


def prime_force_trigger() -> None:
    """Adopt any EXISTING sentinel's mtime as the baseline, at startup.

    `_last_trigger_mtime` starts at 0, so a leftover file fired a
    recording the instant the agent came up — and the file is usually
    leftover, because the operator touches it as `pi` while the agent
    runs as its own user and /tmp is sticky, so the unlink after firing
    silently fails. Every service restart therefore recorded and queued
    a spurious clip. Restarts are frequent during an update; this was
    quietly adding to the backlog we were trying to drain.

    Only a touch made AFTER startup should fire.
    """
    global _last_trigger_mtime
    try:
        _last_trigger_mtime = FORCE_TRIGGER_PATH.stat().st_mtime
        log.info(
            "force-trigger sentinel already exists — adopting it as the "
            "baseline; touch it again to fire a capture",
        )
    except OSError:
        _last_trigger_mtime = 0.0


def _take_force_trigger() -> bool:
    """True once per `touch`, keyed on MTIME rather than on deleting the
    file.

    Deleting it does not work: the operator touches it as `pi`, the agent
    runs as its own service user, and /tmp is sticky — so only the owner
    may unlink. The first version unlinked, caught the PermissionError,
    logged, and left the file in place: the condition stayed true, so it
    re-fired and re-logged every loop, several times a second, forever.

    Watching mtime is ownership-proof. Each touch bumps it, we fire once,
    and a file we cannot remove is simply ignored until it is touched
    again. Deletion is still attempted, but only as tidying.
    """
    global _last_trigger_mtime
    try:
        mtime = FORCE_TRIGGER_PATH.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        return False        # unreadable /tmp — stay silent, never spam
    if mtime <= _last_trigger_mtime:
        return False
    _last_trigger_mtime = mtime
    try:
        FORCE_TRIGGER_PATH.unlink()
    except OSError:
        pass                # not ours to delete; mtime already guards us
    return True
# The build id is a hash of the installed agent source, so this
# changes on every update and is identical on two Pis running the
# same code — which is the whole question when one camera's clips
# look wrong and the other's do not. See agent_build_id().
FIRMWARE = f"tee-0.1.0+{agent_build_id()}"

try:
    import mediapipe as mp  # type: ignore
    HAS_MEDIAPIPE = True
except Exception:  # pragma: no cover
    mp = None
    HAS_MEDIAPIPE = False


# ---------------------------------------------------------------------
# Person-in-ROI detectors
# ---------------------------------------------------------------------

class MediaPipePersonDetector:
    """MediaPipe Pose wrapper. Returns where the person is STANDING in
    native-frame pixel coords on each detect() call, or None if no
    person is found -- the x of their landmarks' centre and the y of
    the LOWEST one, which on a standing person is a foot. See
    `_in_roi` for why the feet and not the middle."""

    def __init__(self, detect_width: int = 320):
        self.detect_width = detect_width
        self.pose = mp.solutions.pose.Pose(
            model_complexity=0,
            min_detection_confidence=0.5,
        )

    def detect(self, frame) -> Optional[tuple[int, int]]:
        h, w = frame.shape[:2]
        if w > self.detect_width:
            scale = self.detect_width / float(w)
            small = cv2.resize(frame, (self.detect_width, int(round(h * scale))))
        else:
            scale = 1.0
            small = frame
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        result = self.pose.process(rgb)
        if not result.pose_landmarks:
            return None
        sh, sw = small.shape[:2]
        xs = [lm.x for lm in result.pose_landmarks.landmark]
        ys = [lm.y for lm in result.pose_landmarks.landmark]
        cx = (sum(xs) / len(xs)) * sw / scale
        # The lowest landmark, which on a standing person is a foot.
        # Pose gives ankles, heels and toes; taking the maximum needs no
        # knowledge of which index is which and degrades sensibly when
        # the feet are out of shot, to whatever IS lowest.
        cy = max(ys) * sh / scale
        return int(round(cx)), int(round(cy))

    def close(self):
        try:
            self.pose.close()
        except Exception:
            pass


class YoloPersonDetector:
    """YOLOv8n person detector running on OpenCV's built-in DNN module.

    Reports the bottom-centre of the person's box -- where they are
    STANDING, not where their middle is. See `_in_roi`.

    This is the production detector. It loads a pre-exported ONNX model
    (shipped in the repo at pi-agent/models/) and runs it with cv2.dnn —
    so it needs NO extra Python packages beyond the opencv we already
    depend on. That deliberately sidesteps the torch / ncnn / mediapipe
    wheel problems on newer Pi OS (Trixie / Python 3.13), which is what
    kept knocking us back to the motion detector.

    detect() returns the pixel center of the highest-confidence person
    in native-frame coords, or None. Same contract as the other
    detectors so the main loop doesn't care which one is active.
    """

    # COCO class 0 is "person". The model outputs 4 bbox + 80 class
    # scores per anchor; we only ever look at the person column.
    PERSON_CLASS = 0

    def __init__(
        self,
        model_path,
        input_size: int = 320,
        conf_threshold: float = 0.4,
        iou_threshold: float = 0.5,
        min_box_area_frac: float = 0.0,
    ):
        self.input_size = int(input_size)
        self.conf = float(conf_threshold)
        self.iou = float(iou_threshold)
        # Reject persons whose box is smaller than this fraction of the
        # frame — lets the operator ignore golfers on a *different* tee
        # far in the background. 0.0 = accept any person in the ROI.
        self.min_box_area_frac = float(min_box_area_frac)
        self.net = cv2.dnn.readNetFromONNX(str(model_path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def detect(self, frame, conf_override: Optional[float] = None) -> Optional[tuple[int, int]]:
        conf = self.conf if conf_override is None else float(conf_override)
        h, w = frame.shape[:2]
        n = self.input_size
        blob = cv2.dnn.blobFromImage(
            frame, 1 / 255.0, (n, n), swapRB=True, crop=False,
        )
        self.net.setInput(blob)
        out = self.net.forward()          # (1, 84, anchors)
        out = out[0].T                    # (anchors, 84)
        # Person score is column 4 (first class after the 4 bbox coords).
        person_scores = out[:, 4 + self.PERSON_CLASS]
        keep = person_scores >= conf
        if not np.any(keep):
            return None
        rows = out[keep]
        scores = person_scores[keep]
        min_area_px = self.min_box_area_frac * (w * h)
        boxes, confs = [], []
        for row, sc in zip(rows, scores):
            cx, cy, bw, bh = row[0], row[1], row[2], row[3]
            bw_px = bw / n * w
            bh_px = bh / n * h
            if bw_px * bh_px < min_area_px:
                continue
            x = (cx - bw / 2) / n * w
            y = (cy - bh / 2) / n * h
            boxes.append([int(x), int(y), int(bw_px), int(bh_px)])
            confs.append(float(sc))
        if not boxes:
            return None
        idxs = cv2.dnn.NMSBoxes(boxes, confs, conf, self.iou)
        if idxs is None or len(idxs) == 0:
            return None
        idxs = np.array(idxs).flatten()
        # Among surviving boxes, the most confident person -- reported at
        # their FEET. See _in_roi for why it is the feet and not the
        # middle.
        best = max(idxs, key=lambda i: confs[i])
        x, y, bw_px, bh_px = boxes[best]
        return int(x + bw_px / 2), int(y + bh_px)

    def close(self):
        pass


class MotionFallbackDetector:
    """Fallback when MediaPipe isn't installed: per-frame absolute
    difference against a running background mean, then the bottom of
    the high-motion mask. Much noisier than pose detection but
    keeps the rest of the loop testable on a stock Pi OS without
    the heavy ML deps."""

    def __init__(self, detect_width: int = 320):
        self.detect_width = detect_width
        self.bg: Optional[np.ndarray] = None
        self.alpha = 0.05  # background learning rate

    def detect(self, frame) -> Optional[tuple[int, int]]:
        h, w = frame.shape[:2]
        if w > self.detect_width:
            scale = self.detect_width / float(w)
            small = cv2.resize(frame, (self.detect_width, int(round(h * scale))))
        else:
            scale = 1.0
            small = frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self.bg is None:
            self.bg = gray
            return None
        diff = cv2.absdiff(gray, self.bg)
        self.bg = (1 - self.alpha) * self.bg + self.alpha * gray
        _, mask = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        mask = mask.astype(np.uint8)
        # Open kills isolated speckle (sensor noise); close merges a
        # person's separate motion patches (torso, arms, club) into one
        # coherent blob.
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        total_px = mask.shape[0] * mask.shape[1]
        # A global lighting / auto-exposure shift lights up most of the
        # frame at once — never a person.
        if int(cv2.countNonZero(mask)) > 0.40 * total_px:
            return None
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            return None
        # Key idea: a person is ONE coherent blob; sensor noise is many
        # scattered specks. Take the single largest connected region and
        # require it to be person-sized. Catches a golfer standing back at
        # the tee (small but solid) while ignoring the speckle that made a
        # blank wall false-trigger.
        biggest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(biggest) < 300:
            return None
        m = cv2.moments(biggest)
        if m["m00"] == 0:
            return None
        cx = m["m10"] / m["m00"]
        # THE BOTTOM OF THE BLOB, not its middle, to match what the
        # other two detectors report. Cruder here by nature: this has
        # no body model, so the bottom of a motion blob is the lowest
        # thing that moved rather than a foot -- a shadow or a club at
        # the bottom of its arc will drag it down. It is the last
        # resort detector and this keeps it answering the same question
        # as the others rather than a different one.
        _bx, _by, _bw, _bh = cv2.boundingRect(biggest)
        cy = _by + _bh
        return int(round(cx / scale)), int(round(cy / scale))

    def close(self):
        pass


class Dwell:
    """How long has somebody been standing in a zone, and is that long
    enough to start recording?

    Pulled out of the loop so it can be tested without a camera, a
    detector and a capture thread — and so there is ONE copy of the
    rule rather than one in the agent and another in a test that
    agrees with it by hand.

    TWO THINGS MAKE IT MORE THAN A STOPWATCH.

    A BLINK IS NOT A DEPARTURE. Detection runs at `detection.fps` (5),
    so a dwell of N seconds asks for N*5 consecutive sightings, and
    this project's own config notes that "a golfer bending to tee up,
    turning away, or stepping aside briefly makes the person detector
    drop out for a few seconds". The stop condition was built generous
    for exactly that; the start condition reset on a single miss. At
    two seconds it survived on luck. At ten it does not survive at
    all: measured on the model of this loop, a two-second blink four
    seconds in means the dwell is NEVER met in the sixteen seconds the
    golfer is actually there.

    AND THE CLOCK MUST NOT OUTLIVE THE PERSON. Once a blink is
    forgiven, elapsed time alone would let somebody who crossed the
    zone for a second trigger three seconds later, on an empty tee.
    So firing also requires them to be in a zone right now.
    """

    def __init__(self, dwell_seconds: float, grace_seconds: float):
        self.dwell = float(dwell_seconds)
        self.grace = float(grace_seconds)
        self.first_seen: Optional[float] = None
        self.last_seen: float = 0.0
        self.restarts = 0

    def reset(self) -> None:
        self.first_seen = None

    def arm(self, now: float) -> None:
        """Treat the dwell as ALREADY SERVED, as of `now`.

        For the length-cap split, where a clip ends at the cap with the
        golfer still standing there. They have been in the zone for two
        minutes; making them serve the dwell again is not a filter, it
        is a hole. And it is a hole that grows with the setting: at a
        two-second dwell the next clip opens inside the five-second
        pre-roll and the two overlap, which is what keeps a swing on
        the boundary from being cut in half. At ten seconds the pre-roll
        no longer covers it and five seconds of golf are simply missing.

        So the split re-arms rather than restarts, and the next clip
        opens on the following tick whatever the dwell is set to. The
        guard against this running away is `_splits` and the cooldown,
        which is a separate mechanism and still applies.
        """
        self.first_seen = now - self.dwell
        self.last_seen = now

    def update(self, now: float, in_roi: bool) -> bool:
        """Advance to `now`. True means trigger."""
        if in_roi:
            if self.first_seen is None:
                self.first_seen = now
            self.last_seen = now
        elif (self.first_seen is not None
                and now - self.last_seen > self.grace):
            self.first_seen = None
            self.restarts += 1
        return (in_roi
                and self.first_seen is not None
                and now - self.first_seen >= self.dwell)

    def waiting_for(self, now: float) -> Optional[float]:
        """Seconds still to go, or None when nobody is being timed."""
        if self.first_seen is None:
            return None
        return max(0.0, self.dwell - (now - self.first_seen))


def _in_roi(pt: tuple[int, int], roi) -> bool:
    """Is the golfer standing on A tee? Any zone counts.

    `pt` IS THE PERSON'S FEET, and used to be the middle of their
    bounding box. The difference is the whole usability of this
    feature.

    With a waist-height point, a zone has to be drawn around BODIES,
    and how far a body extends above the ground in pixels depends on
    how far away it is -- so the same tee needs a different box
    depending on where the camera sits, and an operator drawing the
    obvious thing (the mown rectangle people stand on) gets a zone that
    fires on nobody. Measured on hole 8: zones drawn around two tee
    surfaces would have caught neither tee, while the previous box,
    drawn generously enough to catch waists, also contained the green,
    the bunkers and the tees 60 yards further out.

    Feet are where someone IS. A zone becomes "this patch of ground",
    which is what people draw anyway and what the word zone means.

    A par 3 is played from a back tee and a middle tee, and the camera
    is given one box per tee rather than one box spanning both — a
    rectangle wide enough to hold them both also holds the path between
    them, and somebody walking that path is a recording nobody asked
    for. tee_roi accepts either shape, so a rig still running one box
    behaves exactly as it did.

    NOTE FOR ANY RIG UPDATED PAST THIS CHANGE: zones drawn for the old
    waist test sit too high and will under-trigger. They need redrawing
    onto the ground. The agent says so once at startup.
    """
    return tee_roi.contains(pt, roi)


def _roi_zone(pt, roi):
    """WHICH zone holds this point, for the record only. Never raises.

    Deliberately NOT part of the decision -- `_in_roi` above decides
    whether to record and keeps being the one seam that does, because
    the first version of this inlined the zone lookup into that branch
    and silently replaced the test double the stop-policy suite patches
    in. Annotating a sighting must not be able to change which
    sightings there are, and a detector returning a point this cannot
    read must not be able to stop a recording: that is a worse failure
    than an unattributed sighting, which merely makes the audio
    trigger fall back to "any zone".
    """
    try:
        return tee_roi.which(pt, roi)
    except Exception:  # noqa: BLE001
        return None


def _dwell_yield_zones(value):
    """Where the dwell may stand down for the audio trigger.

    Returns None for "nowhere", True for "any zone", or a set of zone
    indices.

    ONE CAMERA WATCHES TEES AT DIFFERENT RANGES, and the ears only
    reach some of them. A strike from a back tee measures x19-x30 after
    the high-pass; the same strike from a forward tee measures x1.02,
    because air absorbs the high frequencies first and the high-pass
    then removes what little survived. So "the audio trigger replaces
    the dwell" is true of one half of this frame and false of the
    other, and a single boolean has to answer for both: on, and every
    forward-tee swing is lost; off, and every back-tee golfer makes two
    events. Neither is the setting anybody wants.

    Hence a LIST, and three shapes:

        replaces_dwell: false      the dwell always fires (the default)
        replaces_dwell: true       it stands down in any zone
        replaces_dwell: [1, 2]     only in zones 1 and 2

    AN EMPTY LIST IS "NOWHERE", not "anywhere" -- the opposite of what
    `confirm_zones: []` means a few lines down in the same config, and
    deliberately so. These two settings push in opposite directions: a
    permissive confirm_zones records a few clips nobody wanted, while a
    permissive replaces_dwell suppresses the dwell across tees the
    microphone cannot hear and quietly records nothing at all. When the
    operator's intent is unclear, each falls to the side where the
    failure is a surplus clip rather than a missing one.
    """
    if value is True:
        return True
    if isinstance(value, (list, tuple, set)):
        out = set()
        for z in value:
            try:
                out.add(int(z))
            except (TypeError, ValueError):
                continue
        return out or None
    return None


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# How deep the decoded pre-roll ring has to be
# ---------------------------------------------------------------------

# WHEN THE COPY ENGINE RUNS, THE SEGMENT RING IS THE PRE-ROLL.
#
# Both engines were filling a pre-roll at once. The copy engine keeps
# ~155s of the camera's own H.264 on disk, and alongside it the decode
# path kept `buffer_seconds * fps` DECODED frames in RAM -- 301 numpy
# arrays of 1920x1080x3 at 1080p60, about 1.9GB, written every frame and
# read by nothing, because the copy engine cuts its clips out of the
# segment ring instead.
#
# IT CANNOT GO TO ZERO. Two things still read the ring: the detector,
# which only ever looks at snapshot()[-1], and the decode FALLBACK at
# the record fork -- an unhealthy segment ring sends that one swing down
# the old path rather than losing it, and that path opens its clip with
# whatever pre-roll is in RAM. So the ring shrinks to a fallback depth
# rather than disappearing.
#
class TeeAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = BackendClient(cfg["backend_url"], cfg["auth_token"])
        # The zones this agent triggers on. The card's own config is the
        # bootstrap; the backend's zones replace it on the first status
        # poll and whenever an operator redraws them. Held behind a lock
        # because the poll thread writes it while the capture loop reads
        # it every frame.
        self.roi = cfg.get("tee_box_roi")
        self._roi_lock = threading.Lock()
        self._roi_frame: tuple[int, int] | None = None
        # The last payload the backend sent, exactly as sent. Only the
        # backend's zones are re-scaled — a box from config.yaml has
        # already been through _apply_capture_mode and scaling it again
        # here would halve it twice.
        self._roi_raw = None
        # WHAT THIS CAMERA IS REALLY SENDING, for the admin card. The
        # config's fps is a belief on an RTSP camera — nothing here ever
        # asks the camera for a rate — so the delivered rate is measured
        # and the camera's own profile is fetched on request.
        self._delivery = DeliveryMeter()
        self._open_size = None
        self._open_fps = None
        self._stream_profile = None
        self.cam_cfg = cfg.get("camera", {})
        # Operator-requested capture, seconds, set from the
        # live-stream poll thread and consumed by the main loop.
        self._pending_capture: float | None = None
        self._capture_lock = threading.Lock()
        self.det_cfg = cfg.get("detection", {})
        # Lenient confidence used ONLY to keep an in-progress recording
        # alive. A golfer bent over the ball reads at lower confidence than
        # the strict trigger threshold wants, so keying the "still present"
        # check on the strict threshold dropped them mid-shot (clip ended
        # while they were clearly on the mat). Starting a clip still needs a
        # confident person; staying recorded only needs a faint one.
        self.keepalive_conf = float(
            self.det_cfg.get("keepalive_conf_threshold", 0.2)
        )
        self.buffer_seconds = float(cfg.get("buffer_seconds", 5))
        # NOBODY TEES OFF IMMEDIATELY. The trigger fires when a person
        # dwells in the tee box, but from there it is a tee peg, a
        # glove, a couple of waggles -- ten seconds at the very fastest
        # and usually twenty or more. Recording that is a pile of bytes
        # to compress and push over a cellular link for footage nobody
        # will ever watch. So the trigger arms the session and the
        # recording starts this many seconds later, pre-roll and all.
        # An operator Capture from the Cameras page ignores it: that
        # click means "record now".
        self.record_delay = float(cfg.get("record_delay_seconds", 10))
        # Runaway-safety cap. A normal session ends when the tee box
        # has been empty for no_person_timeout_seconds; this cap is
        # only ever hit if the detector misclassifies something (eg a
        # parked cart) as a person for an extended period. 10 min is
        # long enough for a foursome on a slow tee box.
        # CLIP LENGTH USED TO BE UPLOAD SIZE. It was cut to 30s when an
        # upload was one whole-file POST against a 180s write timeout:
        # nothing above ~10 MB ever completed, so a long clip did not
        # arrive at all and the cap was how long a clip COULD be.
        #
        # Chunked, resumable upload removed that ceiling. A clip is sent
        # in slices, the server banks each one, and a clip that needs
        # twenty minutes and four link outages still lands. Size now costs
        # TIME, not the clip -- and the encode bitrate comes down on its
        # own when the link proves it cannot keep up, so a long clip on a
        # bad link gets smaller rather than getting lost.
        #
        # So the cap goes back to being a safety rail. 30s was cutting
        # real swings in half; 120s covers a golfer's whole time over the
        # ball with room to spare, and still bounds a stuck ROI to two
        # minutes of footage rather than an unbounded recording.
        self.max_clip_seconds = float(cfg.get("max_clip_seconds", 120))
        # How many capped clips in an unbroken row before we decide the ROI
        # is holding on somebody who is not going to swing, and pause.
        #
        # These MOVE WITH THE CAP. At 30s clips, four splits then 90s off
        # was 8 minutes of recording an hour; at 120s the same numbers are
        # eight minutes solid then a 90s pause — an 84% duty cycle, which
        # is not a guard. Two capped clips in a row is already four
        # minutes of unbroken presence, which is all the evidence needed
        # that nobody is about to swing.
        self.max_splits = int(cfg.get("max_consecutive_splits", 2))
        self.split_cooldown = float(cfg.get("split_cooldown_seconds", 180))
        self.heartbeat_seconds = int(cfg.get("heartbeat_seconds", 60))
        # Compress each clip to H.264 at this bitrate (kbps) before upload.
        # Makes clips play in any browser (mp4v won't play in desktop
        # Chrome) and lighter on a cellular SIM. Higher default than the
        # green (3.5 Mbps vs 1.5) because the tee feeds the ball tracer and
        # needs the extra detail. Dropped from 5 Mbps: measured cellular
        # showed the tee is the flakier link (1.5% loss, 450 ms jitter vs
        # green's 0%/204 ms), so the tee's oversized clip was landing last
        # and stalling the pairing. 3.5 Mbps keeps ample tracer detail while
        # cutting the file ~30% so it clears the tee's jittery link sooner.
        # Set to 0 to disable.
        self.upload_bitrate_kbps = int(cfg.get("upload_bitrate_kbps", 3500))
        self.upload_scale_height = cfg.get("upload_scale_height")
        # NOT /tmp. The spool (work_dir/pending) holds clips part-way
        # through an upload, sometimes for tens of minutes across
        # link outages -- and /tmp does not survive a reboot. The
        # unit's StateDirectory= creates this owned by the service
        # user, so a config that never mentions work_dir still gets
        # somewhere durable.
        self.work_dir = Path(
            cfg.get("work_dir", "/var/lib/golfreelz-agent"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        # Whether to record a swing whose trigger could not be
        # registered. Beside the spool on purpose: both exist so that an
        # unreachable backend costs footage later rather than never.
        self._gate = TriggerGate(self.work_dir)
        # The session whose trigger never landed, if the one currently
        # recording is that session. Only ever one at a time -- the
        # capture loop runs a session to completion before looking for
        # the next -- so a single slot is the honest shape, and an
        # attribute that is usually None keeps the loop readable.
        self._unregistered_session: Optional[str] = None
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def _resolve_model_path(self) -> Optional[Path]:
        """Find the YOLO ONNX model. Honors detection.model_path in the
        config, else looks for models/yolov8n.onnx alongside the agent
        install (../models relative to this file). Returns None if absent."""
        configured = self.det_cfg.get("model_path")
        if configured:
            p = Path(configured)
            return p if p.exists() else None
        # agent/tee.py -> agent/ -> install root -> models/
        default = Path(__file__).resolve().parent.parent / "models" / "yolov8n.onnx"
        return default if default.exists() else None

    def _build_detector(self, det_width: int):
        """Pick the best available detector: YOLOv8n (OpenCV DNN) first —
        no extra deps, OS-independent — then MediaPipe if installed, then
        the crude motion detector as a last resort."""
        model_path = self._resolve_model_path()
        if model_path is not None:
            try:
                detector = YoloPersonDetector(
                    model_path,
                    input_size=int(self.det_cfg.get("input_size", 320)),
                    conf_threshold=float(self.det_cfg.get("conf_threshold", 0.4)),
                    iou_threshold=float(self.det_cfg.get("iou_threshold", 0.5)),
                    min_box_area_frac=float(
                        self.det_cfg.get("min_box_area_frac", 0.0)
                    ),
                )
                log.info(
                    "using YOLOv8n person detector (OpenCV DNN) — model=%s",
                    model_path,
                )
                return detector
            except Exception as exc:
                log.warning(
                    "YOLO model load failed (%s) — trying next detector", exc,
                )
        if HAS_MEDIAPIPE:
            log.info("using MediaPipe Pose person detector")
            return MediaPipePersonDetector(detect_width=det_width)
        log.warning(
            "no YOLO model and mediapipe not importable — falling back to "
            "motion-density detector (less selective; ship models/yolov8n.onnx "
            "for production accuracy).",
        )
        return MotionFallbackDetector(detect_width=det_width)

    def _on_lens_command(self, op: str, amount: int,
                         params: dict | None = None,
                         repeat: int = 1) -> None:
        """Zoom/focus nudge or an exposure change. See
        common.handle_camera_command — shared with the green runner so
        the two cannot drift."""
        handle_camera_command(self, op, amount, params, repeat)

    def apply_tee_zones(self, payload) -> None:
        """Adopt the backend's trigger zones, scaled to OUR frames.

        Called on every status poll with the current answer, so this is
        mostly a no-op comparison; it logs only when something actually
        moved, which makes "when did the zones change" answerable from
        the Pi's own journal.

        SILENTLY IGNORES AN EMPTY PAYLOAD. None means the camera has no
        zones in the app, and the ones from this card's config are
        better than none at all — a tee that stops triggering because
        nobody has drawn a box in the UI yet would be a bad trade.
        """
        if not payload or not tee_roi.boxes(payload):
            return
        # KEEP THE PAYLOAD AS SENT, and scale a copy. The frame size is
        # not known until the capture is open, so the first zones can
        # arrive unscaled; run() re-applies this raw copy once it knows,
        # and scaling the already-scaled result instead would shrink the
        # zones a second time.
        self._roi_raw = payload
        size = self._roi_frame
        scaled = tee_roi.scaled(payload, size[0], size[1]) if size else payload
        boxes = tee_roi.boxes(scaled)
        with self._roi_lock:
            if tee_roi.boxes(self.roi) == boxes:
                return
            self.roi = scaled
        # SWING SELECT MEASURES WHERE THE OPERATOR SAID THE GOLFERS ARE.
        # The zones are already drawn per camera and already exclude
        # the cart path and the bench between tees; a second ROI typed
        # into config.yaml is the same fact stored twice, and the two
        # drifted apart the first time this ran in the field.
        tracker = getattr(self, "motion", None)
        if tracker is not None and size:
            try:
                tracker.set_zones(boxes, size[0], size[1])
            except Exception as exc:  # noqa: BLE001
                log.debug("swing select: could not adopt zones (%s)", exc)
        # SAY WHAT THE BOX MEANS, every time they change. A zone is
        # tested against the golfer's FEET, and a rig updated past that
        # change is carrying zones drawn for a waist-height point --
        # which sit too high and quietly stop triggering. One line
        # beside the zones themselves is where somebody reading the log
        # to find out why nothing records will actually see it.
        log.info(
            "zones are matched against where a person is STANDING (their "
            "feet). A box drawn around a golfer rather than around the "
            "ground they stand on will under-trigger.")
        log.info(
            "trigger zones updated from the backend: %s",
            ", ".join(
                f"{b.get('label') or 'zone'} {b['x']},{b['y']} "
                f"{b['w']}x{b['h']}" for b in boxes
            ),
        )

    def _on_focus_mode(self, seconds: float) -> None:
        """Backend says focus mode is armed for `seconds` (0 = off).

        Called on every watch-status poll while armed, so the deadline
        keeps sliding forward and the mode ends a beat after the backend
        stops asking -- no explicit "off" message to lose.

        Guarded on both attributes because this arrives from the
        livestream thread, which starts before the meter and the
        heartbeat exist on the green runner.
        """
        deadline = time.monotonic() + float(seconds or 0)
        meter = getattr(self, "_focus", None)
        if meter is not None:
            meter.set_fast_until(deadline)
        hb = getattr(self, "_hb", None)
        if hb is not None:
            hb.set_fast_until(deadline)

    def _request_capture(self, seconds: float) -> None:
        """Operator pressed Capture. Records `seconds` regardless of
        whether anyone is in the ROI, then follows the ordinary trigger
        path — so the clip reaches Production exactly like a real swing
        would. Called from the live-stream poll thread."""
        with self._capture_lock:
            self._pending_capture = float(seconds)

    def _take_pending_capture(self) -> float | None:
        """Consume a pending operator capture, if any."""
        with self._capture_lock:
            secs, self._pending_capture = self._pending_capture, None
        return secs

    def _capture_loop(self, cap) -> None:
        """Dedicated capture thread — the ONLY place cap.read() runs.
        Drains the camera into the ring buffer at full frame rate and
        keeps the live view fresh. Because nothing in here blocks (no
        detection, no disk writes), the camera is never starved, so no
        frames get dropped — that starvation was the source of the
        recorded-clip stutter when detection ran inline."""
        prev = None
        # Watchdog. A wedged V4L2 pipeline (libcamerify losing its buffer
        # queue on open — 'select() timeout' every 10s) returns failure
        # FOREVER, and this loop used to spin on it silently until
        # someone SSHed in. The camera itself is fine: rpicam-still
        # captures normally while the agent sees nothing. So don't
        # diagnose the cause here, just refuse to sit in the broken
        # state — release and reopen until frames come back.
        stall_after = float(self.cam_cfg.get("stall_reopen_seconds", 15.0))
        last_good = time.time()
        reopens = 0
        while not self.stopping.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                if time.time() - last_good > stall_after:
                    reopens += 1
                    log.error(
                        "camera delivered no frames for %.0fs — reopening "
                        "(attempt %d)", stall_after, reopens,
                    )
                    try:
                        cap.release()
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(1.0)
                    try:
                        cap = open_camera(self.cam_cfg)
                        self.cap = cap          # run()'s finally releases this one
                        log.info("camera reopened after stall")
                    except Exception as exc:  # noqa: BLE001
                        log.error("camera reopen failed: %s", exc)
                    # Restart the clock either way, so a failed reopen
                    # retries on the next interval instead of hammering.
                    last_good = time.time()
                    prev = None
                time.sleep(0.02)
                continue
            if reopens:
                log.info("camera recovered after %d reopen(s)", reopens)
                reopens = 0
            last_good = time.time()
            # Camera-side delivery stats. Recorded independently of the
            # writer so the timing log can say whether a stall was the
            # CAMERA not delivering frames (thermal/driver) or OUR
            # pipeline failing to keep up — two different fixes.
            _cnow = time.time()
            if self._cap_last is not None:
                _cgap = _cnow - self._cap_last
                # AGAINST WHAT THE CAMERA IS DELIVERING, not what the
                # config believes. This counter is the one that says
                # "the camera stalled" as opposed to "our pipeline fell
                # behind" — and on a camera configured for 50 while
                # delivering 30, it called every ordinary 33 ms interval
                # a stall, because the threshold it was using was 36 ms.
                # A diagnostic that cries camera fault on a healthy
                # camera is worse than no diagnostic.
                _dfps, _ = self._delivery.read()
                if _cgap > 1.8 / max(1.0, _dfps or self.fps):
                    self._cap_gaps += 1
                    self._cap_worst = max(self._cap_worst, _cgap)
            self._cap_last = _cnow
            self._cap_frames += 1
            self._delivery.tick()
            fcopy = frame.copy()
            # Drop exact-duplicate reads. The GoPro in USB-webcam mode
            # hands cv2 the same frame again ~once/second (the read loop
            # outpaces the sensor's true ~29 fps). Buffered + written at
            # a fixed fps, each repeat is a 1-frame freeze then a
            # double-step jump. Genuine captures always differ by at
            # least sensor noise, so byte-identical means a re-read —
            # safe to skip so the clip stays smooth.
            # TWO-STAGE duplicate check. Stage 1: sparse grid (every
            # 16th pixel, ~1/256th the cost) — different frames almost
            # always differ here, so the fast path stays fast. Stage 2:
            # only when the sparse grid matches, confirm with the FULL
            # compare — a static scene can look identical on the grid
            # while genuinely differing by sensor noise, and dropping
            # those real frames showed up as ~11% frame loss + ~1s
            # gaps even on a cool, unthrottled Pi. Only true re-reads
            # (byte-identical everywhere) are skipped.
            if (
                prev is not None
                and fcopy.shape == prev.shape
                and np.array_equal(fcopy[::16, ::16], prev[::16, ::16])
                and np.array_equal(fcopy, prev)
            ):
                continue
            prev = fcopy
            ts = time.time()
            self.buffer.push(ts, fcopy)
            # THE SWING SIGNAL RIDES ALONG HERE. One resize and one
            # absolute difference on a frame already decoded for the
            # person detector, rate-limited to 20 Hz -- so selecting
            # which seconds to upload costs no second decode and no
            # pass over the clip at the end. It runs whether or not a
            # recording is in progress, because a clip starts
            # buffer_seconds before its trigger and a swing in that
            # pre-roll is common. feed() swallows everything: a
            # tracker that raised in here would cost frames, and
            # frames are the one thing this must never cost.
            tracker = getattr(self, "motion", None)
            if tracker is not None:
                tracker.feed(ts, fcopy)
            streamer = getattr(self, "streamer", None)
            if streamer is not None:
                streamer.update_frame(frame)

    def _check_dwell_yield_zones(self) -> None:
        """Say which zone is which, then shout if one stands its dwell
        down for ears that are barred from it.

        THE INDICES ARE ZERO-BASED AND THE OPERATOR'S SCREEN IS NOT.
        The zone editor draws "Zone 1" and "Zone 2" over the boxes,
        counting from one, while confirm_zones and replaces_dwell are
        positions in the list, counting from zero -- so the back tee an
        operator sees as Zone 2 is index 1 in the config, and setting
        the number on the screen stands down the wrong tee. Nothing can
        make those two agree now without renumbering a deployed
        setting, so the rig states the mapping at startup and the
        journal answers the question instead of arithmetic.

        replaces_dwell and confirm_zones are separate lists and nothing
        makes them agree. Name zone 2 in the first and leave it out of
        the second and zone 2 has no trigger at all: the dwell declines
        to fire because the ears have it, and the ears decline to
        confirm because the geography is wrong. The rig looks healthy,
        the log is empty, and every swing on that tee is lost -- the
        one failure mode this whole change exists to prevent, arrived
        at by a different route.

        A warning rather than a refusal: an agent that will not start
        is a camera that records nothing either, and the operator may
        be mid-edit.
        """
        if self._dwell_yields is None:
            return
        for i, b in enumerate(tee_roi.boxes(self.roi)):
            log.info("zone %d (\"Zone %d\" on screen, %s): dwell %s",
                     i, i + 1, b.get("label") or "unlabelled",
                     "STANDS DOWN for the audio trigger"
                     if (self._dwell_yields is True
                         or i in self._dwell_yields) else "fires")
        t = self._audio_trigger
        if t is None:
            return
        cz = t.confirm_zones
        if cz is None:                      # confirms anywhere, fine
            return
        if self._dwell_yields is True:
            orphans = "every zone outside %s" % sorted(cz)
        else:
            missing = sorted(z for z in self._dwell_yields if z not in cz)
            if not missing:
                return
            orphans = "zone(s) %s" % missing
        log.warning(
            "CONFIG: replaces_dwell stands the dwell down for %s, but "
            "confirm_zones=%s bars the audio trigger from firing there "
            "— swings on those tees will be recorded by NOTHING. Add "
            "them to confirm_zones or drop them from replaces_dwell.",
            orphans, sorted(cz))

    def _yield_zone_for(self, stance, roi):
        """The zone whose dwell may stand down, or None to fire it.

        A NAME, so the caller logs WHICH tee stood aside rather than
        that one did -- the zone is attributed before the setting is
        consulted, so even `replaces_dwell: true` has to land on a real
        zone to stand anything down. None means record, which is the
        direction every unknown falls in. Three of them do:

          * a stance this cannot be attributed to a zone -- a detector
            point `tee_roi.which` will not read, or zones that changed
            under the lock between the dwell test and this one;
          * a zone outside the configured list, which is the forward
            tee doing exactly what it is meant to;
          * anything thrown in here at all.

        In each case the dwell fires and the worst outcome is a second
        event for one golfer. The opposite default would hand a tee
        the microphone cannot hear to a trigger that will never fire on
        it, and the rig would record nothing while looking healthy.
        """
        try:
            z = _roi_zone(stance, roi)
            if z is None:
                return None
            if self._dwell_yields is True or z in self._dwell_yields:
                return "zone %d" % z
            return None
        except Exception:  # noqa: BLE001
            return None

    def _cut_at_impact(self, decision: dict) -> None:
        """Cut and upload a swing the audio trigger confirmed.

        Called only when the trigger is LIVE -- in shadow it decides
        and returns -- and only for a crack that had a golfer in the
        named zone. Everything that can go wrong here is logged and
        swallowed: the dwell is still running, still recording and
        still uploading, so a failure costs bandwidth, not a swing.
        """
        if self._copy is None:
            log.info("audio trigger: no copy engine — nothing to cut from")
            return
        _off = None
        try:
            _off, _ = clock_offset()
        except Exception:  # noqa: BLE001
            pass
        self._copy.cut_at_impact(
            self, float(decision["at"]),
            before_sec=float(decision["at"]) - float(decision["start"]),
            after_sec=float(decision["end"]) - float(decision["at"]),
            clock_offset_sec=_off,
        )

    def run(self) -> None:
        cap = open_camera(self.cam_cfg)
        # WHAT SIZE THE FRAMES ACTUALLY ARE, asked of the capture rather
        # than taken from the config: a driver can hand back something
        # other than what it was asked for, and the trigger zones are in
        # pixels. Scaling them against a size we only believe in would
        # put the tee in the wrong part of the picture.
        try:
            import cv2 as _cv2
            _fw = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH) or 0)
            _fh = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            self._open_fps = float(cap.get(_cv2.CAP_PROP_FPS) or 0) or None
            if _fw and _fh:
                self._open_size = (_fw, _fh)
                self._roi_frame = (_fw, _fh)
                log.info("trigger zones will be scaled to %dx%d", _fw, _fh)
                # Zones that arrived before the capture was open went in
                # unscaled. Now that the size is known, redo them.
                if self._roi_raw is not None:
                    with self._roi_lock:
                        self.roi = None
                    self.apply_tee_zones(self._roi_raw)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not read the frame size for zone scaling: %s", exc)
        fps = float(self.cam_cfg.get("fps", 30))
        self.fps = fps
        _preroll = preroll_seconds(self.cfg)
        self.buffer = FrameBuffer(_preroll, fps)
        # SWING SELECTION, off unless asked for. The tracker is cheap
        # but it is not free, and a rig in the field should not change
        # what it uploads because an agent was updated.
        _sw = self.cfg.get("swing_select") or {}
        # (epoch, zone_index) pairs. See person_sightings() for why
        # the zone is kept and seen_times() for the readers that only
        # want the clock.
        self._audio_trigger = None
        # WHERE a live audio trigger may suppress the dwell: None,
        # True for anywhere, or a set of zone indices. Off by default:
        # the dwell is the thing that has always produced clips, and
        # standing it down is the LAST step, taken once shadow mode
        # has shown the ears agree with it. See _dwell_yield_zones for
        # why this is not a boolean.
        self._dwell_yields = _dwell_yield_zones(
            (self.cfg.get("audio_trigger") or {}).get("replaces_dwell", False))
        self._yielded_logged = False
        self._person_seen_at: list = []
        self.motion = None
        if _sw.get("enabled"):
            self.motion = MotionTracker(
                sample_hz=float(_sw.get("sample_hz", 20.0)),
                roi_top=float(_sw.get("roi_top", 0.0)),
                roi_bottom=float(_sw.get("roi_bottom", 1.0)),
                keep_seconds=float(self.buffer_seconds)
                + float(self.cfg.get("max_clip_seconds", 120)) + 60.0,
            )
            try:
                _boxes = tee_roi.boxes(self.roi)
                _size = getattr(self, "_roi_frame", None)
                if _boxes and _size:
                    self.motion.set_zones(_boxes, _size[0], _size[1])
            except Exception as exc:  # noqa: BLE001
                log.debug("swing select: no zones at startup (%s)", exc)
            log.info(
                "swing select: ON (sampling %.0f Hz, measuring %s) — only "
                "the swings go up",
                float(_sw.get("sample_hz", 20.0)),
                ("inside %d trigger zone(s)" % len(self.motion._boxes))
                if self.motion._boxes else
                "the whole frame until trigger zones arrive",
            )
        if _preroll != self.buffer_seconds:
            log.info(
                "pre-roll ring: %.1fs (%d frames), not buffer_seconds=%.1fs "
                "— the copy engine's segment ring is the real pre-roll, and "
                "this one is only for detection and the decode fallback",
                _preroll, self.buffer.max_frames, self.buffer_seconds,
            )
        # Camera-side delivery counters (see _capture_loop).
        self._cap_frames = 0
        self._cap_gaps = 0
        self._cap_worst = 0.0
        self._cap_last = None
        # NB: do NOT cap cv2.setNumThreads here. Capping it to 2
        # starved the mp4v ENCODER — measured 9.9 fps of throughput
        # versus 31 fps with the full pool — and the encoder, not the
        # camera, is the pipeline's bottleneck.

        det_width = int(self.det_cfg.get("detect_width", 320))
        det_fps = float(self.det_cfg.get("fps", 5))
        # Seconds between detection samples. Detection no longer gates
        # the capture rate, so this is purely how often we re-check for
        # a person — independent of the recorded frame rate.
        det_period = 1.0 / max(0.5, det_fps)
        _splits = 0                 # consecutive length-cap splits
        _cooldown_until = 0.0       # automatic triggers paused until this
        dwell_seconds = float(self.det_cfg.get("trigger_dwell_seconds", 2))
        # HOW LONG THE DETECTOR MAY LOSE THEM without the dwell starting
        # over. See the dwell loop below: without this the clock resets
        # on a SINGLE missed detection, and at 5 Hz a ten-second dwell
        # is fifty consecutive sightings with no gaps -- which this
        # config's own notes say does not happen, because "a golfer
        # bending to tee up, turning away, or stepping aside briefly
        # makes the person detector drop out for a few seconds". The
        # STOP condition was built generous for exactly that; the start
        # condition was not.
        dwell_grace = float(
            self.det_cfg.get("trigger_dwell_grace_seconds", 3))
        # THE FALLBACK MATCHES THE SHIPPED CONFIG, which it did not for
        # as long as both have existed: config.example.yaml said 30 and
        # this said 5, so a rig whose config omitted the key recorded
        # six-second clips while its own documentation promised
        # half-minute ones. Nobody hit it because every rig in the
        # field sets the key -- which is exactly why it could sit there.
        # tools/stop_policy_test.py now fails if the two drift again.
        no_person_timeout = float(
            self.det_cfg.get("no_person_timeout_seconds", 12))

        detector = self._build_detector(det_width)

        from .battery import BatteryMonitor

        _batt = BatteryMonitor(self.cfg.get("battery"))
        # The tee measures sharpness INSIDE the tee box: that is the
        # patch whose focus decides whether a golfer is detected at all,
        # and a camera sharp on the horizon but soft on the tee is
        # exactly the failure this is meant to surface.
        # One rectangle, because the meter only needs a patch of the
        # right grass to measure sharpness on — the union of the zones.
        _focus = FocusMeter(roi=tee_roi.union(self.roi) or self.roi)
        self._focus = _focus

        def _hb_extra():
            out = {}
            if (r := _batt.read_averaged()):
                out["battery_voltage"] = r["voltage"]
                out["battery_current_a"] = r["current_a"]
            # Needs no sensor, unlike the above — which is the point, on
            # a rig with no INA226 fitted. See read_throttled().
            if (t := read_throttled()):
                out["throttled"] = json.dumps(t)[:500]
            if (f := _focus.read()):
                out.update(f)
            # What the camera said about its own exposure, if anyone has
            # asked it since the last heartbeat.
            out.update(drain_camera_settings(self))
            out.update(stream_info_fields(self))
            # WHAT THE UPLINK LOOKS LIKE FROM HERE. Read over USB and
            # /proc, so it costs the cellular link nothing and arrives
            # even when that link is too starved to hold an SSH session
            # -- which is the only time anyone wants it.
            out.update(link_info_fields())
            return out or None

        hb = HeartbeatThread(
            self.client, self.heartbeat_seconds, FIRMWARE,
            extra_fn=_hb_extra,
        )
        self._hb = hb
        hb.start()

        streamer = LiveStreamer(
            self.client, on_capture_request=self._request_capture,
            on_focus_mode=self._on_focus_mode,
            on_lens_command=self._on_lens_command,
            on_tee_zones=self.apply_tee_zones,
            on_triggering_state=self._gate.note_backend_state,
        )
        streamer.start()
        self.streamer = streamer

        # Uploads (compress + send) run on a background worker so a slow
        # cellular upload never blocks us from detecting the next group.
        self.uploader = BackgroundUploader(
            self.client,
            compress_kbps=self.upload_bitrate_kbps,
            scale_height=self.upload_scale_height,
            # Under work_dir on purpose: the spool move must stay on one
            # filesystem or the rename fails across devices.
            spool_dir=self.work_dir / "pending",
            spool_max_mb=int(self.cfg.get("upload_spool_max_mb", 2048)),
            spool_max_age_hours=float(
                self.cfg.get("upload_spool_max_age_hours", 24)),
            fresh_timeout=int(self.cfg.get("upload_fresh_timeout", 60)),
            idle_timeout=int(self.cfg.get("upload_idle_timeout", 90)),
            patient_timeout=int(self.cfg.get("upload_patient_timeout", 120)),
            settle_seconds=float(self.cfg.get("upload_settle_seconds", 120)),
            backoff_base=float(self.cfg.get("upload_backoff_base", 20)),
            backoff_max=float(self.cfg.get("upload_backoff_max", 600)),
            chunked=bool(self.cfg.get("upload_chunked", True)),
            chunk_kb=int(self.cfg.get("upload_chunk_kb", 512)),
        )
        self.uploader.start()
        # THE OTHER ENGINE, when config asks for it. None leaves every
        # line below untouched and the agent on the path it has always
        # used; see copyengine.py for why both live here at once.
        self._copy = CopyEngine.from_config(self.cfg, self.work_dir)
        if self._copy is not None:
            self._copy.start()
        # LISTENING FOR THE SWING ITSELF, alongside everything above and
        # changing none of it. Off unless asked for, and shadow by
        # default: it writes the cut it WOULD have made and nothing
        # else, so a day of it can be compared against what the dwell
        # actually recorded before any clip depends on it.
        self._audio_trigger = audiotrigger.build(
            self.cfg, self.work_dir, self,
            video_span=(self._copy.ring.span if self._copy is not None
                        else None),
            on_cut=self._cut_at_impact,
        )
        self._check_dwell_yield_zones()

        # Start draining the camera into the buffer before we begin
        # detecting, so the pre-roll is already populated at trigger.
        capture_thread = threading.Thread(
            target=self._capture_loop, args=(cap,),
            daemon=True, name="tee-capture",
        )
        capture_thread.start()

        dwell = Dwell(dwell_seconds, dwell_grace)
        prime_force_trigger()
        log.info(
            "tee agent running: roi=%s buffer=%.1fs dwell=%.1fs",
            # The ring that exists, not the one config asked for — with
            # the copy engine on, those differ and the journal saying 5s
            # while 2s is allocated is how you lose an afternoon.
            self.roi, _preroll, dwell_seconds,
        )
        try:
            while not self.stopping.is_set():
                snap = self.buffer.snapshot()
                if not snap:
                    time.sleep(0.05)
                    continue
                # Detect on the most recent buffered frame. Runs in this
                # thread, but the capture thread keeps filling the buffer
                # meanwhile, so a slow detect() can't drop frames.
                frame = snap[-1][1]
                now = time.time()
                # Sharpness, off the frame we already have. No-op except
                # every few seconds; rides out on the heartbeat.
                _focus.sample(frame)
                stance = detector.detect(frame)
                with self._roi_lock:
                    _roi_now = self.roi
                in_roi = stance is not None and _in_roi(stance, _roi_now)
                # Manual trigger, for testing the record+upload path
                # without anyone walking into frame:
                #     touch /tmp/golfreelz-trigger
                # Consumed on sight, so it fires exactly once.
                # Operator-requested Capture (admin Cameras page) carries
                # an explicit duration; the SSH sentinel does not.
                # Cheap: at most one new second of audio exists per
                # second of wall clock, and a tick with nothing new is
                # a directory listing. Wrapped because a detector that
                # throws must not take the recorder down with it.
                if self._audio_trigger is not None:
                    try:
                        self._audio_trigger.tick()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("audio trigger: tick failed (%s)", exc)
                pending_secs = self._take_pending_capture()
                forced = _take_force_trigger() or pending_secs is not None
                if not forced and now < _cooldown_until:
                    # Paused after a runaway. An explicit Capture or a
                    # touched sentinel still gets through — the pause is
                    # for the automatic trigger, not for the operator.
                    dwell.reset()
                    time.sleep(det_period)
                    continue

                _was = dwell.first_seen
                _restarts = dwell.restarts
                dwell_met = dwell.update(now, in_roi) if not forced else False
                if dwell.first_seen is not None and _was is None:
                    log.info("person standing in a zone at %s (their feet)"
                             " — recording in %.1fs if they stay",
                             stance, dwell.dwell)
                elif dwell.restarts != _restarts:
                    log.debug("nobody in a zone for over %.1fs before the "
                              "dwell was met — starting the clock over",
                              dwell.grace)
                # THE DWELL STANDS ASIDE WHEN THE EARS WORK. In mode
                # B the audio trigger registers its own events and the
                # green cuts backwards from them, so a dwell trigger on
                # top of that is a second event for the same golfer:
                # green records live for nothing and the event has no
                # clip to show for it.
                #
                # Only while the trigger is LIVE and demonstrably
                # hearing something -- see AudioTrigger.healthy(). A
                # mic that dies, or a camera whose Audio in gets
                # unchecked, puts the dwell straight back in charge
                # rather than quietly ending every recording on this
                # rig. An operator Capture is never suppressed: that
                # click means record now.
                #
                # AND ONLY FOR THE TEES THE EARS REACH. replaces_dwell
                # names zones, because a forward tee in the same frame
                # is out of earshot -- see _dwell_yield_zones.
                _yield_zone = None
                if (dwell_met and not forced
                        and self._audio_trigger is not None
                        and not self._audio_trigger.shadow
                        and self._dwell_yields is not None
                        and self._audio_trigger.healthy()):
                    _yield_zone = self._yield_zone_for(stance, _roi_now)
                if _yield_zone is not None:
                    if not self._yielded_logged:
                        log.info("dwell met in %s, but the audio trigger is "
                                 "live and hearing the course — standing "
                                 "aside and letting it cut the swings",
                                 _yield_zone)
                        self._yielded_logged = True
                    dwell.reset()
                    time.sleep(det_period)
                    continue
                self._yielded_logged = False
                if forced or dwell_met:
                    session_id = str(uuid.uuid4())
                    log.info(
                        "trigger: session=%s%s", session_id,
                        (" (CAPTURE %.0fs)" % pending_secs)
                        if pending_secs is not None
                        else (" (MANUAL)" if forced else ""),
                    )
                    _ack = None
                    self._unregistered_session = None
                    try:
                        _ack = self.client.event_trigger(session_id) or {}
                    except BackendUnreachable as exc:
                        # SILENCE IS NOT A REFUSAL, and this used to be
                        # treated as one. A trigger that times out took
                        # the whole swing with it -- nothing recorded,
                        # nothing spooled, nothing to recover -- on a
                        # rig whose modem re-enumerates every three
                        # minutes. Two swings died this way inside two
                        # minutes on 14 Oct.
                        #
                        # Record it. The clip spools like any other, and
                        # the uploader's _recover_lost_event registers
                        # the session when the link returns, because
                        # /event-trigger is idempotent and takes a
                        # `recover` flag for exactly this. The gate is
                        # the one veto: an operator who PAUSED this
                        # camera still means it.
                        log.warning(
                            "event_trigger got no answer (%s)", exc)
                        if self._gate.should_record_unregistered(session_id):
                            self._unregistered_session = session_id
                            _ack = {}
                    except Exception as exc:
                        # The backend ANSWERED and refused -- a 4xx:
                        # unknown token, camera disabled, not a tee
                        # camera. Recording against that is pointless,
                        # and spooling a clip for it would re-send it
                        # for 24 hours.
                        log.error(
                            "event_trigger refused (%s) — skipping "
                            "session=%s", exc, session_id)
                    if _ack is None:
                        pass        # the trigger never registered
                    # PAUSED MEANS PAUSED, INCLUDING THE UPLOAD.
                    #
                    # The backend has always answered a trigger from a
                    # paused camera with `triggering_disabled`, and its
                    # own comment says the point is "so the Pi doesn't
                    # bother recording / uploading". Nothing out here
                    # ever read it. So pausing stopped events reaching
                    # Production and did not stop this Pi spending the
                    # next minute pushing a 6 MB clip up a 125 KB/s
                    # modem — which is the link an operator is usually
                    # trying to pause it in order to GET.
                    elif _ack.get("triggering_disabled"):
                        log.info(
                            "trigger: session=%s ignored — triggering is "
                            "paused for this camera, so nothing is recorded "
                            "and nothing is uploaded", session_id,
                        )
                    else:
                        # LET THEM GET SET. The event is armed (the green
                        # is already recording off the same trigger); the
                        # tee just does not start rolling yet. An
                        # operator Capture is exempt -- that click means
                        # record now.
                        _not_before = None
                        if pending_secs is None and self.record_delay > 0:
                            log.info(
                                "holding %.0fs before recording — a golfer "
                                "needs at least that long to tee up",
                                self.record_delay,
                            )
                            _not_before = time.time() + self.record_delay
                            while (
                                not self.stopping.is_set()
                                and time.time() < _not_before
                            ):
                                time.sleep(
                                    min(0.2, max(0.0, _not_before - time.time()))
                                )
                            if self.stopping.is_set():
                                break
                        # WHICH ENGINE RECORDS THIS SWING. The copy
                        # engine is asked whether it is ready BEFORE the
                        # clip is committed to it: an unhealthy ring
                        # sends this one swing down the decode path
                        # rather than losing it, and says so once.
                        _copy = getattr(self, "_copy", None)
                        if _copy is not None and _copy.ready():
                            _why = _copy.record_and_upload(
                                self, session_id,
                                self._copy_stop_policy(
                                    detector, no_person_timeout, det_period),
                                # THE GREEN IS WAITING ON THIS. The
                                # decode path sends it from inside
                                # _record_and_upload, which the copy
                                # engine replaces — so without this the
                                # partner is never told the swing ended
                                # and records to its 150s cap, which on
                                # the copy engine is a 269MB clip that
                                # will never cross a cellular link.
                                on_recording_ended=(
                                    lambda: self._signal_event_stop(
                                        session_id)),
                                fixed_seconds=pending_secs,
                                not_before=_not_before,
                            )
                        else:
                            if _copy is not None:
                                log.warning(
                                    "copy engine is not ready (%s) — "
                                    "recording session=%s the old way",
                                    _copy.status(), session_id,
                                )
                            _why = self._record_and_upload(
                                detector, no_person_timeout, det_period,
                                fps, session_id, fixed_seconds=pending_secs,
                                not_before=_not_before,
                            )
                        if _why == "length_cap" and _splits >= self.max_splits:
                            # RUNAWAY GUARD. A split means "the person is
                            # still there", and with a loose ROI that is
                            # true forever — someone on the fairway or the
                            # cart path holds it open, so the tee records
                            # 30s clips back to back with no end. Each one
                            # costs ~23s to compress and ~10s to upload on
                            # a Pi that is also capturing at 50fps: an 88%
                            # duty cycle, and the queue grows.
                            log.warning(
                                "%d consecutive length-cap splits (%.0fs of "
                                "unbroken recording) — pausing triggers for "
                                "%.0fs. If this keeps happening the trigger "
                                "ROI is too large: it is holding on someone "
                                "who is not teeing off. Narrow tee_box_roi "
                                "to the tee box.",
                                _splits, _splits * self.max_clip_seconds,
                                self.split_cooldown,
                            )
                            _splits = 0
                            _cooldown_until = now + self.split_cooldown
                            self.buffer.clear()
                            dwell.reset()
                        elif _why == "length_cap":
                            _splits += 1
                            # A SPLIT, not an ending. Keep the ring buffer
                            # so the next clip opens with its full pre-roll
                            # and OVERLAPS this one — otherwise a swing
                            # that happens to land on the boundary is cut
                            # in half and neither clip can be produced.
                            # The golfer is still in the ROI and has
                            # been for two minutes, so the dwell is
                            # re-armed as already served and the next
                            # clip opens on the following tick. See
                            # Dwell.arm: making them serve it again
                            # would put a hole the length of the dwell
                            # between two clips of the same golfer.
                            dwell.arm(now)
                        else:
                            _splits = 0
                            self.buffer.clear()
                            dwell.reset()
                time.sleep(det_period)
        finally:
            self.stopping.set()
            capture_thread.join(timeout=2)
            cap.release()
            detector.close()
            hb.stop()
            self.uploader.stop(drain_timeout=10.0)
            streamer.stop()
            if getattr(self, "_copy", None) is not None:
                self._copy.stop()
            audiotrigger.stop(getattr(self, "_audio_trigger", None))

    # -----------------------------------------------------------------

    def _signal_event_stop(self, session_id: str) -> None:
        """Tell the backend this event is over, so the paired green
        stops recording. Best-effort and loud about failing: the decode
        path has always done this and the symptom when it does not
        happen is silent and expensive — a green that keeps rolling to
        its cap.

        BOTH RECORDING PATHS COME THROUGH HERE. The copy engine calls it
        from on_recording_ended and the decode loop calls it before its
        upload; they used to be two copies of the same try/except, which
        is two places for the guard below to be missing from.
        """
        if session_id and session_id == self._unregistered_session:
            # There is no event to stop. This session's trigger never
            # reached the backend, so /event-stop would 404 its way
            # through four retries and log a warning that reads like a
            # fault -- and no green is waiting on it, because no green
            # was ever woken.
            log.info(
                "event_stop skipped for %s — its trigger never registered, "
                "so there is no event to end and no green to release",
                session_id,
            )
            return
        try:
            self.client.event_stop(session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("event_stop failed for %s: %s", session_id, exc)

    def _copy_stop_policy(self, detector, no_person_timeout: float,
                          det_period: float):
        """When should a copy-engine clip end? The same answer the
        decode loop gives: once nobody has been in a trigger zone for
        `no_person_timeout`.

        Handed to the engine rather than built into it, because the
        green's reason for stopping is completely different (it waits
        to be told) and neither runner should have to know about the
        other's. Detection is rate-limited to `det_period` here just as
        it is in the decode loop — the engine ticks faster than that so
        it can notice the agent shutting down promptly.
        """
        state = {"last_seen": time.time(), "next_detect": 0.0,
                 "polls": 0, "in_roi": 0, "no_frames": 0}

        def _stop(now: float):
            if now < state["next_detect"]:
                return None
            state["next_detect"] = now + det_period
            state["polls"] += 1
            snap = self.buffer.snapshot()
            if not snap:
                state["no_frames"] += 1
                # No frames is not "nobody is there" — it is a camera
                # problem, and ending the clip early would hide it.
                return None
            stance = detector.detect(snap[-1][1])
            with self._roi_lock:
                _roi = self.roi
            if stance is not None and _in_roi(stance, _roi):
                state["in_roi"] += 1
                state["last_seen"] = now
                # AND REMEMBER THAT WE SAW THEM, which this did not do
                # for as long as the copy engine has existed.
                #
                # swing_select rejects any motion peak with nobody near
                # it -- on a camera aimed down the hole, moving cloud
                # scored a 4.72 "swing" in footage with no golfer in
                # it. The sightings it checks against come from
                # `_person_seen_at`, and only the DECODE loop was
                # filling it. On a copy-engine rig the list stayed
                # empty for the life of the process, and an empty list
                # does not mean "no information" to find_swings -- it
                # means "we looked and nobody was there", which is a
                # distinction it is right to make and was being told
                # wrongly. So every peak that cleared the ratio was
                # thrown out, and swing_select could never return a
                # swing on a copy rig.
                #
                # Measured on hole 8, 20 Oct: 17 sessions, 0 swings,
                # and in the five where peaks did clear the threshold
                # ALL of them were rejected for nobody in the ROI --
                # during sessions a person standing in that same ROI
                # had triggered.
                _seen = getattr(self, "_person_seen_at", None)
                if _seen is not None:
                    # WHICH ZONE, NOT JUST WHEN. A sound can only have
                    # come from the tee its player is standing on, and
                    # this camera watches more than one. Recorded as
                    # (time, zone index) so the audio trigger can ask
                    # "was anybody on the back tee when that crack
                    # happened" rather than "was anybody anywhere".
                    # Older readers want a bare float, so `_seen_times`
                    # keeps giving them one.
                    _seen.append((now, _roi_zone(stance, _roi)))
                return None
            if now - state["last_seen"] >= no_person_timeout:
                return "no_person"
            return None

        _stop.stats = state
        return _stop

    def _record_and_upload(
        self, detector, no_person_timeout: float, det_period: float,
        fps: float, session_id: str, fixed_seconds: float | None = None,
        not_before: float | None = None,
    ) -> str:
        """Persist the pre-roll buffer + keep recording until no person
        is seen for `no_person_timeout` seconds (capped by
        `max_clip_seconds`), then upload. Frames come from the shared
        ring buffer (filled by the capture thread) — this method never
        touches the camera directly, so detection here can't stall
        capture and drop frames."""
        snapshot = self.buffer.snapshot()
        if not snapshot:
            log.warning("buffer empty at trigger; skipping session=%s", session_id)
            return "no_buffer"
        # THE HOLD APPLIES TO THE PRE-ROLL TOO. The buffer has been
        # filling throughout the wait, so keeping it would put the very
        # seconds we decided not to record at the front of the clip.
        # Drop everything older than the start line -- but never all of
        # it: the writer needs a frame to size itself from, and the last
        # couple are the freshest picture there is.
        if not_before is not None:
            _kept = [f for f in snapshot if f[0] >= not_before]
            _dropped = len(snapshot) - len(_kept)
            snapshot = _kept if len(_kept) >= 2 else snapshot[-2:]
            log.info(
                "record: held %.0fs, dropped %d pre-roll frames",
                self.record_delay, _dropped,
            )
        height, width = snapshot[0][1].shape[:2]

        # Wall-clock time of the first frame in the clip (the start of
        # the committed pre-roll). Reported on upload so the backend can
        # align the dual-camera cut to the tee/green real-time delta.
        first_frame_ts = snapshot[0][0]

        # Write at the camera's REAL delivered rate, measured from the
        # (already de-duplicated) pre-roll timestamps. See write_fps_for
        # in common.py for why the measurement is trusted against
        # absolute bounds rather than against the configured rate — the
        # band around nominal is what stamped this camera's clips at 50
        # while it delivered 29.9, and every one of them was jumpy.
        # The delivery meter is handed in as the second opinion — see
        # write_fps_for. Without it, a bursty RTSP stream can defeat the
        # pre-roll median and silently fall back to the config.
        write_fps, _measured, _why = write_fps_for(
            snapshot, fps, delivered=self._delivery.read()[0],
        )
        # Kept for the heartbeat: the card should report the rate clips
        # are REALLY stamped at, not the one the config hoped for.
        self._last_write_fps = write_fps
        log.info(
            "record: nominal_fps=%.1f measured_fps=%s write_fps=%.2f "
            "preroll_frames=%d (%s)",
            fps, f"{_measured:.2f}" if _measured else "—",
            write_fps, len(snapshot), _why,
        )

        clip_path = self.work_dir / f"{session_id}.mp4"
        clip_writer = ClipWriter(clip_path, write_fps, (width, height))
        if not clip_writer.ok:
            log.error("VideoWriter failed to open for %s", clip_path)
            self.uploader.capture_ended()
            return "writer_failed"

        # Kick off parallel audio capture. The WAV runs alongside the
        # video write and gets ffmpeg-muxed into the MP4 at release(),
        # so the uploaded clip has audio. Best-effort: if arecord
        # isn't installed or the device can't be opened, the agent
        # carries on with silent video.
        audio_recorder = build_audio_recorder(self.cfg, self.work_dir)
        audio_recorder.start(session_id)
        # Tell the uploader to get off the wire: one thing at a time.
        self.uploader.capture_started()

        # Hand the pre-roll to the encoder thread, then keep feeding it
        # newly-captured frames. Submitting is a queue put — the drain
        # loop never waits on encoding, so the ring buffer can't wrap
        # and lose frames while a hitch works itself out. The writer
        # thread owns gap filling (see _ClipWriter).
        _cap_at_start = self._cap_frames
        _cap_t_start = time.time()
        for _ts, f in snapshot:
            clip_writer.submit(_ts, f)
        last_written_ts = snapshot[-1][0]
        # Playback length of the pre-roll == its wall-clock span, since
        # gap filling keeps playback time locked to real time.
        preroll_span = snapshot[-1][0] - snapshot[0][0]
        recording_start = time.time()
        last_person_seen = recording_start
        next_det = recording_start + det_period
        # Why this clip ended decides whether the NEXT one keeps its
        # pre-roll — see the caller.
        stop_reason = "stopping"

        while not self.stopping.is_set():
            now = time.time()
            current = self.buffer.snapshot()
            new_frames = [(ts, f) for ts, f in current if ts > last_written_ts]
            for ts, f in new_frames:
                clip_writer.submit(ts, f)
                last_written_ts = ts
            if fixed_seconds is not None:
                # Operator Capture: record exactly this long. The
                # person-presence stop below is skipped entirely — there
                # is nobody in frame, and stopping on that would give a
                # clip a few seconds long instead of the requested one.
                if now - recording_start >= fixed_seconds:
                    log.info(
                        "capture complete (%.1fs requested)", fixed_seconds,
                    )
                    stop_reason = "fixed"
                    break
                time.sleep(0.02)
                continue
            if now - recording_start > self.max_clip_seconds:
                # NOT an error any more. This is the normal way a clip
                # ends when a group is on the tee: cut here, upload a
                # file small enough to survive the link, and re-trigger
                # for the next stretch. The golfer is still in frame, so
                # the next clip starts within a couple of seconds.
                log.info(
                    "clip length cap (%.0fs) reached — splitting here and "
                    "starting a new clip; the golfer is still in the ROI",
                    self.max_clip_seconds,
                )
                stop_reason = "length_cap"
                break
            # Stop-detection on the latest frame, at the detection
            # cadence — off the capture path, so it can't drop frames.
            if now >= next_det and current:
                next_det = now + det_period
                # Keep-alive uses the LENIENT confidence so a bent-over
                # golfer (low YOLO confidence) isn't dropped mid-shot — the
                # strict trigger confidence already gated starting the clip.
                # Fall back for detectors that don't take the override.
                try:
                    stance = detector.detect(
                        current[-1][1], conf_override=self.keepalive_conf
                    )
                except TypeError:
                    stance = detector.detect(current[-1][1])
                if stance is not None:
                    last_person_seen = now
                    # NOTHING SWINGS WITHOUT A PERSON. Kept so the
                    # swing finder can throw out peaks with nobody
                    # near them: on a camera aimed down the hole,
                    # moving cloud scored a 4.72 "swing" in footage
                    # containing no golfer at all.
                    _seen = getattr(self, "_person_seen_at", None)
                    if _seen is not None:
                        # (time, zone) in BOTH loops or neither. The two
                        # paths write the same list and one reader
                        # drains it; a float here and a tuple there is
                        # a crash that only appears on whichever engine
                        # the rig happens to run.
                        _seen.append(
                            (now, _roi_zone(stance, self.tee_box_roi)))
                elif now - last_person_seen > no_person_timeout:
                    log.info(
                        "no person for %.1fs; stopping (recorded %.1fs)",
                        no_person_timeout, now - recording_start,
                    )
                    stop_reason = "no_person"
                    break
            time.sleep(0.02)
        clip_writer.close()
        real_span = last_written_ts - first_frame_ts
        n_frames_written = clip_writer.n_written
        # DOES THE FILE'S OWN HEADER MATCH THE WALL CLOCK? Normally yes,
        # because gap filling holds the previous frame through a stall —
        # and then this is None and the upload is untouched. It speaks
        # up only when the file is genuinely short of frames for the
        # time it covers, which plays fast and jumpy. See reclock_fps.
        real_fps = reclock_fps(n_frames_written, real_span, write_fps)
        if real_fps:
            log.warning(
                "clip is short for its length: %d frames over %.2fs is "
                "%.2f fps, but it is stamped %.2f — re-clocking on upload "
                "so it plays in real time (%.2fs instead of %.2fs)",
                n_frames_written, real_span, real_fps, write_fps,
                real_span, n_frames_written / write_fps,
            )
        _captured = n_frames_written - clip_writer.n_filled
        # Two rates, deliberately separate:
        #   camera  = what the SENSOR delivered to the capture thread
        #   written = what survived into the clip
        # Camera low  -> the camera/driver stalled (thermal, hardware).
        # Camera fine but written low -> our pipeline lost frames
        # (queue overflow) — a software problem. Never guess again.
        log.info(
            "timing: %d frames over %.2fs (%d captured @ %.1f fps eff, "
            "%d gap-filled, stamped %.2f) — %d gap(s), worst %.0f ms | "
            "camera delivered %d frames (%.1f fps, %d gap(s), worst "
            "%.0f ms), queue drops %d, fills skipped %d",
            n_frames_written, real_span, _captured,
            (_captured / real_span) if real_span > 0.5 else 0.0,
            clip_writer.n_filled, write_fps, clip_writer.n_gaps,
            clip_writer.worst_gap * 1000.0,
            self._cap_frames - _cap_at_start,
            (self._cap_frames - _cap_at_start)
            / max(0.001, time.time() - _cap_t_start),
            self._cap_gaps, self._cap_worst * 1000.0,
            clip_writer.n_dropped, clip_writer.n_fills_skipped,
        )
        # Reset camera counters for the next recording.
        self._cap_frames = 0
        self._cap_gaps = 0
        self._cap_worst = 0.0
        # Tell the backend recording is done before starting the
        # (possibly multi-minute) upload, so the paired green Pi —
        # which is polling /event-status — can release its writer at
        # roughly the same wall-clock moment. Best-effort: if the
        # call fails the green Pi will eventually hit its safety cap.
        self._signal_event_stop(session_id)

        # Stop the parallel audio capture and fold the WAV into the
        # MP4. Each step is best-effort; failures leave the video
        # untouched (silent) so the upload still succeeds.
        wav_done = audio_recorder.stop()
        if wav_done is not None:
            # The clip opens with a silent pre-roll (buffered before
            # arecord started), so delay the audio by the pre-roll's
            # playback length to line it up with the trigger moment.
            preroll_seconds = preroll_span
            muxed = mux_audio_into_video(
                clip_path, wav_done,
                # The pre-roll is silent (those frames were buffered
                # before recording began), and an RTSP mic needs its
                # session negotiated on top of that. lead_seconds is
                # zero for an ALSA mic, so this is the same number as
                # before wherever the mic hangs off the Pi.
                audio_delay_seconds=(
                    preroll_seconds + audio_recorder.lead_seconds
                ),
            )
            if muxed:
                # LOG THE DELAY THAT WAS ACTUALLY APPLIED, and both of
                # its parts. Logging preroll_seconds alone understated
                # it by the RTSP connect time, which is the one number
                # you need when a clap is heard off its frame.
                log.info(
                    "audio: muxed into %s (delay=%.2fs = preroll %.2fs "
                    "+ connect %.2fs)",
                    clip_path.name,
                    preroll_seconds + audio_recorder.lead_seconds,
                    preroll_seconds, audio_recorder.lead_seconds,
                )

        size = clip_path.stat().st_size if clip_path.exists() else 0
        # The CLIP's length, not the wall clock to this line. Measuring
        # wall clock here included the encoder drain in clip_writer.close()
        # — 40s on a busy Pi — and reported a 35s clip as 59s, which made
        # the 30s cap look broken when it was working exactly right.
        # Frames written at the write rate IS the clip's playback length.
        _dur = (n_frames_written / write_fps) if write_fps else 0.0
        _wall = time.time() - recording_start
        log.info(
            "recorded %s: %d frames, %.1fs of video, %.1f MB "
            "(%.0fs wall clock incl. encoder drain)",
            clip_path.name, n_frames_written, _dur, size / (1024 * 1024),
            _wall,
        )
        # LENGTH IS WHAT DRIVES SIZE, and length here is however long a
        # person stood in the ROI — on a tee box a waiting group can hold
        # it open for minutes. A clip this long will not clear a weak
        # uplink inside the upload timeout however well it compresses, so
        # say so at the moment it is created rather than leaving it to be
        # inferred from an upload that quietly failed five times.
        if _dur > 60.0:
            log.warning(
                "recorded %.0fs of tee video — that is %.0fx a swing and "
                "will be ~%.0f MB after compression at %d kbps. Expect the "
                "upload to struggle: it needs ~%.0f kbps sustained.",
                _dur, _dur / 12.0,
                self.upload_bitrate_kbps * _dur / 8 / 1024,
                self.upload_bitrate_kbps,
                self.upload_bitrate_kbps * _dur / 180.0,
            )

        # Hand off to the background uploader (compress + send + cleanup)
        # and return to detection AT ONCE, so a slow cellular upload can't
        # make us miss the next group arriving at the tee.
        self.uploader.capture_ended()
        self.uploader.enqueue(
            session_id, clip_path, first_frame_ts, real_fps=real_fps,
        )
        return stop_reason
