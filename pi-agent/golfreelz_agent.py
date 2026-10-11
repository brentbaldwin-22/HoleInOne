#!/usr/bin/env python3
"""GolfReelz Pi capture agent — entry point.

Loads config.yaml (or the path passed as the first arg), dispatches
to the tee or green runner based on the role the backend assigns to
this camera's auth_token.

Usage:
    python3 golfreelz_agent.py [/path/to/config.yaml]

The role is determined at runtime: the first heartbeat returns the
camera's assigned_role, and the agent picks tee.run() or green.run()
based on that. So a single SD-card image works for both deployment
positions — only the config.yaml differs.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from agent.common import (
    BackendClient,
    CameraUnavailable,
    camera_free_telemetry,
    install_dns_cache,
    load_config,
)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str]) -> int:
    cfg_path = Path(argv[1]) if len(argv) > 1 else Path("config.yaml")
    if not cfg_path.exists():
        print(f"config not found at {cfg_path}; see config.example.yaml", file=sys.stderr)
        return 2
    # LOGGING FIRST, because loading the config is itself something
    # that logs — and what it logs is exactly what an operator reads to
    # confirm a config edit took effect: which capture mode won, a
    # warning when `mode` overrules an explicit fps, and the pre-roll
    # buffer's RAM appetite. With the order reversed those went out
    # under the root logger's default level, which drops INFO, so the
    # one check that answers "did my edit land" printed nothing at all
    # and the WARNING half arrived without the lines giving it context.
    #
    # log_level lives in the file we have not read yet, so configure at
    # the default and re-level once we have it. basicConfig is a no-op
    # the second time (the root logger already has a handler), which is
    # why this sets the level directly rather than calling it again.
    setup_logging()
    cfg = load_config(cfg_path)
    _level = str(cfg.get("log_level", "INFO")).upper()
    logging.getLogger().setLevel(getattr(logging, _level, logging.INFO))
    log = logging.getLogger("golfreelz_agent")

    # BEFORE THE FIRST REQUEST. On a cellular link that loses DNS
    # queries, an uncached lookup is a 5s stall or an outright failure
    # -- and every dead connection triggers a fresh one. Cache them, and
    # keep serving the last good address when a lookup fails.
    install_dns_cache()

    # First heartbeat tells us our assigned role.
    client = BackendClient(cfg["backend_url"], cfg["auth_token"])
    try:
        hb = client.heartbeat(firmware_version="bootstrap")
    except Exception as exc:
        log.error("initial heartbeat failed: %s", exc)
        return 1
    role = (hb.get("assigned_role") or "").lower()
    if role not in ("tee", "green"):
        log.error("backend returned unknown assigned_role=%r", role)
        return 1
    log.info(
        "this camera = #%s on course %s hole %s role=%s",
        hb.get("camera_id"), hb.get("course_id"),
        hb.get("assigned_hole"), role,
    )
    if not hb.get("enabled"):
        log.error("camera is disabled on the backend — exiting")
        return 1

    if role == "tee":
        from agent.tee import TeeAgent
        from agent import tee_roi

        # ZONES CAN COME FROM EITHER END NOW. The card still carries a
        # box for a rig provisioned the old way, but a camera whose
        # zones were drawn in the app is fully configured without one —
        # so refuse only when NEITHER end has any, which is the case
        # where the agent really cannot know what counts as the tee.
        server_zones = hb.get("tee_box_roi")
        if not cfg.get("tee_box_roi") and not tee_roi.boxes(server_zones):
            log.error(
                "no trigger zones: none in config.yaml and none drawn for "
                "this camera in the app — a tee agent has no way to tell "
                "who is on the tee",
            )
            return 2
        from agent.tee import FIRMWARE as _firmware
        agent = TeeAgent(cfg)
        if tee_roi.boxes(server_zones):
            # Before the first status poll, so the very first golfer is
            # judged against the zones an operator actually drew.
            agent.apply_tee_zones(server_zones)
    else:
        from agent.green import FIRMWARE as _firmware
        from agent.green import GreenAgent
        agent = GreenAgent(cfg)

    # Curfew sleep/wake (battery deployments): halts the Pi at night
    # and lets the RTC wake it at dawn. Off unless config enables it.
    from agent.curfew import CurfewThread
    # The curfew asks the running agent what it still owes the backend,
    # so a rig does not power off on top of today's last swing. The
    # The agent grows its `uploader` when it starts running; before
    # that there is nothing queued anyway, so a missing attribute reads
    # as nothing pending rather than blocking a halt.
    def _uploads_outstanding() -> int:
        up = getattr(agent, "uploader", None)
        return up.pending_count() if up is not None else 0

    curfew = CurfewThread(cfg.get("curfew"), work_pending=_uploads_outstanding)
    curfew.start()

    # Graceful Ctrl-C / systemd stop.
    stopping = {"yes": False}

    def _shutdown(signum, _frame):
        log.info("signal %d received — shutting down", signum)
        stopping["yes"] = True
        agent.stop()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # A MISSING CAMERA IS NOT A REASON TO DIE.
    #
    # open_camera() is the first thing run() does, so a rig whose
    # camera is unplugged used to exit before anything else started —
    # before the heartbeat that carries the real firmware string,
    # before the uploader, before the uplink and temperature readers.
    # systemd restarted it; it died again. Observed: 1062 restarts in
    # twelve hours, during which the camera card showed `fw bootstrap`
    # and a frozen snapshot from the night before, while the rig was
    # up the whole time and perfectly reachable.
    #
    # So wait instead, and KEEP REPORTING WHILE WAITING. The readers
    # here need no camera, which means the rig you most want to look
    # at — the broken one — is the one still answering. The spool
    # cannot drain without run(), but nothing is lost by waiting: the
    # clips are on disk either way.
    wait = 5.0
    while not stopping["yes"]:
        try:
            agent.run()
            break
        except KeyboardInterrupt:
            agent.stop()
            break
        except CameraUnavailable as exc:
            if stopping["yes"]:
                break
            log.warning(
                "camera not available (%s) — retrying in %.0fs. The agent "
                "stays up and keeps heartbeating so the card can still "
                "show this rig's power, temperature and uplink.",
                exc, wait,
            )
            try:
                client.heartbeat(
                    firmware_version=_firmware,
                    extra=camera_free_telemetry(),
                )
            except Exception as hb_exc:  # noqa: BLE001
                log.debug("waiting heartbeat failed: %s", hb_exc)
            # Slept in slices so a systemd stop is honoured promptly
            # rather than after a full minute of backoff.
            _deadline = time.time() + wait
            while time.time() < _deadline and not stopping["yes"]:
                time.sleep(0.5)
            wait = min(wait * 2, 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
