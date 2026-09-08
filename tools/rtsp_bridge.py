#!/usr/bin/env python3
"""Give an IP camera the live view that a Pi agent gets for free.

A Pi opens its own lens and PUSHES frames up; the backend never reaches
into anything. An RTSP camera does the opposite -- it answers when asked
and pushes nothing -- so the Cameras page has no frames to show and the
Watch button sits there doing nothing.

This is the missing half: it pulls from the camera and pushes to the
backend, speaking exactly the protocol the Pi already speaks. Two
endpoints, no new server code:

    GET  /api/cameras/{token}/watch-status  -> {"watching": bool, ...}
    POST /api/cameras/{token}/live-frame    <- JPEG bytes

Run it anywhere that can see BOTH the camera and the internet -- a
laptop on the same LAN today, the Pi on the pole later. It is the small
front half of teaching the agent to read RTSP, kept separate so it can
be proven on its own before anything that records is touched.

ONE ffmpeg, NOT one per frame. Reconnecting RTSP for every still costs
a second or two of handshake each time and hammers the camera; instead a
single process emits MJPEG continuously and frames are split out of the
byte stream as they arrive.

Usage:
    export GOLFREELZ_CAM_TOKEN=...        # the camera's auth_token
    export GOLFREELZ_CAM_PASSWORD=...     # the camera's own password
    python3 tools/rtsp_bridge.py \
        --backend https://holeinone-s6qm.onrender.com \
        --rtsp rtsp://admin:{password}@10.0.0.249/profile2/media.smp

The password is read from the environment and substituted into {password}
rather than passed on the command line, because argv is world-readable
in `ps` on a shared machine.
"""
from __future__ import annotations

import argparse
import collections
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SOI = b"\xff\xd8"          # JPEG start of image
EOI = b"\xff\xd9"          # JPEG end of image
MAX_FRAME_BYTES = 500_000  # the backend's own limit; drop rather than 413
# No frame for this long while an operator IS watching means the stream
# has stalled rather than gone quiet, so tear ffmpeg down and start over.
STALL_SEC = 20.0


def _log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def watch_status(backend: str, token: str, timeout: float = 8.0) -> dict:
    url = f"{backend.rstrip('/')}/api/cameras/{token}/watch-status"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        import json
        return json.loads(r.read().decode("utf-8") or "{}")


def post_frame(backend: str, token: str, jpeg: bytes,
               timeout: float = 10.0) -> bool:
    url = f"{backend.rstrip('/')}/api/cameras/{token}/live-frame"
    req = urllib.request.Request(
        url, data=jpeg, method="POST",
        headers={"Content-Type": "image/jpeg"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as exc:
        _log(f"live-frame rejected: {exc.code} {exc.reason}")
        return False
    except Exception as exc:  # noqa: BLE001
        _log(f"live-frame failed: {exc}")
        return False


def _drain(pipe, sink: collections.deque) -> None:
    """Keep ffmpeg's stderr moving.

    NOTHING READ THIS BEFORE, and that was a deadlock: ffmpeg writes a
    line per corrupt macroblock, the OS pipe buffer fills at 64 KB, and
    ffmpeg then BLOCKS trying to write it. Blocked on stderr it stops
    producing stdout, so the reader waits forever for bytes that cannot
    come. It looked exactly like a healthy process that had gone quiet:
    twelve good minutes, then silence with no error.
    """
    try:
        for line in iter(pipe.readline, b""):
            sink.append(line.decode(errors="replace").rstrip())
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            pipe.close()
        except Exception:  # noqa: BLE001
            pass


def start_ffmpeg(ffmpeg: str, rtsp: str, fps: float, width: int,
                 quality: int) -> subprocess.Popen:
    cmd = [
        ffmpeg, "-loglevel", "error",
        # TCP, not UDP: a dropped datagram on a cellular link turns into
        # a torn frame, and a torn JPEG is worse than a late one.
        "-rtsp_transport", "tcp",
        "-i", rtsp,
        "-an",
        "-vf", f"fps={fps},scale={width}:-2",
        "-q:v", str(quality),
        "-f", "mjpeg", "-",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0)


def frames_from(proc: subprocess.Popen):
    """Yield complete JPEGs out of ffmpeg's MJPEG stdout."""
    buf = bytearray()
    while True:
        chunk = proc.stdout.read(16384)
        if not chunk:
            return
        buf += chunk
        # A frame is everything from a start marker to the next end
        # marker. Anything before the first start is leftover garbage
        # from a mid-stream reconnect and is thrown away.
        while True:
            start = buf.find(SOI)
            if start < 0:
                buf.clear()
                break
            end = buf.find(EOI, start + 2)
            if end < 0:
                if start > 0:
                    del buf[:start]
                break
            frame = bytes(buf[start:end + 2])
            del buf[:end + 2]
            yield frame


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--rtsp", required=True,
                    help="RTSP URL; {password} is filled from the env")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--quality", type=int, default=6, help="mjpeg q:v, 2=best")
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    ap.add_argument("--poll", type=float, default=3.0,
                    help="seconds between watch-status polls")
    args = ap.parse_args()

    token = os.environ.get("GOLFREELZ_CAM_TOKEN", "").strip()
    if not token:
        _log("set GOLFREELZ_CAM_TOKEN to the camera's auth_token")
        return 2
    rtsp = args.rtsp.replace(
        "{password}", os.environ.get("GOLFREELZ_CAM_PASSWORD", ""),
    )

    # SAY SOMETHING AT STARTUP. State-change logging alone meant the
    # normal case -- running fine, nobody watching yet -- printed
    # nothing at all, which is indistinguishable from hung.
    _log(f"bridge up · camera token ...{token[-6:]} · {args.backend}")
    _log("waiting for an operator to press Watch")

    proc = None
    reader = None
    errs: collections.deque = collections.deque(maxlen=40)
    frames: "queue.Queue[bytes]" = queue.Queue(maxsize=2)
    sent = 0
    last_poll = 0.0
    last_frame = 0.0
    watching = False
    polls = 0

    def stop_ffmpeg():
        nonlocal proc, reader
        if proc is not None:
            try:
                proc.kill(); proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        proc, reader = None, None
        while not frames.empty():
            try:
                frames.get_nowait()
            except queue.Empty:
                break

    def read_frames(p: subprocess.Popen):
        # NEWEST FRAME WINS. A live view wants what is happening now, so
        # a full queue drops the stale one rather than blocking the
        # reader -- which would re-create the very stall this fixes.
        for f in frames_from(p):
            if len(f) > MAX_FRAME_BYTES:
                continue
            try:
                frames.put_nowait(f)
            except queue.Full:
                try:
                    frames.get_nowait()
                    frames.put_nowait(f)
                except (queue.Empty, queue.Full):
                    pass

    try:
        while True:
            now = time.time()
            if now - last_poll >= args.poll:
                last_poll = now
                try:
                    st = watch_status(args.backend, token)
                    was, watching = watching, bool(st.get("watching"))
                    polls += 1
                    if watching != was:
                        _log("operator is watching — streaming" if watching
                             else "nobody watching — idle")
                        if watching:
                            last_frame = time.time()
                    elif not watching and polls % 20 == 1:
                        _log("still idle — nobody watching")
                except Exception as exc:  # noqa: BLE001
                    _log(f"watch-status failed: {exc}")

            if not watching:
                if proc is not None:
                    stop_ffmpeg()
                time.sleep(0.4)
                continue

            if proc is None or proc.poll() is not None:
                if proc is not None:
                    tail = " | ".join(list(errs)[-3:]) or "no error text"
                    _log(f"ffmpeg exited: {tail}")
                    stop_ffmpeg()
                errs.clear()
                _log("starting ffmpeg")
                proc = start_ffmpeg(args.ffmpeg, rtsp, args.fps,
                                    args.width, args.quality)
                threading.Thread(target=_drain, args=(proc.stderr, errs),
                                 daemon=True).start()
                reader = threading.Thread(target=read_frames, args=(proc,),
                                          daemon=True)
                reader.start()
                last_frame = time.time()

            try:
                frame = frames.get(timeout=1.0)
            except queue.Empty:
                # A WATCHDOG, because a wedged ffmpeg does not exit. It
                # sits there holding a pipe, and without this the bridge
                # waits on it for as long as the operator is patient.
                if time.time() - last_frame > STALL_SEC:
                    tail = " | ".join(list(errs)[-3:]) or "no error text"
                    _log(f"no frames for {STALL_SEC:.0f}s — restarting "
                         f"ffmpeg · {tail}")
                    stop_ffmpeg()
                continue

            last_frame = time.time()
            if post_frame(args.backend, token, frame):
                sent += 1
                if sent % 20 == 1:
                    _log(f"pushed {sent} frame(s), latest {len(frame)} bytes")
    except KeyboardInterrupt:
        _log("stopping")
    finally:
        stop_ffmpeg()
    return 0


if __name__ == "__main__":
    sys.exit(main())
