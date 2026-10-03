#!/usr/bin/env python3
"""Try the stream-copy capture path against a real camera, safely.

Runs the segment ring beside the live agent WITHOUT touching it: a
second RTSP client, a scratch directory, nothing written that the agent
reads. Records for a few seconds, extracts a clip the way a trigger
would, and then re-encodes the same clip the way the CURRENT pipeline
does — so the two costs are measured on the same machine, from the same
footage, in the same minute.

    sudo -u golfreelz /opt/golfreelz-agent/venv/bin/python3 \
        /opt/golfreelz-agent/copycap_probe.py

Reads the camera URL out of the agent's own config, so the password
never has to be typed or pasted. Add --substream to test the camera's
small profile (what detection should run on), --seconds to record for
longer, --keep to leave the clip behind for inspection.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.copycap import SegmentRing  # noqa: E402


def _resolve_url(device: str) -> str:
    return device.replace(
        "{password}", os.environ.get("GOLFREELZ_CAM_PASSWORD", ""),
    )


def _safe(url: str) -> str:
    """The URL with the password taken out, for printing."""
    if "@" not in url:
        return url
    head, _, tail = url.rpartition("@")
    scheme, _, creds = head.partition("://")
    user = creds.split(":", 1)[0] if creds else ""
    return f"{scheme}://{user}:***@{tail}"


def _probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,bit_rate:stream=codec_name,width,height,nb_frames",
         "-of", "default=noprint_wrappers=1:nokey=0", str(path)],
        capture_output=True, timeout=30,
    )
    out = {}
    for line in (r.stdout or b"").decode().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/golfreelz-agent/config.yaml")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="how long to let the ring fill")
    ap.add_argument("--clip", type=float, default=8.0,
                    help="length of the clip to extract")
    ap.add_argument("--segment", type=float, default=1.0)
    ap.add_argument("--substream", action="store_true",
                    help="use camera.substream_device instead of camera.device")
    ap.add_argument("--keep", action="store_true",
                    help="leave the extracted clip on disk")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found", file=sys.stderr)
        return 2

    import yaml
    cfg = yaml.safe_load(open(args.config)) or {}
    cam = cfg.get("camera") or {}
    key = "substream_device" if args.substream else "device"
    device = cam.get(key)
    if not device:
        print(f"camera.{key} is not set in {args.config}", file=sys.stderr)
        return 2
    url = _resolve_url(str(device))
    if not url.startswith(("rtsp://", "rtsps://")):
        print(f"camera.{key} is not an RTSP URL — this probe is for IP cameras",
              file=sys.stderr)
        return 2

    print(f"camera : {_safe(url)}")
    print(f"filling: {args.seconds:.0f}s at {args.segment:g}s segments\n")

    workdir = Path(tempfile.mkdtemp(prefix="copycap-probe-"))
    ring = SegmentRing(
        source=url, work_dir=workdir / "ring",
        segment_seconds=args.segment,
        window_seconds=max(30.0, args.seconds + args.clip + 10),
    )
    cpu0 = time.process_time()
    wall0 = time.time()
    ring.start()
    try:
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            time.sleep(1.0)
            st = ring.status()
            print(f"  {st['segments']:3d} segments · "
                  f"{st['seconds_held']:5.1f}s held · "
                  f"{st['bytes_held']/1e6:6.2f} MB · "
                  f"{'ok' if st['healthy'] else 'UNHEALTHY'}", end="\r")
        print()

        st = ring.status()
        if not st["segments"]:
            print("\nno segments were written — is the RTSP URL reachable "
                  "from this Pi, and is the password set?", file=sys.stderr)
            return 1

        first, last = ring.span()
        end = last - args.segment
        start = end - args.clip
        out = workdir / "clip.mp4"
        res = ring.extract(start, end, out)
        if not res.get("ok"):
            print(f"\nextract failed: {res.get('why')}", file=sys.stderr)
            return 1

        info = _probe(out)
        secs = float(info.get("duration") or 0) or res["seconds"]
        mb = res["bytes"] / 1e6
        print(f"\nSTREAM COPY")
        print(f"  {info.get('codec_name')} {info.get('width')}x"
              f"{info.get('height')} · {info.get('nb_frames')} frames · "
              f"{secs:.2f}s")
        print(f"  {mb:.2f} MB  ({mb*8/max(secs,0.01):.2f} Mbps as sent by the camera)")
        print(f"  extracted in {res['took']}s from {res['segments']} segments")
        print(f"  asked for {args.clip:.1f}s, got {res['seconds']:.1f}s "
              f"(rounded out to segment edges)")

        # The comparison that decides it: what the CURRENT path costs for
        # the same footage, on this same Pi, right now.
        target_kbps = int(cfg.get("upload_bitrate_kbps", 2500))
        enc = workdir / "reencoded.mp4"
        t0 = time.time()
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(out),
             "-c:v", "libx264", "-b:v", f"{target_kbps}k", str(enc)],
            capture_output=True, timeout=900,
        )
        took = time.time() - t0
        print(f"\nRE-ENCODE (what the agent does today, {target_kbps} kbps)")
        if r.returncode == 0 and enc.exists():
            emb = enc.stat().st_size / 1e6
            print(f"  {emb:.2f} MB in {took:.1f}s")
            print(f"\nVERDICT for {secs:.1f}s of video on this Pi")
            print(f"  cpu   : {took/max(res['took'],0.001):.0f}x cheaper to copy "
                  f"({res['took']}s vs {took:.1f}s)")
            print(f"  bytes : {mb/max(emb,0.001):.1f}x larger to copy "
                  f"({mb:.2f} MB vs {emb:.2f} MB)")
            print(f"\n  To make the copy affordable, lower the CAMERA's profile "
                  f"bitrate\n  toward {target_kbps} kbps — its encoder then does the "
                  f"job this Pi\n  is doing now, in hardware, with no second "
                  f"generation of loss.")
        else:
            print(f"  failed after {took:.1f}s: "
                  f"{(r.stderr or b'').decode('utf-8','replace')[:200]}")

        print(f"\nring cpu while recording: "
              f"{time.process_time()-cpu0:.2f}s of python over "
              f"{time.time()-wall0:.0f}s wall "
              f"(the copying itself is ffmpeg, not this process)")
        if args.keep:
            dest = Path.home() / "copycap-clip.mp4"
            shutil.copy2(out, dest)
            print(f"\nclip kept at {dest}")
        return 0
    finally:
        ring.stop()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"workdir: {workdir}")


if __name__ == "__main__":
    raise SystemExit(main())
