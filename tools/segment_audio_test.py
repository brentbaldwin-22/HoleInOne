#!/usr/bin/env python3
"""Does the camera's audio survive the ring, the concat and the cut?

WHY THIS MATTERS. The clip the operator watches has five seconds of
silence at the front. That is not a bug in the mux -- it is correct.
The ring holds footage from BEFORE the trigger, the separate audio
recorder only starts AT the trigger, so the pre-roll has no sound and
mux_audio_into_video pads it with exactly enough silence to keep the
rest aligned.

Putting the camera's own AAC track in the segments fixes it at the
source: the pre-roll has sound, sync is sample-accurate because it is
one container, and there is no second RTSP session and no mux pass.

Three places have to carry the audio and all three are tested with
REAL ffmpeg against a REAL file, because "the flags look right" is how
a silent clip ships:

  1. the segment writer       (copycap SegmentRing._cmd)
  2. the concat of a span     (copycap extract)
  3. the swing sub-cut        (copyengine _subcut)

And one thing has to NOT happen: the separate recorder must not also
run, because its mux maps 0:v:0 + 1:a:0 and would throw the camera's
track away in favour of its own.

    python3 tools/segment_audio_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.common import audio_in_segments  # noqa: E402
from agent import copycap, copyengine  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def streams(path: Path) -> list[str]:
    """codec_type of every stream in a file, via real ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return sorted(s.get("codec_type")
                      for s in json.loads(out.stdout).get("streams", []))
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------
# 1. The setting
# ---------------------------------------------------------------------

check("off by default — a camera with no mic must not get silent segments",
      audio_in_segments({}), False)
check("audio on but in_segments unset is still the separate recorder",
      audio_in_segments({"audio": {"enabled": True}}), False)
check("both set turns it on",
      audio_in_segments({"audio": {"enabled": True, "in_segments": True}}),
      True)
# CONTROL: audio off wins. Without this, `enabled: false` plus a
# leftover `in_segments: true` would put audio back in the segments on
# a rig somebody had deliberately silenced.
check("audio disabled beats in_segments",
      audio_in_segments({"audio": {"enabled": False, "in_segments": True}}),
      False)


if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    print("\nffmpeg/ffprobe missing — skipping the pipeline checks")
    sys.exit(1 if FAIL else 0)


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    src = tmp / "src.ts"
    # A stand-in for the camera: H.264 video + 16 kHz mono AAC, which
    # is what the Hanwha with a mic on it actually sends.
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
        "-c:a", "aac", "-ar", "16000", "-ac", "1",
        "-f", "mpegts", str(src),
    ], check=True, capture_output=True, timeout=120)
    check("the stand-in camera stream has both tracks",
          streams(src), ["audio", "video"])

    def segment(keep_audio: bool, into: Path) -> list[Path]:
        """Run the REAL SegmentRing command against the file."""
        into.mkdir(parents=True, exist_ok=True)
        ring = copycap.SegmentRing(
            source=str(src), work_dir=into, segment_seconds=1.0,
            keep_audio=keep_audio,
            # -re READS AT NATIVE RATE, which is what makes this test
            # honest rather than merely green. Segment filenames come
            # from strftime at one-second granularity; without -re
            # ffmpeg chews the six-second file in a fraction of a
            # second, every segment lands in the SAME wall-clock
            # second, and they overwrite each other down to one file.
            # The concat below was then "joining" a single segment and
            # proving nothing. The real ring reads RTSP in real time,
            # so this is also the faithful simulation.
            input_args=["-re", "-i", str(src)],
        )
        subprocess.run(ring._cmd(), check=True, capture_output=True,
                       timeout=120)
        return sorted(into.glob("*.ts"))

    kept = segment(True, tmp / "on")
    stripped = segment(False, tmp / "off")
    check("segments written with keep_audio carry the audio",
          streams(kept[0]), ["audio", "video"])
    # CONTROL: the old behaviour is intact when the flag is off, so a
    # rig that has not opted in is unchanged.
    check("segments written without it are video only",
          streams(stripped[0]), ["video"])
    # NOT a comparison of the two counts. Segment filenames come from
    # strftime at one-second granularity, and ffmpeg chews through a
    # six-second file in well under a second, so segments overwrite
    # each other and the count reflects wall-clock timing rather than
    # anything under test. What the concat below needs is simply more
    # than one segment to join.
    check("the span is more than one segment, so the concat is a real join",
          (len(kept) > 1, len(stripped) > 1), (True, True))

    # 2. THE CONCAT. extract() joins a span of segments with -c copy,
    # which should carry every stream -- but "should" is why this is
    # here.
    joined = tmp / "joined.mp4"
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in kept))
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
        str(joined),
    ], check=True, capture_output=True, timeout=120)
    check("a concat of those segments still has audio",
          streams(joined), ["audio", "video"])

    # 3. THE SWING SUB-CUT, the real _subcut from copyengine. A clip
    # that had sound until swing select trimmed it would be the worst
    # version of this bug: it only shows up on the clips that matter.
    cut = tmp / "cut.mp4"
    ok = copyengine._subcut(joined, cut, 1.0, 4.0)
    check("_subcut succeeded", ok, True)
    check("and the swing cut still has audio", streams(cut),
          ["audio", "video"])


# ---------------------------------------------------------------------
# 4. The lock: the separate recorder must not also run
# ---------------------------------------------------------------------

class FakeRunner:
    cfg: dict = {}


def built_a_recorder(keep_audio: bool) -> bool:
    """Does the REAL decision reach build_audio_recorder?

    Calls CopyEngine._audio_recorder_for rather than restating its
    condition. The first version of this test copied the `if` into
    itself, so deleting the guard from the engine entirely changed
    nothing it could see -- a mutation run caught that, not the test.
    """
    eng = copyengine.CopyEngine.__new__(copyengine.CopyEngine)
    eng.keep_audio = keep_audio
    eng.clips_dir = Path("/tmp")
    hit: list = []
    real = copyengine.build_audio_recorder
    copyengine.build_audio_recorder = lambda *a, **k: (hit.append(1), "rec")[1]
    try:
        got = eng._audio_recorder_for(FakeRunner())
    finally:
        copyengine.build_audio_recorder = real
    return bool(hit) and got is not None


# THE CONFIG HAS TO REACH THE ENGINE, AND THE RING. Everything above
# tests objects built by hand with keep_audio set directly; from_config
# dropping it would leave every real rig on the old path with every
# check still green.
def _engine(cfg):
    return copyengine.CopyEngine.from_config(
        cfg, Path(tempfile.mkdtemp()))


_RTSP = {"capture_engine": "copy", "camera": {"device": "rtsp://x/y"}}
_on = _engine({**_RTSP, "audio": {"enabled": True, "in_segments": True}})
_off = _engine({**_RTSP, "audio": {"enabled": True}})
check("from_config carries in_segments to the engine", _on.keep_audio, True)
check("...and all the way to the ring", _on.ring.keep_audio, True)
# CONTROL: and leaves both off when the config does not ask for it.
check("a config without in_segments gets neither",
      (_off.keep_audio, _off.ring.keep_audio), (False, False))

check("with audio in the segments, no second recorder is built",
      built_a_recorder(True), False)
# CONTROL: and it IS built otherwise, or every rig goes silent.
check("without it, the separate recorder still runs",
      built_a_recorder(False), True)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
