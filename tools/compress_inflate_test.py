#!/usr/bin/env python3
"""Can a "compress" pass make the clip BIGGER?

It could, and on the tee it did. The upload ladder chooses its target
by comparing against its own configured ceiling and never against the
clip in front of it. That is harmless on the decode path, where the
source is raw mp4v and any H.264 target shrinks it. On a stream-copy
rig the source is already the camera's H.264 at the camera's bitrate,
so a "degrade" to a target ABOVE that bitrate inflates the file.

Observed 11 Oct, tee camera, on a cellular link that was the whole
problem that day:

    compress: copy-1d017ad2-69bfb0.mp4 10.2 MB -> 16.2 MB (H.264 4000k)

A 59% larger file, to send over a modem we had spent the morning
blaming, after a software x264 pass on 1080p60 on a board with no
hardware encoder.

This runs the REAL compress_for_upload against REAL ffmpeg, because
the bug is entirely about what the encoder actually produces, and a
test that stubs the encoder cannot see it. The controls matter as much
as the assertion: a guard that refuses everything would pass the first
check and break every path that legitimately shrinks a clip.

    python3 tools/compress_inflate_test.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import common  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def make_clip(path: Path, kbps: int, seconds: int = 3,
              size: str = "640x360", fps: int = 30) -> None:
    """A real H.264 mp4 at a known CBR, the shape of a copy-engine clip."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}",
         "-t", str(seconds),
         "-c:v", "libx264", "-preset", "veryfast",
         "-b:v", f"{kbps}k", "-minrate", f"{kbps}k",
         "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


def mb(p: Path) -> float:
    return p.stat().st_size / (1024 * 1024)


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("SKIP: ffmpeg/ffprobe not installed")
        return 0

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # ---- the probe itself, before anything leans on it -----------
        # Checked against bytes/duration rather than against the bitrate
        # ffmpeg was ASKED for: libx264 undershoots a CBR request badly
        # on synthetic input (testsrc at 2000k lands near 500k), so the
        # request is not ground truth and an assertion against it tests
        # x264's rate control, not our probe.
        src = d / "probe.mp4"
        make_clip(src, 2000)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True, check=True).stdout.strip())
        independent = int(src.stat().st_size * 8 / dur / 1000)
        got = common.source_kbps(src)
        near = got is not None and abs(got - independent) <= independent * 0.1
        check(f"source_kbps matches bytes/duration (got {got}, "
              f"computed {independent})", near, True)
        check("source_kbps on a file that is not there is None, not 0",
              common.source_kbps(d / "nope.mp4"), None)

        # ---- THE BUG: target above the clip's own bitrate ------------
        a = d / "inflate.mp4"
        make_clip(a, 2000)
        before, before_mt = mb(a), a.stat().st_mtime
        ret = common.compress_for_upload(a, target_kbps=4000)
        check("a 4000k pass over a ~2000k clip is refused", ret, False)
        check("and the file is left exactly as it was",
              round(mb(a), 4), round(before, 4))
        check("including its mtime, which the spool orders by",
              a.stat().st_mtime, before_mt)
        check("no stray temp file left behind",
              (d / "inflate.h264.mp4").exists(), False)

        # ---- CONTROL: a target that really does shrink it ------------
        b = d / "shrink.mp4"
        make_clip(b, 2000)
        before = mb(b)
        ret = common.compress_for_upload(b, target_kbps=400)
        check("a 400k pass over the same clip still runs", ret, True)
        check("and actually made it smaller", mb(b) < before * 0.8, True)

        # ---- CONTROL: geometry is asked for, so honour it ------------
        c = d / "scaled.mp4"
        make_clip(c, 2000, size="1280x720")
        ret = common.compress_for_upload(c, target_kbps=4000,
                                         scale_height=360)
        check("a downscale at a non-shrinking bitrate is NOT refused",
              ret, True)
        h = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of",
             "default=noprint_wrappers=1:nokey=1", str(c)],
            capture_output=True, text=True, check=False).stdout.strip()
        check("and the geometry change really happened", h, "360")

        # ---- CONTROL: a reclock is asked for, so honour it -----------
        e = d / "reclock.mp4"
        make_clip(e, 2000)
        ret = common.compress_for_upload(e, target_kbps=4000,
                                         force_input_fps=15.0)
        check("a reclock at a non-shrinking bitrate is NOT refused",
              ret, True)

        # ---- CONTROL: unknown source bitrate must not block ----------
        f = d / "unknown.mp4"
        make_clip(f, 2000)
        _real = common.source_kbps
        common.source_kbps = lambda *a, **k: None
        try:
            ret = common.compress_for_upload(f, target_kbps=4000)
        finally:
            common.source_kbps = _real
        check("an unreadable bitrate means carry on, never skip",
              ret, True)

        # ---- the boundary, both sides --------------------------------
        g = d / "edge.mp4"
        make_clip(g, 2000)
        s = common.source_kbps(g)
        common.compress_for_upload(g, target_kbps=int(s * 0.95))
        check("just under the clip's bitrate is still refused "
              f"(src {s} kbps)",
              common.compress_for_upload(g, target_kbps=int(s * 0.95)),
              False)
        h2 = d / "edge2.mp4"
        make_clip(h2, 2000)
        s2 = common.source_kbps(h2)
        check("comfortably under it is accepted",
              common.compress_for_upload(h2, target_kbps=int(s2 * 0.5)),
              True)

        # ---- the original numbers, to scale --------------------------
        i = d / "field.mp4"
        make_clip(i, 2367, seconds=4, size="1280x720")
        was, was_kbps = mb(i), common.source_kbps(i)
        common.compress_for_upload(i, target_kbps=4000)
        check(f"the field case ({was_kbps} kbps clip, 4000k target) "
              "does not grow", mb(i) <= was, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
