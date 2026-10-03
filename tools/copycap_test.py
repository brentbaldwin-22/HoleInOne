#!/usr/bin/env python3
"""End-to-end test of the stream-copy segment ring.

Runs the REAL SegmentRing against a real H.264 source paced in real
time (ffmpeg -re), which is what an RTSP camera looks like to the
reader. Checks the properties the capture path depends on:

  * segments appear at the configured cadence
  * a span extracted across several of them is one valid clip
  * the clip's frames are the camera's frames, not re-encoded ones
  * the span is rounded OUTWARD and reported honestly
  * the window is swept, so a ring left running is bounded
  * asking for video the ring never held fails instead of lying

Needs ffmpeg and a few seconds. Run from the repo root:

    python3 tools/copycap_test.py [source.mp4]

With no argument it synthesises a 1-second-GOV H.264 clip, so it runs
anywhere; point it at a real camera capture to test against that.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.copycap import SegmentRing, _seg_start  # noqa: E402

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def check_true(name: str, got, why: str = "") -> None:
    ok = bool(got)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}{'' if ok else f' ({why}: {got!r})'}")
    if not ok:
        FAIL.append(name)


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height,nb_frames,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        return {}
    d = json.loads(r.stdout or b"{}")
    st = (d.get("streams") or [{}])[0]
    return {
        "codec": st.get("codec_name"),
        "w": st.get("width"), "h": st.get("height"),
        "frames": int(st.get("nb_frames") or 0),
        "rate": st.get("avg_frame_rate"),
        "duration": float((d.get("format") or {}).get("duration") or 0),
    }


def synth(path: Path, seconds: int = 20, fps: int = 30) -> None:
    """A source with one-second keyframes, like the camera's GOV 60@60."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-g", str(fps), "-keyint_min", str(fps),
         "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=180,
    )


def main() -> int:
    if shutil_which("ffmpeg") is None or shutil_which("ffprobe") is None:
        print("ffmpeg/ffprobe not available — skipping")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if len(sys.argv) > 1:
            src = Path(sys.argv[1])
            print(f"source: {src}")
        else:
            src = tmp / "src.mp4"
            synth(src)
            print(f"source: synthesised {src.name}")
        want = probe(src)
        print(f"        {want['codec']} {want['w']}x{want['h']} "
              f"{want['rate']} {want['duration']:.1f}s\n")

        ring = SegmentRing(
            source=str(src),
            work_dir=tmp / "ring",
            segment_seconds=1.0,
            window_seconds=6.0,          # short, so the sweep is testable
            # -re paces a file at wall-clock speed: a live source, with
            # no RTSP server needed to test the reader.
            input_args=["-re", "-i", str(src)],
        )
        ring.start()
        try:
            t_start = time.time()
            # Let the ring fill past its window so the sweeper has run.
            deadline = time.time() + 12
            while time.time() < deadline and len(ring.segments()) < 8:
                time.sleep(0.25)

            segs = ring.segments()
            check_true("segments are being written", len(segs) >= 5,
                       "count")
            check_true("ring reports healthy", ring.healthy(), "healthy")

            # The window is 6s; the ring must not grow without bound.
            check_true("window is swept (ring stays bounded)",
                       len(ring.segments(include_current=True)) <= 9,
                       "segments held")

            first, last = ring.span()
            check_true("span is known", first is not None and last is not None,
                       "span")

            # Ask for a 3-second span inside what the ring holds.
            want_start = last - 3.5
            want_end = last - 0.5
            out = tmp / "clip.mp4"
            res = ring.extract(want_start, want_end, out)
            check_true("extract succeeded", res.get("ok"), res.get("why"))
            if res.get("ok"):
                got = probe(out)
                check("clip codec is the camera's", got["codec"], want["codec"])
                check("clip resolution is untouched",
                      (got["w"], got["h"]), (want["w"], want["h"]))
                check("clip frame rate is untouched", got["rate"], want["rate"])
                check_true("clip has frames", got["frames"] > 0, "frames")
                # Rounded outward: never SHORTER than asked for.
                check_true(
                    "span is rounded outward, not inward",
                    res["start"] <= want_start + 1e-6
                    and res["end"] >= want_end - 1e-6,
                    f"{res['start']:.2f}..{res['end']:.2f} vs "
                    f"{want_start:.2f}..{want_end:.2f}",
                )
                # ...and by less than one segment at each end.
                check_true(
                    "slop is under one segment per end",
                    (want_start - res["start"]) < 1.001
                    and (res["end"] - want_end) < 1.001,
                    "slop",
                )
                check_true("extract is fast (it is a copy)",
                           res["took"] < 2.0, f"{res.get('took')}s")
                print(f"      → {res['seconds']}s, {res['bytes']/1e6:.2f} MB, "
                      f"{res['segments']} segments, {res['took']}s")

            # Video from before the ring existed is gone, and saying so
            # matters more than returning something shorter in silence.
            old = ring.extract(t_start - 600, t_start - 590, tmp / "old.mp4")
            check("asking for video the ring never held fails",
                  old.get("ok"), False)

            st = ring.status()
            check_true("status reports a healthy ring", st["healthy"], "status")
            check_true("status counts bytes", st["bytes_held"] > 0, "bytes")
        finally:
            ring.stop()

        check_true("ring stops cleanly", not ring.healthy(), "still healthy")

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


def shutil_which(x):
    import shutil
    return shutil.which(x)


if __name__ == "__main__":
    raise SystemExit(main())
