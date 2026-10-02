"""Tap-to-watch live stream for the on-course Pi agent.

Polls the backend's /api/cameras/{token}/watch-status endpoint. When the
operator clicks Watch on /admin/cameras, the backend flips that flag
to true. The Pi then JPEG-encodes its most recent frame and POSTs it
to /api/cameras/{token}/live-frame at ~10 fps until the operator closes
the view (the backend stops returning watching=true after ~10s of no
admin poll, which is the natural stop signal).

Integration points (in tee.py / green.py main loops):

    from .livestream import LiveStreamer

    streamer = LiveStreamer(self.client)
    streamer.start()
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None: continue
            streamer.update_frame(frame)   # ← single new line
            # ... existing capture logic
    finally:
        streamer.stop()

The streamer thread is daemonized and self-throttling: zero frame
encoding cost when no one is watching, so it's safe to leave running
forever in the background.

IT ALSO SENDS THE STILL. A camera nobody is watching still has a view,
and the operator opening /admin/cameras wants to see it without putting
every Pi on the course into 10 fps streaming to find out. So the same
watch-status poll carries `still_wanted`: true when the picture the
backend is holding for this camera is missing or stale, and the thread
answers with ONE higher-quality frame POSTed to /still. The backend owns
the interval and the staleness test, because the file is on its disk and
its disk is the one that comes back empty after a redeploy — a timer out
here would keep believing it had sent a frame that no longer exists.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import cv2

log = logging.getLogger("golfreelz_agent.livestream")


class LiveStreamer:
    def __init__(
        self,
        client,
        *,
        fps: int = 10,
        jpeg_quality: int = 65,
        idle_poll_seconds: float = 1.0,
        watched_poll_seconds: float = 5.0,
        on_capture_request=None,
        on_focus_mode=None,
        on_lens_command=None,
        on_tee_zones=None,
        still_quality: int = 85,
        still_min_interval: float = 15.0,
    ) -> None:
        self.client = client
        # Called with (seconds) when the backend asks for an on-demand
        # capture. Delivered on the watch-status poll this thread already
        # makes, so the operator's Capture button needs no new endpoint
        # on the device and no extra polling. Tee cameras pass a handler;
        # green ignores it, because a green records only when its paired
        # tee tells it to.
        self.on_capture_request = on_capture_request
        # Called on every poll with the seconds of focus mode remaining
        # (0 when off), so the agent can raise its measurement rate.
        self.on_focus_mode = on_focus_mode
        self.on_lens_command = on_lens_command
        # Called with the backend's trigger zones on every poll (None
        # when the camera has none set). A STATE, like focus mode, not a
        # command: the backend sends the current answer every time
        # rather than trying to tell us only when it changes, because a
        # Pi that restarts or wakes from curfew has no idea what it
        # missed.
        self.on_tee_zones = on_tee_zones
        self.frame_interval = 1.0 / max(1, fps)
        self.jpeg_quality = max(20, min(95, jpeg_quality))
        # The still is looked AT rather than through, one frame every
        # quarter hour, so it is worth more bytes than a live frame is.
        self.still_quality = max(40, min(95, still_quality))
        # A floor between snapshot pushes. Not the interval -- the
        # backend sets that -- just a guard so a push that keeps failing,
        # or a backend that keeps asking, cannot turn into a hot loop.
        self.still_min_interval = max(5.0, still_min_interval)
        self.idle_poll = idle_poll_seconds
        self.watched_poll = watched_poll_seconds

        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._watching = False
        # The backend's answer to "is the stored snapshot stale?".
        self._still_wanted = False
        # Our own reason to send one: something happened here that
        # changed what the camera is looking at -- a zoom nudge, a focus
        # move, the live view closing after somebody re-aimed the mount
        # -- and the stored picture is now a picture of the past. Kept
        # separate from the flag above so a poll's "no thanks" cannot
        # clear it before it has been acted on.
        self._still_forced = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def update_frame(self, frame) -> None:
        """Capture loop calls this on every frame it processes."""
        with self._frame_lock:
            self._latest_frame = frame

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="livestream",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    # -------- internal -----------------------------------------------------

    def _watch_status_url(self) -> str:
        # Reuse BackendClient's tokenized URL builder so we stay in lock-
        # step with the auth scheme used by every other Pi endpoint.
        return self.client._url("/watch-status")

    def _live_frame_url(self) -> str:
        return self.client._url("/live-frame")

    def _still_url(self) -> str:
        return self.client._url("/still")

    def _run(self) -> None:
        last_poll = 0.0
        last_push = 0.0
        last_still = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            poll_every = self.watched_poll if self._watching else self.idle_poll
            if now - last_poll >= poll_every:
                self._poll_watch_status()
                last_poll = now

            if self._watching and now - last_push >= self.frame_interval:
                self._push_frame()
                last_push = now

            # The snapshot, whether or not anyone is watching -- being
            # watched is not a reason to let the stored picture go stale,
            # and one extra frame a quarter hour costs nothing next to
            # the ten a second already going out. The clock is advanced
            # on the ATTEMPT, so a camera whose pushes are failing retries
            # on the floor rather than every loop.
            if ((self._still_wanted or self._still_forced)
                    and now - last_still >= self.still_min_interval):
                last_still = now
                self._push_still()

            time.sleep(0.05)

    def _poll_watch_status(self) -> None:
        try:
            r = self.client.session.get(self._watch_status_url(), timeout=5)
            r.raise_for_status()
            payload = r.json()
            new_state = bool(payload.get("watching"))
            if new_state != self._watching:
                log.info("live-stream %s", "started" if new_state else "stopped")
                # Somebody was just looking at this camera live, and the
                # usual reason to look is that you are about to change
                # something. Refresh the still as the view closes so the
                # card shows what was left behind, not what was there
                # fifteen minutes before anyone touched it.
                if not new_state:
                    self._still_forced = True
            self._watching = new_state
            # An older backend doesn't send this; absent means "no".
            self._still_wanted = bool(payload.get("still_wanted"))
            # Consumed server-side on read, so it arrives exactly once.
            # Focus mode is a STATE, not a one-shot: it arrives on every
            # poll until it expires, and the handler is called each time
            # so the deadline keeps moving forward while it is armed.
            fsecs = payload.get("focus_seconds")
            if self.on_focus_mode:
                try:
                    self.on_focus_mode(float(fsecs or 0))
                except Exception as exc:  # noqa: BLE001
                    log.debug("focus-mode handler failed: %s", exc)
            # A LIST, drained whole. Applied in the order the operator
            # clicked, because a Simple Focus after a zoom means
            # something different from one before it.
            cmds = payload.get("lens_commands") or []
            if cmds and self.on_lens_command:
                log.info("lens: %d command(s) from the operator", len(cmds))
                for c in cmds:
                    try:
                        # `params` carries the commands that are not a
                        # nudge — exposure, which has named values
                        # rather than a step size. An older backend
                        # sends none and the handler sees {}.
                        self.on_lens_command(
                            str(c.get("op") or ""), int(c.get("amount") or 0),
                            dict(c.get("params") or {}),
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.error("lens command handler failed: %s", exc)
                # The lens just moved, so the stored still is of the old
                # framing. Not immediately -- the floor in the run loop
                # gives the motor time to finish and the exposure time to
                # settle before the frame we keep is taken.
                self._still_forced = True
            if self.on_tee_zones:
                try:
                    self.on_tee_zones(payload.get("tee_box_roi"))
                except Exception as exc:  # noqa: BLE001
                    log.debug("tee-zone handler failed: %s", exc)
            secs = payload.get("capture_seconds")
            if secs and self.on_capture_request:
                log.info("capture requested by operator: %ss", secs)
                try:
                    self.on_capture_request(float(secs))
                except Exception as exc:  # noqa: BLE001
                    log.error("capture request handler failed: %s", exc)
        except Exception as e:
            log.debug("watch-status poll failed: %s", e)
            if self._watching:
                log.info("live-stream stopped (poll failed)")
            self._watching = False

    def _push_frame(self) -> None:
        with self._frame_lock:
            frame = self._latest_frame
        if frame is None:
            return
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not ok:
            return
        try:
            self.client.session.post(
                self._live_frame_url(),
                data=buf.tobytes(),
                headers={"Content-Type": "image/jpeg"},
                timeout=2,
            )
        except Exception as e:
            # Don't let stream failures interrupt recording.
            log.debug("frame push failed: %s", e)

    def _push_still(self) -> None:
        """One frame, kept by the backend until the next one replaces it."""
        with self._frame_lock:
            frame = self._latest_frame
        if frame is None:
            # Nothing captured yet (agent just started, or the stream is
            # down). Leave both flags set: whatever is wrong, the answer
            # is to try again once there are frames, not to give up.
            return
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.still_quality],
        )
        if not ok:
            return
        try:
            r = self.client.session.post(
                self._still_url(),
                data=buf.tobytes(),
                headers={"Content-Type": "image/jpeg"},
                timeout=20,
            )
            r.raise_for_status()
            log.info("still: sent %d bytes", len(buf))
            self._still_forced = False
            self._still_wanted = False
        except Exception as e:  # noqa: BLE001
            # Same rule as the live frames: a picture for the operator is
            # never worth interrupting a recording for.
            log.debug("still push failed: %s", e)
