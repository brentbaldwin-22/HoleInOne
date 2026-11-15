#!/usr/bin/env python3
"""A failed splice says WHY, with ffmpeg's own words.

"d3 produce: swing 0 failed: composite splice failed" is the whole
message an operator got when a clip died at the last step. The sentence
has no cause in it, and there was nothing behind it either: of the
failure paths in `splice_impact_clip`, TWO returned False having logged
nothing at all -- the output missing, or zero bytes, after ffmpeg had
reported success -- and the one that did log ran ffmpeg without
capturing stderr, so it could only say "returned non-zero exit status
1" while ffmpeg's actual complaint went to the void.

A produce that dies here is a clip that does not exist. Guessing is not
a debugging strategy for that.

Runs real ffmpeg against real broken inputs.

    python3 tools/splice_reason_test.py
"""
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import video as V  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class Caught(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, rec):
        # getMessage() ALREADY applies rec.args. Doing it again raises
        # inside emit, logging swallows that, and the handler collects
        # nothing -- which reads exactly like "the code logged nothing",
        # the very bug this file exists to prevent.
        try:
            self.lines.append(rec.getMessage())
        except Exception as exc:  # noqa: BLE001
            self.lines.append("<handler broke: %s>" % exc)


def run(tee, green, out):
    """Call the splice and return (ok, the warning text it logged)."""
    h = Caught()
    # THE MODULE'S OWN LOGGER BY NAME. `V.__name__` is
    # "app.services.video"; the module logs to "golfreelz.video",
    # so a handler on the former collects nothing and every
    # assertion below reads as "it logged no reason".
    lg = V.log
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        ok = V.splice_impact_clip(tee, 1.0, green, 1.0, out)
    finally:
        lg.removeHandler(h)
    return ok, " || ".join(x for x in h.lines if "FAILED" in x)


if not V.have_ffmpeg():
    print("ffmpeg is not installed here — cannot run this test honestly")
    sys.exit(0)

TMP = Path(tempfile.mkdtemp(prefix="splice-test-"))


def make_video(path, sec=3.0):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={sec}:size=320x240:rate=30",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={sec}",
         "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=120, stderr=subprocess.PIPE)
    return path


print("\n-- a good splice still works (control)")
good_tee = make_video(TMP / "tee.mp4")
good_green = make_video(TMP / "green.mp4")
ok, why = run(good_tee, good_green, TMP / "out_ok.mp4")
check("a valid pair splices", ok, True)
check("  and logs no failure", why, "")
check("  and writes a non-empty file",
      (TMP / "out_ok.mp4").exists()
      and (TMP / "out_ok.mp4").stat().st_size > 0, True)


print("\n-- a missing tee source is named, not guessed at")
ok, why = run(TMP / "nope.mp4", good_green, TMP / "out_a.mp4")
check("it fails", ok, False)
check("  and says the tee source is not on disk",
      "the tee source is not on disk" in why, True)


print("\n-- a CORRUPT green file reaches ffmpeg and ffmpeg's words come back")
# THE CASE THAT USED TO VANISH. Non-empty, so the `exists() and
# st_size > 0` guard lets it through as "green available"; ffmpeg then
# refuses it. That refusal is the only thing that says what is wrong.
bad_green = TMP / "bad_green.mp4"
bad_green.write_bytes(b"this is not an mp4" * 64)
ok, why = run(good_tee, bad_green, TMP / "out_b.mp4")
check("it fails", ok, False)
check("  and says ffmpeg refused the dual-cam splice",
      "ffmpeg refused the dual-cam splice" in why, True)
check("  and carries ffmpeg's own message, not an exit code",
      "returned non-zero exit status" not in why, True)
check("  and names the inputs",
      "bad_green.mp4" in why and "tee.mp4" in why, True)
# The half-written output must not survive a failure: the caller
# raises, but a stale file left behind is a clip that looks produced.
check("  and removes the half-written output",
      (TMP / "out_b.mp4").exists(), False)


print("\n-- the tee-only fallback reports in the same shape")
ok, why = run(good_tee, None, TMP / "out_c.mp4")
check("no green at all still produces a clip", ok, True)
bad_tee = TMP / "bad_tee.mp4"
bad_tee.write_bytes(b"not an mp4 either" * 64)
ok, why = run(bad_tee, None, TMP / "out_d.mp4")
check("a corrupt tee fails", ok, False)
check("  and says ffmpeg refused the tee-only encode",
      "ffmpeg refused the tee-only encode" in why, True)
check("  with ffmpeg's own message",
      "returned non-zero exit status" not in why and len(why) > 60, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
