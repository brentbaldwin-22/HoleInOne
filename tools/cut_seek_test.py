#!/usr/bin/env python3
"""`cut_segment` builds the ffmpeg command the mode asks for.

WHY THIS IS WORTH A TEST. The difference between a cut that takes two
seconds and one that takes two minutes is whether `-ss` comes before
or after `-i`, and the difference between a frame-accurate cut and one
up to two seconds early is whether the stream is copied or re-encoded.
Both are argv ORDER, both are invisible in review, and both produced
real bugs on this page: the manual cutter shipped with the accurate-
but-slow form and hung on every swing deep into a recording.

The output cannot be checked without ffmpeg and a real file. The
command can, and the command is the part that was wrong.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import video                                  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ("" if cond else "  <- " + detail))
    if not cond:
        FAILED.append(name)


def capture(**kw):
    """Run cut_segment with ffmpeg stubbed, and return the argv."""
    seen = {}

    def fake_run(cmd, **_):
        seen["cmd"] = list(cmd)
        # cut_segment checks the output exists and is non-empty after.
        Path(cmd[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0)

    real_run, real_have = subprocess.run, video.have_ffmpeg
    subprocess.run = fake_run
    video.have_ffmpeg = lambda: True
    try:
        ok = video.cut_segment(**kw)
    finally:
        subprocess.run = real_run
        video.have_ffmpeg = real_have
    return ok, seen.get("cmd", [])


def idx(cmd, flag, after=0):
    for i in range(after, len(cmd)):
        if cmd[i] == flag:
            return i
    return -1


def main():
    out = Path("/tmp/_cut_seek_test_out.mp4")
    src = Path("/tmp/_cut_seek_test_in.mp4")
    src.write_bytes(b"x")
    base = dict(input_path=src, output_path=out, start_sec=107.5,
                end_sec=120.5)

    print("fast=True -- keyframe seek, stream copy:")
    ok, cmd = capture(**base, fast=True)
    check("it ran", ok)
    check("-ss comes BEFORE -i", idx(cmd, "-ss") < idx(cmd, "-i"),
          " ".join(cmd))
    check("stream copy", "-c" in cmd and "copy" in cmd)
    check("no re-encode", "libx264" not in cmd)
    check("one -ss only", cmd.count("-ss") == 1)

    print("\nfast=False, no pad -- accurate but decodes from zero:")
    ok, cmd = capture(**base)
    check("it ran", ok)
    check("-ss comes AFTER -i", idx(cmd, "-ss") > idx(cmd, "-i"),
          " ".join(cmd))
    check("re-encodes", "libx264" in cmd)
    check("one -ss only", cmd.count("-ss") == 1)
    check("-ss is the full start", "107.5" in cmd)

    print("\nfast=False, seek_pad_sec=5 -- accurate AND fast:")
    ok, cmd = capture(**base, seek_pad_sec=5.0)
    check("it ran", ok)
    check("two -ss", cmd.count("-ss") == 2, " ".join(cmd))
    first, i_at = idx(cmd, "-ss"), idx(cmd, "-i")
    second = idx(cmd, "-ss", first + 1)
    check("one -ss before -i, one after", first < i_at < second,
          " ".join(cmd))
    # THE ARITHMETIC THAT MATTERS. Pre-seek to (start - pad), then
    # walk pad. They must add to start, or the cut is in the wrong
    # place -- which is exactly the bug the pad is meant to avoid.
    pre, post = float(cmd[first + 1]), float(cmd[second + 1])
    check("pre-seek is start - pad", abs(pre - 102.5) < 1e-6, str(pre))
    check("post-seek is the pad", abs(post - 5.0) < 1e-6, str(post))
    check("they sum to the requested start",
          abs((pre + post) - 107.5) < 1e-6, f"{pre} + {post}")
    check("re-encodes", "libx264" in cmd)
    check("duration is the window", "-t" in cmd
          and abs(float(cmd[idx(cmd, "-t") + 1]) - 13.0) < 1e-6)

    print("\nstart inside the pad -- the plain path, which is cheap there:")
    ok, cmd = capture(input_path=src, output_path=out, start_sec=2.0,
                      end_sec=15.0, seek_pad_sec=5.0)
    check("one -ss only", cmd.count("-ss") == 1, " ".join(cmd))
    check("-ss comes AFTER -i", idx(cmd, "-ss") > idx(cmd, "-i"))
    check("-ss is the full start", "2.0" in cmd)

    for p in (src, out):
        p.unlink(missing_ok=True)
    print("\n%d check(s) failed" % len(FAILED) if FAILED else "\nall passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
