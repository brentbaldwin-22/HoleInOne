"""Device-facing endpoints for on-course capture (always-on Pi
cameras). All routes auth via the per-Camera auth_token in the URL —
the admin password and user JWTs are not in scope here. Endpoints
are designed to be called by an unattended Python agent running on a
Raspberry Pi in the field; see docs/field-deployment.md for the
deployment plan and routers/admin.py for the operator-facing CRUD
that mints the tokens these routes accept.

Flow per session (a session covers a whole group's time on the tee
box — potentially many swings — not a single swing):

  1. Tee Pi detects a person in its tee-box ROI for >=2 s.
  2. Tee Pi POSTs /event-trigger with a session_id (UUID4) it
     generated. Backend creates a CameraEvent row and pushes the
     trigger into an asyncio.Queue keyed by the paired green camera's
     id. Tee Pi starts recording from its pre-roll buffer.
  3. Green Pi is sitting in /poll-trigger long-polling. It wakes up
     with the session_id, commits its pre-roll, keeps recording.
  4. Tee Pi keeps recording until its tee box has been empty for
     no_person_timeout_seconds (default 5 s). Then it POSTs
     /event-stop, releases the writer, and uploads. /event-stop sets
     stop_signal_at on the event row.
  5. Green Pi polls /event-status every ~1 s while recording. When
     stop_signal_at is non-null it releases its writer and uploads.
     There is a hard runaway-safety cap (default 10 min) on both Pis
     in case /event-stop never arrives (tee crash, network split).
  6. Both Pis upload their MP4s via /upload-event with the shared
     session_id. As soon as the second clip lands (or the only clip,
     for unpaired-tee single-camera setups), a background thread
     runs the existing _process_long_upload_segments pipeline. Since
     the raw clips can contain multiple swings, the worker first
     auto-detects them via the same audio+motion detector the long-
     upload flow uses and produces one VideoClip per detected swing.

State that lives in-memory only:
  _pending_triggers: dict[camera_id -> asyncio.Queue] — the wake-up
  queue for the long-poll. Lost on backend restart, which is fine:
  the tee Pi will retry; the green Pi reconnects to /poll-trigger on
  its next iteration. Single-instance deployment assumed (Replit).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import (
    Camera,
    CameraEvent,
    Course,
    DeletedCameraSession,
    VideoClip,
)
from ..services import storage, tee_roi, workload
from ..services.video import probe_video_info

log = logging.getLogger("golfreelz.cameras")

router = APIRouter(prefix="/api/cameras", tags=["cameras"])

# In-memory live-stream state. Single uvicorn worker assumed.
# Swap for Redis if you scale to multiple workers.
_LIVE_FRAMES: dict[int, tuple[bytes, datetime]] = {}
_WATCHERS: dict[int, datetime] = {}
_LIVE_LOCK = threading.Lock()
WATCH_TTL = timedelta(seconds=10)
FRAME_TTL = timedelta(seconds=5)

# Where uploaded MP4s land. Same directory the rest of the pipeline
# reads from / writes to. Resolved relative to the backend package
# root, matching the pattern in routers/admin.py.
CLIPS_DIR = Path(__file__).resolve().parents[2] / settings.upload_dir / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

# THE STILL: one JPEG per camera, overwritten, taken whether or not
# anybody is watching. The Cameras page shows the camera's view the
# moment it loads instead of an empty box with a Watch button, and the
# cost is one frame every ten minutes rather than ten a second.
#
# On the instance's own disk, not object storage: it is a 60-100 KB file
# with a lifetime of minutes, and losing it on a redeploy costs nothing
# because the agent is asked for a new one as soon as it polls.
STILLS_DIR = CLIPS_DIR.parent / "stills"
STILLS_DIR.mkdir(parents=True, exist_ok=True)

# How old a still may be before the agent is asked for a fresh one.
# Ten minutes is ~90 frames a day per camera over a dawn-to-dusk season
# — a rounding error against a single uploaded clip — and the view of a
# tee box does not change faster than that except while somebody is
# standing at it, which is what the live view is for.
STILL_MAX_AGE = timedelta(minutes=10)

# A still is one full-resolution frame, encoded at a higher quality than
# the live stream, so it gets more room than the 500 KB live cap.
MAX_STILL_BYTES = 2_000_000


def still_path(camera_id: int) -> Path:
    return STILLS_DIR / f"camera-{int(camera_id)}.jpg"


def still_wanted(cam: Camera) -> bool:
    """Should the agent send a snapshot on this poll?

    THE SERVER DECIDES, not the Pi's own timer, because the file and the
    timer live on different machines with different lifetimes: the
    instance restarts and the disk comes back empty while a Pi that has
    been up for a week still believes it pushed one ten minutes ago. So
    the question asked on every poll is about the file that actually
    exists — which also means the interval is tunable here, with nothing
    to re-provision in the field.
    """
    try:
        st = still_path(cam.id).stat()
    except OSError:
        return True
    age = time.time() - st.st_mtime
    return age >= STILL_MAX_AGE.total_seconds()


def _write_still(camera_id: int, body: bytes) -> None:
    """Write-then-rename, so a reader mid-refresh never gets half a JPEG."""
    final = still_path(camera_id)
    tmp = final.with_name(final.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(final)


# Per-camera wake-up queues. The tee Pi's /event-trigger writes into
# the paired green's queue; the green Pi's /poll-trigger awaits it.
_pending_triggers: dict[int, asyncio.Queue] = {}

# Max bytes per uploaded event clip (~100 MB). 1080p30 for 12 s is
# typically 30-50 MB; this catches malformed uploads / wrong files
# without rejecting legitimate captures.
MAX_EVENT_CLIP_BYTES = 500 * 1024 * 1024

# RESUMABLE UPLOAD. The tee's uplink is a USB cellular modem that
# reboots itself every ~30 seconds under load: alive for ~23s at
# ~125 KB/s, then gone for ~8s while it re-enumerates. A 6.5 MB clip
# needs ~52s of live wire, so it can NEVER complete inside one of the
# modem's lifetimes — every whole-file POST died mid-flight and threw
# away everything it had sent. Partial uploads live here between
# chunks so a clip can cross as many modem lifetimes as it needs.
PARTS_DIR = CLIPS_DIR.parent / "partial"
PARTS_DIR.mkdir(parents=True, exist_ok=True)

# A part with no activity for this long is abandoned (the Pi gave up,
# or the clip was re-encoded and restarted under a fresh size).
PART_MAX_AGE_SECONDS = 24 * 3600

# Upper bound on a single chunk. Generous — the Pi picks the real size
# (512 KB by default, ~4s of wire at the modem's rate).
PART_MAX_CHUNK_BYTES = 8 * 1024 * 1024

# Appends are short and serialized; the read of the incoming body
# happens OUTSIDE this lock so the event loop is never blocked on the
# network while holding it.
_PARTS_LOCK = threading.Lock()
_next_part_prune = 0.0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _utcnow_naive() -> datetime:
    """Match the timezone-naive convention the rest of the schema uses."""
    return datetime.utcnow()


def _get_camera_by_token(token: str, db: Session) -> Camera:
    """Resolve the auth_token in the URL to a Camera, or raise 404 /
    403. Bumps last_seen_at so any successful call counts as a
    heartbeat for free."""
    cam = db.query(Camera).filter(Camera.auth_token == token).first()
    if cam is None:
        raise HTTPException(404, "unknown camera token")
    if not cam.enabled:
        raise HTTPException(403, "camera is disabled")
    cam.last_seen_at = _utcnow_naive()
    return cam


def _queue_for(camera_id: int) -> asyncio.Queue:
    """Lazy-create the wake-up queue for a camera. maxsize=10 keeps a
    dead camera's queue from growing unbounded; old entries are
    dropped at trigger time (we only ever care about the latest
    pending trigger anyway)."""
    q = _pending_triggers.get(camera_id)
    if q is None:
        q = asyncio.Queue(maxsize=10)
        _pending_triggers[camera_id] = q
    return q


def _drain_queue(q: asyncio.Queue) -> None:
    """Clear any stale triggers before pushing a new one — we only
    want the green Pi to react to the most recent event."""
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            return


def _post_process_raw_clip(filename: str) -> None:
    """Re-encode a Pi-uploaded raw clip to browser-friendly H.264 +
    faststart and generate a poster JPG sibling for the production
    preview tile. Both ops are best-effort: failures log a warning
    and leave the original file intact (production reads via cv2,
    which handles mp4v fine, so capture itself is unaffected).

    Designed to be called from a daemon thread so the Pi's
    upload-event HTTP response isn't blocked on a multi-second
    re-encode.

    TAKES THE HEAVY GATE. It used to run alongside the production job
    for the same event — two ffmpeg/OpenCV workloads splitting one core
    while uvicorn waited for a slice, which is how a health check misses
    its five seconds and Render restarts the instance mid-produce. It
    also meant the SAME file could be transcoded twice at once, from
    here and from _process_camera_event_job; now the second call finds
    the marker and costs nothing.
    """
    from ..services.video import extract_thumbnail, transcode_for_web

    src = CLIPS_DIR / filename
    if not src.exists():
        log.warning("post-process: %s vanished before processing", filename)
        return
    workload.deprioritize()
    with workload.heavy(f"post-process {filename}"):
        # Re-checked under the gate: waiting out a produce is exactly
        # the window in which the file can be deleted or replaced.
        if not src.exists():
            log.warning("post-process: %s vanished while queued", filename)
            return
        transcode_for_web(src)
        extract_thumbnail(src)


def _save_event_clip(
    data: bytes,
    event_id: int,
    role: str,
    original_filename: str | None,
) -> str:
    """Write the uploaded MP4 to disk under a recognizable name.
    Returns the bare filename (so it can be stored in
    CameraEvent.{tee,green}_clip_filename without absolute paths)."""
    ext = "mp4"
    if original_filename and "." in original_filename:
        candidate = original_filename.rsplit(".", 1)[-1].lower()
        if candidate in ("mp4", "mov", "m4v", "webm"):
            ext = candidate
    fname = f"event-{event_id}-{role}-{secrets.token_hex(4)}.{ext}"
    out_path = CLIPS_DIR / fname
    out_path.write_bytes(data)
    return fname


def _adopt_event_clip(
    src: Path,
    event_id: int,
    role: str,
    original_filename: str | None,
) -> str:
    """Move an already-complete file into CLIPS_DIR under the standard
    name. Same result as _save_event_clip, minus reading the whole clip
    into memory — the resumable path already has it on disk, and
    PARTS_DIR is a sibling of CLIPS_DIR so the rename is atomic."""
    ext = "mp4"
    if original_filename and "." in original_filename:
        candidate = original_filename.rsplit(".", 1)[-1].lower()
        if candidate in ("mp4", "mov", "m4v", "webm"):
            ext = candidate
    fname = f"event-{event_id}-{role}-{secrets.token_hex(4)}.{ext}"
    src.replace(CLIPS_DIR / fname)
    return fname


# ---------------------------------------------------------------------
# Resumable upload state
# ---------------------------------------------------------------------


def _part_paths(camera_id: int, upload_id: str) -> tuple[Path, Path]:
    """Where this camera's in-progress upload lives. Namespaced by
    camera id so the tee and green can share a session_id as the
    upload_id without colliding."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", upload_id or "")[:80]
    if not safe:
        raise HTTPException(400, "upload_id is required")
    base = PARTS_DIR / f"cam{int(camera_id)}-{safe}"
    return base.with_suffix(".part"), base.with_suffix(".meta")


def _part_received(part: Path, meta: Path, total_size: int) -> int:
    """Bytes already banked for this upload, or 0 if what's on disk
    belongs to a different file.

    The size check matters: when the link keeps failing, the uploader
    re-encodes the clip at a lower bitrate and retries. Those are
    different bytes under the same session_id, so resuming on top of
    them would splice two encodes into one corrupt MP4.
    """
    if not part.exists():
        return 0
    prev = None
    try:
        prev = json.loads(meta.read_text()).get("total_size")
    except (OSError, ValueError, AttributeError):
        prev = None
    if total_size > 0 and prev is not None and int(prev) != int(total_size):
        log.info(
            "cameras: %s restarted — clip is now %d bytes, not %d (re-encoded)",
            part.name, int(total_size), int(prev),
        )
        part.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        return 0
    try:
        return part.stat().st_size
    except OSError:
        return 0


def _prune_parts() -> None:
    """Drop abandoned partials so a fortnight of dead uploads can't
    fill the disk. Cheap, and rate-limited to once a minute."""
    global _next_part_prune
    now = time.time()
    if now < _next_part_prune:
        return
    _next_part_prune = now + 60.0
    try:
        for p in PARTS_DIR.iterdir():
            try:
                if now - p.stat().st_mtime > PART_MAX_AGE_SECONDS:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def _resolve_event_role(
    cam: Camera, session_id: str, db: Session,
) -> tuple[CameraEvent, str]:
    """Find the event this upload belongs to and which side it is."""
    event = db.query(CameraEvent).filter(
        CameraEvent.session_id == session_id,
    ).first()
    if event is None:
        raise HTTPException(404, "no event for that session_id; trigger first")
    if cam.id == event.tee_camera_id:
        return event, "tee"
    if event.green_camera_id is not None and cam.id == event.green_camera_id:
        return event, "green"
    raise HTTPException(403, "this camera is not part of that event")


def _record_event_clip(
    event: CameraEvent,
    role: str,
    fname: str,
    recording_started_at: float | None,
    db: Session,
) -> dict:
    """Attach a stored clip to its event, advance the status, and kick
    off post-processing. Shared by the whole-file and resumable upload
    paths so they cannot drift apart."""
    # First-frame wall-clock time (epoch seconds from the Pi). Stored
    # per role so the dual-camera cut can align the green clip to the
    # tee cut's real-world moment instead of trusting frame indices.
    started_dt = None
    if recording_started_at is not None:
        try:
            started_dt = datetime.utcfromtimestamp(float(recording_started_at))
        except (ValueError, OverflowError, OSError):
            started_dt = None
    if role == "tee":
        event.tee_clip_filename = fname
        if started_dt is not None:
            event.tee_recording_started_at = started_dt
    else:
        event.green_clip_filename = fname
        if started_dt is not None:
            event.green_recording_started_at = started_dt

    # Decide the new status + whether we're ready to process. A paired
    # event needs both clips; an unpaired tee event needs only its own.
    has_tee = event.tee_clip_filename is not None
    has_green = event.green_clip_filename is not None
    is_paired = event.green_camera_id is not None
    ready_to_process = (has_tee and has_green) if is_paired else has_tee
    if ready_to_process:
        event.status = "paired_uploaded"
    elif has_tee:
        event.status = "tee_uploaded"
    elif has_green:
        # Unusual: green came in before tee. Hold; the tee upload
        # (which is mandatory) will flip the status.
        event.status = "tee_uploaded"
    db.commit()

    log.info(
        "cameras: upload-event event=%s role=%s file=%s status=%s",
        event.id, role, fname, event.status,
    )

    # Re-encode + thumbnail in the background so the admin preview
    # works. Doesn't block the Pi's HTTP response. Runs in parallel
    # with the production job for paired events; both touch the file
    # via os-atomic rename, so cv2 reading from one inode while
    # ffmpeg writes a new one is fine.
    threading.Thread(
        target=_post_process_raw_clip,
        args=(fname,),
        daemon=True,
        name=f"post-process-{event.id}-{role}",
    ).start()

    if ready_to_process:
        threading.Thread(
            target=_process_camera_event_job,
            args=(event.id,),
            daemon=True,
            name=f"camera-event-{event.id}",
        ).start()

    return {
        "ok": True,
        "event_id": event.id,
        "role": role,
        "filename": fname,
        "status": event.status,
        "ready_to_process": ready_to_process,
    }


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------


def battery_status(
    voltage: float | None,
    current_a: float | None,
    updated_at=None,
) -> dict | None:
    """Coarse battery readout for the dashboard from one voltage/current
    sample. LiFePO4's discharge curve is famously flat mid-charge, so
    the percent is honest-but-approximate — the buckets are what matter
    (do I need to bring the charger?). None when no telemetry exists."""
    if voltage is None:
        return None
    v = float(voltage)
    # Resting-ish voltage -> approx % for a 4S LiFePO4 under light load.
    curve = [
        (13.35, 100), (13.25, 90), (13.18, 80), (13.10, 65),
        (13.05, 55), (13.00, 40), (12.90, 30), (12.80, 20),
        (12.60, 10), (12.10, 5),
    ]
    pct = 2
    for thresh, p in curve:
        if v >= thresh:
            pct = p
            break
    level = (
        "full" if pct >= 80
        else "good" if pct >= 40
        else "low" if pct >= 20
        else "critical"
    )
    watts = (
        round(v * float(current_a), 1)
        if current_a is not None and float(current_a) > 0.05
        else None
    )
    est_days = None
    cap = float(settings.battery_capacity_wh or 0)
    if cap > 0:
        draw_w = watts if (watts and watts > 1.0) else 7.0
        est_days = round(
            (cap * pct / 100.0)
            / (draw_w * max(1.0, float(settings.battery_active_hours))),
            1,
        )
    return {
        "voltage": round(v, 2),
        "current_a": round(float(current_a), 2) if current_a is not None else None,
        "watts": watts,
        "percent": pct,
        "level": level,
        "est_days": est_days,
        "low": bool(v < float(settings.battery_low_volts)),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def stream_status(info, updated_at=None) -> dict | None:
    """What the camera is sending, and whether the three numbers agree.

    THE DISAGREEMENT IS THE POINT. `config_fps` is what clips get
    stamped at; `delivered_fps` is what the camera actually hands over.
    A clip is stamped at the first and filled from the second, so when
    they differ it plays fast — which is exactly the fault this readout
    exists to make visible before it reaches a golfer's inbox.
    """
    if not isinstance(info, dict):
        return None
    cfg = info.get("config_fps")
    got = info.get("delivered_fps")
    mismatch = None
    if cfg and got and cfg > 0:
        off = abs(got - cfg) / cfg
        if off >= 0.1:
            mismatch = (
                f"delivering {got:.1f} fps but configured for {cfg:.0f} — "
                f"{'slower' if got < cfg else 'faster'} by "
                f"{off * 100:.0f}%"
            )
    return {
        "open_w": info.get("open_w"),
        "open_h": info.get("open_h"),
        "open_fps": info.get("open_fps"),
        "config_fps": cfg,
        "delivered_fps": got,
        "mismatch": mismatch,
        # The camera's own answer, when somebody has asked it.
        "profile": info.get("profile"),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def exposure_status(settings_blob, updated_at=None) -> dict | None:
    """The camera's own exposure reading, for the dashboard. None until
    something has asked it.

    THE SHAPE IS THE AGENT'S, not ours: which keys a Hanwha exposes
    varies by firmware, so the agent reports what it found and this does
    no more than flatten the useful bits out front and keep the raw dump
    behind them. `ok: false` with an error is a perfectly good answer —
    it means the camera refused or we named a key it does not have, and
    an operator needs to see that rather than a blank.
    """
    if not isinstance(settings_blob, dict):
        return None
    values = settings_blob.get("values") or {}
    keys = settings_blob.get("keys") or {}
    return {
        "ok": bool(settings_blob.get("ok")),
        "error": settings_blob.get("error"),
        # Flattened for the card: the mode, the fixed value if there is
        # one, the slow-end cap if there is one, and WDR.
        "mode": values.get("mode_key"),
        "speed": values.get("value_key"),
        "slow_limit": values.get("slow_limit_key"),
        "wdr": values.get("wdr_key"),
        # Which key each of those came from, because on an unfamiliar
        # firmware that is the thing worth knowing.
        "keys": keys,
        # What was last sent, when this reading followed a change.
        "sent": settings_blob.get("sent"),
        "raw": settings_blob.get("raw") or {},
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def focus_status(
    score: float | None,
    brightness: float | None,
    updated_at=None,
    best: float | None = None,
    focus_seconds: int = 0,
) -> dict | None:
    """Focus readout for the dashboard. None when nothing reported yet.

    NO PASS/FAIL, deliberately. The score is a variance of the
    Laplacian, and its absolute value depends entirely on what the
    camera is pointed at -- a tree line scores an order of magnitude
    above bare turf at identical sharpness. A threshold would be wrong
    on half the cameras the day it shipped.

    What IS reportable is when the number cannot be trusted: the
    measurement needs light, and below ~40 or above ~220 mean luma the
    region is crushed or blown and the score says more about exposure
    than about the lens. That gets flagged; sharpness itself is left for
    a human to read against the same camera yesterday.
    """
    if score is None:
        return None
    b = float(brightness) if brightness is not None else None
    exposure = None
    if b is not None:
        if b < 40:
            exposure = "dark"
        elif b > 220:
            exposure = "bright"
    return {
        "score": round(float(score), 1),
        "brightness": round(b, 1) if b is not None else None,
        # True when exposure makes the score unreliable — fix the light
        # before reading anything into the sharpness.
        "unreliable": exposure is not None,
        "exposure": exposure,
        "updated_at": updated_at.isoformat() if updated_at else None,
        # Peak of the current focus session, and how long that session
        # has left. Both empty outside a session.
        "best": round(float(best), 1) if best is not None else None,
        "focus_seconds": int(focus_seconds or 0),
    }


@router.post("/{token}/heartbeat")
def heartbeat(
    token: str,
    firmware_version: str | None = Form(None),
    battery_voltage: float | None = Form(None),
    battery_current_a: float | None = Form(None),
    focus_score: float | None = Form(None),
    focus_brightness: float | None = Form(None),
    camera_settings: str | None = Form(None),
    stream_info: str | None = Form(None),
    lens_info: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Cheap keepalive the Pi calls every ~60 s. Touches last_seen_at
    so the admin UI can flag offline cameras. Optionally captures the
    Pi's firmware/git-commit version for diagnostics, plus battery
    telemetry (INA226) on battery-powered rigs."""
    cam = _get_camera_by_token(token, db)
    if firmware_version:
        cam.firmware_version = firmware_version.strip()[:40]
    # Sharpness, measured by the agent on a frame it already had. Stored
    # unconditionally rather than alerted on: unlike battery voltage
    # there is no threshold that means the same thing on two cameras
    # pointed at different scenes, so this is for a human to read.
    if focus_score is not None:
        cam.focus_score = float(focus_score)
        cam.focus_brightness = (
            float(focus_brightness) if focus_brightness is not None else None
        )
        cam.focus_updated_at = _utcnow_naive()
        # The peak of the session, kept because a live number cannot show
        # it: turning the ring, by the time you know you are past the
        # best you are past it. Only tracked while focus mode is armed --
        # otherwise the "best" would be whatever the light did at noon
        # three weeks ago and would never fall.
        if _focus_remaining(cam.id) and (
            cam.focus_best is None or cam.focus_score > cam.focus_best
        ):
            cam.focus_best = cam.focus_score
            cam.focus_best_at = cam.focus_updated_at
    if battery_voltage is not None:
        cam.battery_voltage = float(battery_voltage)
        cam.battery_current_a = (
            float(battery_current_a) if battery_current_a is not None else None
        )
        cam.battery_updated_at = _utcnow_naive()
        _thr = float(settings.battery_low_volts)
        if cam.battery_voltage >= _thr + 0.15:
            # Recharged / swapped — re-arm the one-shot alert.
            cam.battery_low_notified_at = None
        elif (
            cam.battery_voltage < _thr
            and cam.battery_low_notified_at is None
        ):
            cam.battery_low_notified_at = _utcnow_naive()
            try:
                from ..services import notifications

                _name = cam.name or f"camera #{cam.id}"
                notifications.send_email(
                    settings.admin_alert_email or None,
                    f"GolfReelz: low battery on {_name}",
                    (
                        f"{_name} (hole {cam.assigned_hole}, "
                        f"{cam.assigned_role}) reported "
                        f"{cam.battery_voltage:.2f}V — below the "
                        f"{_thr:.2f}V alert threshold. Plan a battery "
                        "swap in the next day or two."
                    ),
                )
                log.info(
                    "battery: LOW alert for camera %s (%.2fV)",
                    cam.id, cam.battery_voltage,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("battery: low alert send failed: %s", exc)
    # THE CAMERA'S OWN WORDS, not the command we sent. The agent asks
    # the camera what its exposure is after every change (and on a bare
    # read), and this is that answer — stored whole, including the raw
    # key/value dump, because a setting we have no name for yet is still
    # worth an operator being able to see.
    if camera_settings:
        try:
            parsed = json.loads(camera_settings)
            if isinstance(parsed, dict):
                cam.camera_settings = parsed
                cam.camera_settings_at = _utcnow_naive()
        except (ValueError, TypeError) as exc:
            log.warning(
                "cameras: camera %s sent unparseable settings: %s",
                cam.id, exc,
            )
    # WHAT THE LENS SAID WHEN ASKED WHERE IT IS — which on this model is
    # expected to be nothing, since it declares no absolute position.
    # Merged BESIDE the counted position rather than over it: the count
    # is the backend's and the reading is the camera's, and the card
    # prefers the reading only when there actually is one.
    if lens_info:
        try:
            parsed = json.loads(lens_info)
            if isinstance(parsed, dict):
                merged = dict(cam.lens_zoom or {})
                parsed["at"] = _utcnow_naive().isoformat()
                merged["reported"] = parsed
                cam.lens_zoom = merged
        except (ValueError, TypeError) as exc:
            log.warning(
                "cameras: camera %s sent unparseable lens info: %s",
                cam.id, exc,
            )
    if stream_info:
        try:
            parsed = json.loads(stream_info)
            if isinstance(parsed, dict):
                # MERGED, not replaced. The cheap fields ride every
                # heartbeat; the camera's own profile arrives once when
                # an operator asks for it and should survive the next
                # heartbeat that does not carry one.
                merged = dict(cam.stream_info or {})
                merged.update({k: v for k, v in parsed.items()
                               if v is not None or k != "profile"})
                if parsed.get("profile"):
                    merged["profile"] = parsed["profile"]
                cam.stream_info = merged
                cam.stream_info_at = _utcnow_naive()
        except (ValueError, TypeError) as exc:
            log.warning(
                "cameras: camera %s sent unparseable stream info: %s",
                cam.id, exc,
            )
    db.commit()
    return {
        "ok": True,
        "camera_id": cam.id,
        "enabled": cam.enabled,
        # Read at startup, before the status poll has run once: a tee
        # agent whose card carries no zones can still come up on the
        # ones drawn in the app.
        "tee_box_roi": (
            {"boxes": tee_roi.boxes(cam.tee_box_roi),
             "frame": (lambda f: {"w": f[0], "h": f[1]} if f else None)(
                 tee_roi.frame_size(cam.tee_box_roi))}
            if tee_roi.boxes(cam.tee_box_roi) else None
        ),
        # Pi can read this to skip its person-detection loop entirely
        # while paused. Older agents that ignore it are still covered:
        # /event-trigger refuses to create events when this is False.
        "triggering_enabled": cam.triggering_enabled,
        "assigned_role": cam.assigned_role,
        "course_id": cam.course_id,
        "assigned_hole": cam.assigned_hole,
        "paired_with_camera_id": cam.paired_with_camera_id,
        "server_time": _utcnow_naive().isoformat(),
    }


# Operator-requested captures, camera_id -> seconds. Set by the admin
# Capture button, consumed by the tee Pi's next watch-status poll.
# In-memory on purpose: a request that doesn't reach a camera within a
# poll interval is stale and should evaporate, not queue up.
_CAPTURE_LOCK = threading.Lock()
_CAPTURE_REQUESTS: dict[int, int] = {}

# ── lens commands for IP cameras ──────────────────────────────────────
# A QUEUE, not a single slot like the capture request above. Aiming a
# lens is a series of nudges -- three taps of zoom-in then a focus --
# and a single slot would silently drop two of them. Ordering matters
# too: Simple Focus after a zoom means something different from before
# it.
#
# Capped, because the queue grows from the operator's clicks and drains
# only when an agent polls. A camera whose Pi is offline would otherwise
# collect every press until the process restarts, then fire all of them
# at a lens nobody is watching.
_LENS_LOCK = threading.Lock()
_LENS_QUEUES: dict[int, list[dict]] = {}
LENS_QUEUE_MAX = 24


def request_lens(camera_id: int, op: str, amount: int = 0,
                 params: dict | None = None, repeat: int = 1) -> int:
    """Queue one camera command. Returns the queue depth after adding.

    `amount` is the step size for a zoom or focus nudge. `params`
    carries the commands that are not a nudge — an exposure change has
    named values ("manual", "1/500") rather than a step — and rides the
    same queue so ordering still holds across both kinds.

    `repeat` is how many times to apply that same step. The lens accepts
    three magnitudes and nothing between them, so a move of any size is
    a run of identical nudges; sending the run as one queued command
    rather than thirty keeps the queue (and the poll that drains it)
    about the operator's gestures instead of about the lens's gearing.
    """
    with _LENS_LOCK:
        q = _LENS_QUEUES.setdefault(int(camera_id), [])
        if len(q) >= LENS_QUEUE_MAX:
            # Drop the OLDEST. A stale zoom from two minutes ago is worth
            # less than the one just clicked.
            del q[0]
        cmd = {"op": op, "amount": int(amount)}
        if repeat and int(repeat) > 1:
            cmd["repeat"] = int(repeat)
        if params:
            cmd["params"] = {str(k): str(v) for k, v in params.items()}
        q.append(cmd)
        return len(q)


_FOCUS_LOCK = threading.Lock()
_FOCUS_UNTIL: dict[int, float] = {}


def request_focus_mode(camera_id: int, seconds: int) -> None:
    """Arm focus mode: the agent measures and reports far more often, so
    a ring can be turned against a live number.

    In-memory and time-boxed, like the capture request beside it. A Pi
    that never picks it up should not stay in a high-rate mode forever,
    and an operator who drives away mid-adjustment should not leave one
    hammering the backend -- so it expires on its own rather than
    needing to be switched off.
    """
    with _FOCUS_LOCK:
        _FOCUS_UNTIL[int(camera_id)] = time.time() + max(1, int(seconds))


def extend_focus_mode(camera_id: int, seconds: int) -> bool:
    """Keep focus mode armed for at least `seconds` more, and say whether
    it had been off.

    For the lens controls, which arm it as a side effect: pressing a
    focus nudge is the whole reason anyone wants the fast readout, so
    asking for it separately was a step that only ever got skipped.

    NEVER SHORTENS. A short top-up after a nudge must not cut a window
    somebody armed deliberately, so the later of the two deadlines wins.

    The return value exists for the session peak. The peak is reset when
    a focus session STARTS and not on every nudge inside one -- resetting
    it per press would wipe the number that tells you that you have
    already gone past the best.
    """
    now = time.time()
    until = now + max(1, int(seconds))
    with _FOCUS_LOCK:
        current = _FOCUS_UNTIL.get(int(camera_id), 0.0)
        was_off = current <= now
        if until > current:
            _FOCUS_UNTIL[int(camera_id)] = until
    return was_off


def _focus_remaining(camera_id: int) -> int:
    """Seconds of focus mode left for this camera, 0 when off."""
    with _FOCUS_LOCK:
        until = _FOCUS_UNTIL.get(int(camera_id))
        if not until:
            return 0
        left = until - time.time()
        if left <= 0:
            _FOCUS_UNTIL.pop(int(camera_id), None)
            return 0
        return int(left)


def clear_focus_mode(camera_id: int) -> None:
    with _FOCUS_LOCK:
        _FOCUS_UNTIL.pop(int(camera_id), None)


def request_capture(camera_id: int, seconds: int) -> None:
    """Queue a one-shot capture for a camera. Called from the admin
    router; delivered on the camera's next watch-status poll."""
    with _CAPTURE_LOCK:
        _CAPTURE_REQUESTS[camera_id] = int(seconds)


@router.get("/{token}/watch-status")
def watch_status(token: str, db: Session = Depends(get_db)):
    """Pi polls this. If watching=true, Pi should push live frames.

    Also the delivery channel for an operator-requested capture. The tee
    Pi already polls this every few seconds for the live view, so riding
    along here means no new endpoint on the device and no extra traffic.
    `capture_seconds` is CONSUMED on read — one poll, one capture.
    """
    cam = _get_camera_by_token(token, db)
    with _LIVE_LOCK:
        last = _WATCHERS.get(cam.id)
        watching = bool(last and _utcnow_naive() - last < WATCH_TTL)
    with _CAPTURE_LOCK:
        capture_seconds = _CAPTURE_REQUESTS.pop(cam.id, None)
    # Drained whole, like capture: one poll delivers every pending nudge
    # in the order they were clicked.
    with _LENS_LOCK:
        lens_commands = _LENS_QUEUES.pop(cam.id, [])
    if capture_seconds:
        log.info(
            "cameras: delivering capture request to camera %s (%ss)",
            cam.id, capture_seconds,
        )
    return {
        "watching": watching,
        "capture_seconds": capture_seconds,
        # THE TRIGGER ZONES, on every poll rather than on a change. The
        # agent cannot ask "what changed since?" — it can restart, or
        # come back from a curfew, or have been provisioned with a stale
        # card — so the current answer every second is simpler than any
        # protocol for telling it only once, and it is a few hundred
        # bytes. Null means the camera has none set and the agent keeps
        # whatever its own config gave it.
        "tee_box_roi": (
            {"boxes": tee_roi.boxes(cam.tee_box_roi),
             "frame": (lambda f: {"w": f[0], "h": f[1]} if f else None)(
                 tee_roi.frame_size(cam.tee_box_roi))}
            if tee_roi.boxes(cam.tee_box_roi) else None
        ),
        # Empty list on almost every poll; only non-empty right after an
        # operator touches the zoom or focus controls.
        "lens_commands": lens_commands,
        # Not consumed on read, unlike the capture above: this is a mode
        # the agent stays in, not a one-shot, and it has to survive every
        # poll until it expires.
        "focus_seconds": _focus_remaining(cam.id),
        # THE SNAPSHOT ASK. True when the picture the Cameras page would
        # show is missing or stale; the agent answers with one frame to
        # /still and goes back to sleep. Not consumed on read — it stays
        # true until a frame actually lands, so a failed push retries.
        "still_wanted": still_wanted(cam),
    }


# THE TWO FRAME ENDPOINTS DO THEIR DATABASE WORK IN A THREAD.
#
# An `async def` runs ON the event loop, and every line of it that is
# not awaited runs there too -- including a SQLAlchemy query, which is a
# network round trip to Postgres with a pre-ping in front of it. While
# that is in flight nothing else in the process gets served, /health
# included, and Render kills an instance that cannot answer /health in
# five seconds.
#
# One round trip is a millisecond and harmless. These two are the ones
# that repeat: the live view pushes TEN FRAMES A SECOND while an
# operator watches, each one previously taking a trip to the database on
# the loop before it would accept the bytes. A connection pool that goes
# briefly dry (produce holds sessions for minutes) turns each of those
# into a ten-second `pool_timeout` wait -- on the event loop, with the
# health check behind it.
#
# So the session is opened inside the thread rather than injected by
# Depends: the dependency would hold a pooled connection for the whole
# request, body upload included, which on a cellular modem that dies
# every thirty seconds is a connection held for as long as the modem
# takes to come back.


def _camera_id_for_body(token: str) -> int:
    """Resolve the token and note the camera was heard from. Thread-only."""
    db = SessionLocal()
    try:
        cam = _get_camera_by_token(token, db)
        cam_id = cam.id
        db.commit()
        return cam_id
    finally:
        db.close()


@router.post("/{token}/live-frame")
async def post_live_frame(token: str, request: Request):
    """Pi POSTs JPEG bytes as the request body."""
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty frame")
    if len(body) > 500_000:
        raise HTTPException(413, "frame too large (max 500KB)")
    cam_id = await asyncio.to_thread(_camera_id_for_body, token)
    with _LIVE_LOCK:
        _LIVE_FRAMES[cam_id] = (body, _utcnow_naive())
    return {"ok": True}


@router.post("/{token}/still")
async def post_still(token: str, request: Request):
    """The periodic snapshot: JPEG bytes in the body, kept on disk.

    Separate from /live-frame because they have opposite lifetimes. A
    live frame is worth five seconds and is never written down; this one
    is the camera's view until the next one replaces it, and has to
    survive the operator closing the page.

    The size is checked against the declared length before a byte is
    read, so a camera that has gone wrong cannot make the process hold
    two megabytes before being told no.
    """
    try:
        declared = int(request.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > MAX_STILL_BYTES:
        raise HTTPException(413, "still too large (max 2MB)")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty frame")
    if len(body) > MAX_STILL_BYTES:
        raise HTTPException(413, "still too large (max 2MB)")

    def _store() -> int:
        db = SessionLocal()
        try:
            cam = _get_camera_by_token(token, db)
            _write_still(cam.id, body)
            cam.still_at = _utcnow_naive()
            db.commit()
            return cam.id
        finally:
            db.close()

    cam_id = await asyncio.to_thread(_store)
    log.info("cameras: still stored for camera %s (%d bytes)", cam_id, len(body))
    return {"ok": True, "bytes": len(body)}


@router.post("/{token}/event-trigger")
async def event_trigger(
    token: str,
    session_id: str = Form(...),
    recover: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Tee-Pi-only: signals that a person was detected on the tee box
    and the Pi is about to start uploading. Creates the CameraEvent
    row and wakes up the paired green Pi (if any) so it can commit
    its pre-roll buffer too.

    Returns immediately so the tee Pi can keep its capture loop
    responsive. session_id is the Pi's UUID4 for this event; the
    matching /upload-event call reuses it.
    """
    cam = _get_camera_by_token(token, db)
    if cam.assigned_role != "tee":
        raise HTTPException(400, "event-trigger is only valid for tee cameras")

    # Operator kill-switch: camera is online but triggering is paused
    # (e.g. powered on indoors for testing). Touch last_seen_at (done
    # in _get_camera_by_token) but don't create an event — return a
    # benign signal so the Pi doesn't bother recording / uploading.
    if not cam.triggering_enabled:
        db.commit()  # persist the last_seen_at bump
        return {"ok": True, "triggering_disabled": True, "event_id": None}

    sid = (session_id or "").strip()[:80]
    if not sid:
        raise HTTPException(400, "session_id is required")

    # A DELETION IS FINAL. Recovery exists for triggers that were lost
    # (backend mid-deploy, modem between lives) and it cannot tell that
    # case from an event the operator deliberately removed — both are
    # simply a missing row. Without this check the delete is undone as
    # soon as the Pi's backlog drains, which is how sixteen deleted
    # events came back as 502-518. Only recovery is blocked: a genuine
    # live trigger reusing the id would still be honoured.
    if recover and db.query(DeletedCameraSession).filter(
        DeletedCameraSession.session_id == sid,
    ).first() is not None:
        db.commit()  # keep the last_seen_at bump
        log.info("cameras: refused to recover deleted session %s", sid)
        raise HTTPException(
            403, "this session was deleted; not part of that event",
        )

    # Idempotency: if this session_id has already been registered (e.g.
    # the Pi retried because the first response was lost), return the
    # existing event row instead of failing on the unique constraint.
    existing = db.query(CameraEvent).filter(CameraEvent.session_id == sid).first()
    if existing is not None:
        return {
            "ok": True,
            "event_id": existing.id,
            "session_id": existing.session_id,
            "duplicate": True,
        }

    event = CameraEvent(
        session_id=sid,
        tee_camera_id=cam.id,
        green_camera_id=cam.paired_with_camera_id,
        course_id=cam.course_id,
        hole_number=cam.assigned_hole,
        status="triggered",
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Wake the paired green Pi if there is one. asyncio.Queue.put_nowait
    # is fire-and-forget — if no green is currently long-polling, the
    # message sits in the queue for the next iteration.
    #
    # ...unless this is a RECOVERY. A trigger can be lost — the backend
    # was mid-deploy, the modem was between lives — and the tee then
    # records and uploads a clip for a session the server never heard
    # of. Re-registering the session lets that footage land instead of
    # being discarded, but the swing is minutes in the past: waking the
    # green now would only have it record an empty tee box and file it
    # under a session whose green half is long gone.
    partner_id = None if recover else cam.paired_with_camera_id
    if recover:
        # No green half is coming, so don't leave the event waiting for
        # one — the tee-only path can carry it.
        event.green_camera_id = None
        db.commit()
        log.warning(
            "cameras: recovered lost trigger session=%s as event=%s "
            "(tee-only; the green half cannot be recovered)", sid, event.id,
        )
    if partner_id is not None:
        q = _queue_for(partner_id)
        _drain_queue(q)
        try:
            q.put_nowait(
                {
                    "session_id": sid,
                    "event_id": event.id,
                    "triggered_at": event.triggered_at.isoformat()
                    if event.triggered_at
                    else None,
                    "tee_camera_id": cam.id,
                    "hole_number": cam.assigned_hole,
                }
            )
        except asyncio.QueueFull:
            log.warning(
                "cameras: trigger queue full for camera %s, dropping; green "
                "Pi probably offline",
                partner_id,
            )

    log.info(
        "cameras: event-trigger upload=%s session=%s hole=%d tee=%s green=%s",
        event.id,
        sid,
        cam.assigned_hole,
        cam.id,
        partner_id,
    )
    return {
        "ok": True,
        "event_id": event.id,
        "session_id": sid,
        "duplicate": False,
        "paired_with_camera_id": partner_id,
    }


@router.post("/{token}/event-stop")
def event_stop(
    token: str,
    session_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Tee-Pi-only: signals that recording has ended for this session.
    Called right after the tee Pi releases its VideoWriter, before the
    (potentially slow) upload, so the paired green Pi — which polls
    /event-status — can stop recording at roughly the same moment.

    Idempotent: repeat calls with the same session_id keep the first
    stop_signal_at and return ok=True. Cheap and fire-and-forget.
    """
    cam = _get_camera_by_token(token, db)
    if cam.assigned_role != "tee":
        raise HTTPException(400, "event-stop is only valid for tee cameras")

    sid = (session_id or "").strip()[:80]
    if not sid:
        raise HTTPException(400, "session_id is required")

    event = db.query(CameraEvent).filter(CameraEvent.session_id == sid).first()
    if event is None:
        raise HTTPException(404, "no event for that session_id; trigger first")
    if event.tee_camera_id != cam.id:
        raise HTTPException(403, "this camera is not the tee for that event")

    if event.stop_signal_at is None:
        event.stop_signal_at = _utcnow_naive()
        db.commit()
    return {
        "ok": True,
        "event_id": event.id,
        "stop_signal_at": event.stop_signal_at.isoformat(),
    }


@router.get("/{token}/event-status")
def event_status(
    token: str,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Either Pi can poll this. Reports whether the tee has signalled
    end-of-session (via /event-stop) for `session_id`. The green Pi
    uses this to mirror the tee's stop decision instead of recording
    a hard-coded duration."""
    cam = _get_camera_by_token(token, db)
    db.commit()  # touch last_seen_at

    sid = (session_id or "").strip()[:80]
    if not sid:
        raise HTTPException(400, "session_id is required")

    event = db.query(CameraEvent).filter(CameraEvent.session_id == sid).first()
    if event is None:
        raise HTTPException(404, "no event for that session_id")
    # Auth: only the tee or paired green for this event may peek at it.
    if cam.id not in (event.tee_camera_id, event.green_camera_id):
        raise HTTPException(403, "this camera is not part of that event")

    return {
        "session_id": sid,
        "stop_signal": event.stop_signal_at is not None,
        "stop_signal_at": (
            event.stop_signal_at.isoformat() if event.stop_signal_at else None
        ),
        "server_time": _utcnow_naive().isoformat(),
    }


@router.get("/{token}/poll-trigger")
async def poll_trigger(
    token: str,
    timeout: int = 25,
    db: Session = Depends(get_db),
):
    """Green Pi long-poll. Returns immediately if there's a pending
    trigger for this camera; otherwise holds open for `timeout`
    seconds waiting for one. On timeout returns {trigger: null} and
    the Pi reconnects.

    `timeout` capped to 60 s server-side so a stuck client can't pin
    a worker forever. 25 s default is below most LTE / Replit proxy
    idle timeouts.
    """
    cam = _get_camera_by_token(token, db)
    db.commit()

    wait_seconds = max(1, min(60, int(timeout or 25)))
    q = _queue_for(cam.id)
    try:
        msg = await asyncio.wait_for(q.get(), timeout=float(wait_seconds))
        return {"trigger": msg, "server_time": _utcnow_naive().isoformat()}
    except asyncio.TimeoutError:
        return {"trigger": None, "server_time": _utcnow_naive().isoformat()}


@router.post("/{token}/upload-event")
def upload_event(
    token: str,
    session_id: str = Form(...),
    recording_started_at: float | None = Form(None),
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Both Pis call this with their recorded MP4 after the event.
    The role (tee | green) is inferred from the camera's
    assigned_role and verified against the event's tee/green camera
    ids.

    When the second clip lands (or the only clip, on an unpaired tee),
    kicks off the per-segment processing pipeline in a background
    thread. The HTTP request returns immediately so the Pi isn't
    blocked on the multi-minute tracer / composite work.
    """
    cam = _get_camera_by_token(token, db)
    sid = (session_id or "").strip()[:80]
    if not sid:
        raise HTTPException(400, "session_id is required")

    event, role = _resolve_event_role(cam, sid, db)

    # SYNC ON PURPOSE. As an `async def` this ran on the event loop, and
    # _save_event_clip's write_bytes of a multi-MB clip blocked it — the
    # whole server, health check included, stopped for the length of that
    # disk write. A plain `def` is handed to the threadpool instead, where
    # blocking is what the thread is for. Starlette has already buffered
    # the body by the time we are called, so this reads from its spooled
    # temp file rather than the network.
    data = video.file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > MAX_EVENT_CLIP_BYTES:
        raise HTTPException(
            413, f"clip exceeds {MAX_EVENT_CLIP_BYTES // (1024 * 1024)} MB cap"
        )

    fname = _save_event_clip(data, event.id, role, video.filename)
    return _record_event_clip(event, role, fname, recording_started_at, db)


@router.get("/{token}/upload-status")
def upload_status(
    token: str,
    upload_id: str,
    total_size: int = 0,
    db: Session = Depends(get_db),
):
    """How many bytes of this clip the server already holds.

    The Pi calls this before every attempt so it resumes where the last
    one died rather than starting over. Also the recovery path when a
    chunk's response is lost in flight — the bytes may well have landed,
    and this is how the Pi finds out.
    """
    cam = _get_camera_by_token(token, db)
    db.commit()
    _prune_parts()
    part, meta = _part_paths(cam.id, upload_id)
    with _PARTS_LOCK:
        received = _part_received(part, meta, int(total_size or 0))
    return {
        "received": received,
        "total_size": int(total_size or 0),
        "max_chunk_bytes": PART_MAX_CHUNK_BYTES,
    }


@router.post("/{token}/upload-chunk")
def upload_chunk(
    token: str,
    upload_id: str = Form(...),
    offset: int = Form(...),
    total_size: int = Form(...),
    chunk: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Append one slice of a clip.

    `offset` must match what the server already holds. When it doesn't
    the answer is `resync` plus the true offset rather than an error —
    a mismatch means the Pi's idea of progress is stale (a chunk landed
    but its response never made it back through a dying modem), which is
    routine here, not a fault.
    """
    cam = _get_camera_by_token(token, db)
    db.commit()
    if total_size <= 0:
        raise HTTPException(400, "total_size is required")
    if total_size > MAX_EVENT_CLIP_BYTES:
        raise HTTPException(
            413, f"clip exceeds {MAX_EVENT_CLIP_BYTES // (1024 * 1024)} MB cap"
        )
    part, meta = _part_paths(cam.id, upload_id)

    # SYNC ON PURPOSE — see upload_event. This one mattered more: as an
    # `async def` it took _PARTS_LOCK and appended to disk ON THE EVENT
    # LOOP, so two cameras uploading at once meant one of them blocked
    # the entire server while it waited for the lock. Starlette has
    # already buffered the body, so this read is from a temp file.
    #
    # Still read it before taking the lock: no reason to hold the lock
    # across a copy other cameras are waiting on.
    body = chunk.file.read()
    if not body:
        raise HTTPException(400, "empty chunk")
    if len(body) > PART_MAX_CHUNK_BYTES:
        raise HTTPException(413, "chunk too large")

    with _PARTS_LOCK:
        received = _part_received(part, meta, total_size)
        if int(offset) != received:
            return {
                "ok": False,
                "resync": True,
                "received": received,
                "total_size": total_size,
            }
        if received + len(body) > total_size:
            raise HTTPException(400, "chunk runs past the declared total_size")
        with open(part, "ab") as fh:
            fh.write(body)
        received += len(body)
        meta.write_text(json.dumps({
            "total_size": int(total_size),
            "received": int(received),
            "updated_at": time.time(),
        }))

    return {
        "ok": True,
        "received": received,
        "total_size": total_size,
        "complete": received >= total_size,
    }


@router.post("/{token}/upload-complete")
def upload_complete(
    token: str,
    session_id: str = Form(...),
    upload_id: str = Form(...),
    total_size: int = Form(...),
    filename: str | None = Form(None),
    recording_started_at: float | None = Form(None),
    db: Session = Depends(get_db),
):
    """Seal a fully-transferred clip and hand it to the normal pipeline.

    Short of the declared size, this reports `resync` with what's
    actually banked instead of failing, so the Pi simply carries on
    sending from there.
    """
    cam = _get_camera_by_token(token, db)
    sid = (session_id or "").strip()[:80]
    if not sid:
        raise HTTPException(400, "session_id is required")
    event, role = _resolve_event_role(cam, sid, db)
    part, meta = _part_paths(cam.id, upload_id)

    with _PARTS_LOCK:
        received = _part_received(part, meta, int(total_size))
        if received < int(total_size):
            return {
                "ok": False,
                "resync": True,
                "received": received,
                "total_size": int(total_size),
            }
        fname = _adopt_event_clip(part, event.id, role, filename)
        meta.unlink(missing_ok=True)

    log.info(
        "cameras: resumable upload complete — event=%s role=%s %.1f MB",
        event.id, role, int(total_size) / (1024 * 1024),
    )
    return _record_event_clip(event, role, fname, recording_started_at, db)


# ---------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------


def _process_camera_event_job(event_id: int) -> None:
    """Hand off a fully-uploaded CameraEvent to the long-upload
    pipeline so it benefits from the existing multi-swing auto-detect
    + admin edit-wizard. Creates a LongVideoUpload row pointing at the
    raw files the Pis already saved, links it back via
    LongVideoUpload.camera_event_id, then kicks off the standard
    background job. The Production page reads it as a long-upload —
    same card, same buttons (Edit, Re-Produce, Broadcast, Delete).

    Idempotent: if the event already has a linked LongVideoUpload, we
    re-use it instead of creating a duplicate.

    Owns its own DB session so the HTTP request that kicked us off
    has long since returned. Best-effort: any failure marks the event
    'failed' with last_error set instead of bubbling out.
    """
    # Imported here to dodge a circular import (admin.py imports
    # heavy modules at top level).
    from .admin import enqueue_produce_job
    from ..models import LongVideoUpload

    # Own thread, and everything below it is media work: the rehydrate,
    # the re-encodes, and the produce it waits on.
    workload.deprioritize()

    db = SessionLocal()
    try:
        event = db.get(CameraEvent, event_id)
        if event is None:
            log.warning("cameras: event %s vanished before processing", event_id)
            return

        # Rehydrate raws from object storage if the ephemeral disk lost them
        # (e.g. reprocessing an event after a redeploy).
        if event.tee_clip_filename:
            storage.ensure_local(CLIPS_DIR, event.tee_clip_filename)
        if event.green_clip_filename:
            storage.ensure_local(CLIPS_DIR, event.green_clip_filename)

        tee_path = (
            CLIPS_DIR / event.tee_clip_filename if event.tee_clip_filename else None
        )
        if tee_path is None or not tee_path.exists():
            event.status = "failed"
            event.last_error = "tee clip missing on disk"
            db.commit()
            return

        # Re-encode the raws to browser-friendly H.264 + extract a
        # thumbnail in this thread before production opens them. Cheap
        # no-op when the upload-time hook already did it.
        if event.tee_clip_filename:
            _post_process_raw_clip(event.tee_clip_filename)
        if event.green_clip_filename:
            _post_process_raw_clip(event.green_clip_filename)

        green_filename = event.green_clip_filename
        green_path = CLIPS_DIR / green_filename if green_filename else None
        has_green = green_path is not None and green_path.exists()

        # Re-use a previously-linked LongVideoUpload row if any (handles
        # Re-Produce flowing back through this code path).
        lvu = (
            db.query(LongVideoUpload)
            .filter(LongVideoUpload.camera_event_id == event.id)
            .first()
        )
        if lvu is None:
            lvu = LongVideoUpload(
                course_id=event.course_id,
                camera_type="tee",
                base_captured_at=event.triggered_at or _utcnow_naive(),
                tee_filename=event.tee_clip_filename,
                green_filename=green_filename if has_green else None,
                tee_original_filename=f"camera-event-{event.id}-tee.mp4",
                green_original_filename=(
                    f"camera-event-{event.id}-green.mp4" if has_green else None
                ),
                swing_count="multiple",
                camera_event_id=event.id,
                processing_status="pending",
            )
            db.add(lvu)
            db.commit()
            db.refresh(lvu)
        elif has_green and not lvu.green_filename:
            # Re-processing an event that was produced TEE-ONLY (fallback)
            # and whose green half has since arrived — backfill it so this
            # run makes the paired composite instead of tee-only again.
            lvu.green_filename = green_filename
            lvu.green_original_filename = f"camera-event-{event.id}-green.mp4"
            db.commit()
            log.info(
                "cameras: event %s — green arrived late, upgrading upload %s "
                "to paired", event.id, lvu.id,
            )

        # No pre-screen: the produce job's own pose pass doubles as the
        # non-golf screen — zero swing candidates and the job deletes
        # the upload + this event itself (a separate mediapipe scan
        # here would just duplicate that work).
        log.info(
            "cameras: event %s -> long-upload %s (auto-detect swings)",
            event.id, lvu.id,
        )

        # Status flips to 'processed' (or 'failed') from inside the
        # produce job's LongVideoUpload bookkeeping; mirror that onto the
        # CameraEvent at the end. We block this thread on the job so the
        # camera-event row gets its terminal status set in one pass.
        try:
            # THE produce path — the same one Debug3 and Re-Produce run.
            # This used to call _run_long_upload_job with motion-only
            # audio+motion detection; swings come from the pose detector
            # now, and the flight from Debug3, so a capture behaves
            # identically however it was started. A camera covers one
            # par-3, so its hole is passed through rather than inferred.
            # wait=True: produce is queued and serialised with every
            # other upload, but this thread blocks until its turn is
            # done so the CameraEvent's terminal status is stamped from
            # the actual outcome rather than from "we started it".
            # Read what we need, then RELEASE THE POOLED CONNECTION
            # before blocking. The produce queue serialises jobs, so
            # this wait grows with queue depth — the Nth camera event
            # waits N produce durations. Holding a session across it
            # exhausts the pool (SQLAlchemy defaults to 5 + 10
            # overflow), and once it is dry EVERY endpoint 500s:
            # heartbeat, poll-trigger, upload-event, the admin UI. A
            # camera politely waiting its turn took the whole backend
            # down, which is how it presented in the field.
            _lvu_id = lvu.id
            _hole = int(event.hole_number)
            db.close()
            try:
                enqueue_produce_job(
                    upload_id=_lvu_id, hole_number=_hole, wait=True,
                )
            finally:
                db = SessionLocal()
        except Exception as exc:
            log.exception(
                "cameras: event %s produce job crashed: %s", event_id, exc,
            )
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
            db = SessionLocal()

        # Fresh session, so no stale identity map to expire.
        event = db.get(CameraEvent, event_id)
        lvu = db.get(LongVideoUpload, _lvu_id)
        if event is None or lvu is None:
            return

        # The auto-swing detector treats "no paired audio+motion peaks"
        # as a hard failure, which is right for the long-upload UI but
        # wrong for the camera path: the Pi already captured + uploaded
        # the raws (visible on the production page), there just wasn't
        # an actual swing to produce a clip from. Treat this as a
        # successful capture so the event isn't flagged red.
        no_swings = (
            lvu.processing_status == "failed"
            and (lvu.last_error or "").startswith("no swings detected")
        )

        if lvu.processing_status == "completed" or no_swings:
            event.status = "processed"
            event.last_error = None
            # Pick the first produced clip for the back-compat
            # produced_clip_id pointer (the listing surfaces the full
            # set via the linked long-upload).
            from ..models import VideoClip
            first_clip = (
                db.query(VideoClip)
                .filter(VideoClip.long_upload_id == lvu.id)
                .order_by(VideoClip.captured_at.asc().nulls_last())
                .first()
            )
            if first_clip is not None:
                event.produced_clip_id = first_clip.id
        else:
            event.status = "failed"
            event.last_error = (lvu.last_error or "long-upload job did not complete")[:2000]
        db.commit()
        log.info(
            "cameras: event %s done — status=%s lvu=%s segs=%s produced=%s",
            event.id,
            event.status,
            lvu.id,
            lvu.last_n_segments,
            lvu.last_n_succeeded,
        )
    except Exception as exc:  # pragma: no cover
        log.exception("cameras: event %s processing crashed: %s", event_id, exc)
        try:
            db.rollback()
            event = db.get(CameraEvent, event_id)
            if event is not None:
                event.status = "failed"
                event.last_error = str(exc)[:2000]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# Tee-only fallback ---------------------------------------------------------
# Events currently being force-produced tee-only, so the periodic sweep
# doesn't kick the same one twice while it's still running.
_fallback_inflight: set[int] = set()
_fallback_lock = threading.Lock()


def _tee_only_fallback_sweep() -> None:
    """Produce a TEE-ONLY clip for any paired event whose green half never
    arrived within the fallback window. _process_camera_event_job already
    handles a missing green (green_clip_filename is None -> tee-only), so we
    just kick it. If green shows up later, upload_event flips the event back
    to paired and re-produces with both."""
    secs = int(settings.camera_tee_only_fallback_seconds or 0)
    if secs <= 0:
        return
    cutoff = _utcnow_naive() - timedelta(seconds=secs)
    db = SessionLocal()
    try:
        ids = [
            e.id
            for e in db.query(CameraEvent)
            .filter(
                CameraEvent.status == "tee_uploaded",
                CameraEvent.tee_clip_filename.isnot(None),
                CameraEvent.triggered_at < cutoff,
            )
            .all()
        ]
    finally:
        db.close()

    for eid in ids:
        with _fallback_lock:
            if eid in _fallback_inflight:
                continue
            _fallback_inflight.add(eid)

        def _run(event_id: int = eid) -> None:
            try:
                log.info(
                    "cameras: tee-only fallback — green never arrived for "
                    "event %s, producing from tee alone", event_id,
                )
                _process_camera_event_job(event_id)
            finally:
                with _fallback_lock:
                    _fallback_inflight.discard(event_id)

        threading.Thread(
            target=_run, daemon=True, name=f"tee-only-fallback-{eid}"
        ).start()


def start_tee_only_fallback_sweeper(interval_sec: float = 60.0) -> None:
    """Periodically produce tee-only for events whose green half is overdue.
    No-op when the fallback is disabled (camera_tee_only_fallback_seconds=0).
    Idempotent to start."""
    if int(settings.camera_tee_only_fallback_seconds or 0) <= 0:
        return
    if getattr(start_tee_only_fallback_sweeper, "_started", False):
        return
    start_tee_only_fallback_sweeper._started = True  # type: ignore[attr-defined]

    def _loop() -> None:
        log.info(
            "cameras: tee-only fallback sweeper started (%ss window)",
            settings.camera_tee_only_fallback_seconds,
        )
        while True:
            try:
                _tee_only_fallback_sweep()
            except Exception as exc:  # noqa: BLE001
                log.warning("cameras: tee-only fallback sweep failed: %s", exc)
            time.sleep(interval_sec)

    threading.Thread(
        target=_loop, daemon=True, name="tee-only-fallback-sweeper"
    ).start()
