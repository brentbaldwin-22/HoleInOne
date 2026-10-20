#!/usr/bin/env python3
"""Does a clip that is already web-ready get re-encoded anyway?

It did, every capture. The tee camera runs the COPY engine, which cuts
with `-c copy -movflags +faststart` -- so a tee clip reaches the server
as H.264 with the moov atom in front, first-generation from the camera.
transcode_for_web then re-encoded it into a second-generation copy of
itself: a full ffmpeg pass over 1080p, under the heavy gate, on the
critical path of every event, to arrive back where it started.

It did that because it refused to probe at all. The reasoning was that
ffprobe can mis-report cv2-mp4v output, so a marker file was safer than
an answer that might be wrong. But the two wrong answers do not cost the
same: being wrong about "needs transcoding" costs one needless pass,
while being wrong about "already fine" ships a file the browser will not
play. So the probe is allowed to say SKIP only on positive, agreeing
evidence, and says TRANSCODE for everything else -- including its own
failure.

This test is about that asymmetry. Four files:

  copy-engine (h264 + faststart)   -> skipped, byte-identical after
  cv2-style mpeg4                  -> transcoded
  h264 with the moov at the back   -> transcoded
  h264 + faststart + mp3 audio     -> transcoded

    python3 tools/transcode_skip_test.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import video as V  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                   check=True, timeout=180,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


SRC = ("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2")
SILENCE = ("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest")


def make(kind: str, out: Path) -> None:
    if kind == "copy_engine":
        # What the Pi's copy engine hands us: H.264, faststart, no audio.
        ffmpeg(*SRC, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(out))
    elif kind == "mpeg4":
        # What cv2's mp4v fourcc writes: MPEG-4 Part 2.
        ffmpeg(*SRC, "-c:v", "mpeg4", "-vtag", "mp4v",
               "-pix_fmt", "yuv420p", str(out))
    elif kind == "no_faststart":
        ffmpeg(*SRC, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-movflags", "+use_metadata_tags", str(out))
    elif kind == "mp3_audio":
        ffmpeg(*SRC, *SILENCE, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "libmp3lame", "-movflags", "+faststart", str(out))
    else:
        raise ValueError(kind)


def codec_of(path: Path) -> str:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        timeout=30)
    return out.decode().strip()


def test_the_probe_reads_each_kind():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        want = {"copy_engine": True, "mpeg4": False,
                "no_faststart": False, "mp3_audio": False}
        for kind, expect in want.items():
            f = d / f"{kind}.mp4"
            make(kind, f)
            check(f"{kind} is web-ready?", V._already_web_ready(f), expect)
        # CONTROL: the fixtures are what they claim to be, or the four
        # answers above are about the wrong files.
        check("control, the copy-engine fixture really is h264",
              codec_of(d / "copy_engine.mp4"), "h264")
        check("control, the cv2-style fixture really is not",
              codec_of(d / "mpeg4.mp4"), "mpeg4")


def test_a_copy_engine_clip_is_left_alone():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "tee.mp4"
        make("copy_engine", f)
        before = f.read_bytes()
        check("transcode_for_web reports success", V.transcode_for_web(f), True)
        # THE POINT. Not "it produced a playable file" -- it did not run
        # at all, and the bytes are the camera's own.
        check("and the file is untouched", f.read_bytes() == before, True)
        check("  with the marker dropped so nobody tries again",
              f.with_suffix(f.suffix + ".h264-ok").exists(), True)


def test_a_cv2_clip_is_still_transcoded():
    """The green camera's decode engine still needs this."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "green.mp4"
        make("mpeg4", f)
        before = f.read_bytes()
        check("transcode_for_web reports success", V.transcode_for_web(f), True)
        check("the file was rewritten", f.read_bytes() != before, True)
        check("  into H.264", codec_of(f), "h264")
        check("  and it is web-ready afterwards",
              V._already_web_ready(f), True)


def test_a_broken_probe_transcodes():
    """Every uncertainty has to fall the safe way.

    This is the whole argument for probing at all: the cost of the two
    wrong answers is not symmetric, so the failure mode is pinned to
    the cheap one.
    """
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "tee.mp4"
        make("copy_engine", f)
        before = f.read_bytes()
        _real = V.subprocess.check_output

        def exploding(*a, **k):
            raise OSError("no ffprobe here")

        V.subprocess.check_output = exploding
        try:
            ready = V._already_web_ready(f)
        finally:
            V.subprocess.check_output = _real
        check("a probe that blows up says 'transcode'", ready, False)
        # CONTROL: the same file answers True when the probe works, so
        # the False above is the failure and not the fixture.
        check("control, it is web-ready when the probe runs",
              V._already_web_ready(f), True)
        check("  and the fixture was never touched", f.read_bytes() == before,
              True)


def probed(streams):
    """_already_web_ready's verdict on a handmade probe result.

    Two of its branches cannot be reached with a file ffmpeg will
    actually write -- a container tag that disagrees with the codec, and
    a second video stream. They are the branches guarding the expensive
    mistake, so they are tested on the decision rather than left to an
    argument about whether such a file exists.
    """
    import json as _json

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.mp4"
        make("copy_engine", f)          # real bytes, so the moov check passes
        _real = V.subprocess.check_output
        V.subprocess.check_output = (
            lambda *a, **k: _json.dumps({"streams": streams}).encode())
        try:
            return V._already_web_ready(f)
        finally:
            V.subprocess.check_output = _real


H264 = {"codec_type": "video", "codec_name": "h264",
        "codec_tag_string": "avc1"}


def test_the_tag_has_to_agree_with_the_codec():
    check("h264 with an agreeing tag is web-ready", probed([H264]), True)
    # THE ONE MISTAKE THAT COSTS SOMETHING. If a probe ever calls
    # cv2's mp4v output h264, the tag is what stops an unplayable file
    # being waved through.
    check("but h264 tagged mp4v is not",
          probed([{**H264, "codec_tag_string": "mp4v"}]), False)
    check("  nor is a missing tag",
          probed([{"codec_type": "video", "codec_name": "h264"}]), False)


def test_it_only_vouches_for_a_single_video_stream():
    check("one video stream is fine", probed([H264]), True)
    check("two is not", probed([H264, dict(H264)]), False)
    check("none is not", probed([{"codec_type": "audio",
                                  "codec_name": "aac"}]), False)
    # AAC alongside is fine; the transcode would have left it as AAC.
    check("control, aac audio does not disqualify it",
          probed([H264, {"codec_type": "audio", "codec_name": "aac"}]), True)


def test_a_truncated_file_says_transcode():
    """A half-written upload must not be waved through."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "half.mp4"
        make("copy_engine", f)
        f.write_bytes(f.read_bytes()[:200])
        check("a truncated clip is not web-ready",
              V._already_web_ready(f), False)


if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    print("SKIP: ffmpeg/ffprobe not on PATH")
    sys.exit(0)

for fn in (test_the_probe_reads_each_kind,
           test_a_copy_engine_clip_is_left_alone,
           test_a_cv2_clip_is_still_transcoded,
           test_a_broken_probe_transcodes,
           test_the_tag_has_to_agree_with_the_codec,
           test_it_only_vouches_for_a_single_video_stream,
           test_a_truncated_file_says_transcode):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
