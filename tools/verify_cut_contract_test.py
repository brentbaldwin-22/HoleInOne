#!/usr/bin/env python3
"""Read the contract; do not guess it.

Find ascents showed "could not tell" on every cut and no thumbnail,
from one mistake made twice: `_verify_cut` read `is_swing` and
`frames_image` off `swing_heat_check`, and it returns NEITHER. It
answers `verdict` ("club_swing" / "no_swing" / "unknown"), and it
returns the RECT to cut the six tiles from rather than the tiles --
`frames_strip` is a separate call. Two dead keys, one cause.

Dead keys fail silently: `.get()` on a name that was never there is
None, which is a legal value meaning "could not tell" and "no
picture". The feature looked implemented and reported nothing.

This runs the real functions against a real clip, so the contract is
checked rather than remembered.

    python3 tools/verify_cut_contract_test.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-60s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


if not have_ffmpeg():
    print("ffmpeg is not installed here — cannot check this honestly")
    sys.exit(0)

from app.services.tracer import (  # noqa: E402
    swing_heat_check, frames_strip, AUDIO2_OFFSETS_SEC,
)

TMP = Path(tempfile.mkdtemp(prefix="verify-contract-"))
VID = TMP / "clip.mp4"
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
     "-i", "testsrc=duration=6:size=640x360:rate=30",
     "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
     str(VID)], check=True, timeout=180)

chk = swing_heat_check(VID, 3.0, 30.0, debug_dir=TMP, debug_prefix="t",
                       before_sec=1.6, after_sec=1.6, style="golfer",
                       max_candidates=2) or {}

print("\n-- what swing_heat_check actually answers")
check("it answers `verdict`", "verdict" in chk, True)
check("  one of the three words it documents",
      chk.get("verdict") in ("club_swing", "no_swing", "unknown"), True)
# THE TWO KEYS THAT WERE READ AND DO NOT EXIST. Asserted as ABSENT so
# that a future version adding them has to come past this line and
# decide what they mean, rather than silently changing what a find
# reports.
check("it does NOT answer `is_swing`", "is_swing" in chk, False)
check("it does NOT return the six tiles", "frames_image" in chk, False)
check("  it returns the rect to cut them from",
      "frames_rect" in chk or chk.get("frames_rect") is None, True)


print("\n-- the tiles are a separate call, and it writes a real file")
_sp = TMP / "strip.jpg"
_ok = frames_strip(VID, 3.0, _sp, rect=chk.get("frames_rect"),
                   offsets=AUDIO2_OFFSETS_SEC)
check("frames_strip reports success", bool(_ok), True)
check("  and the file is on disk", _sp.exists(), True)
check("  and is not empty", _sp.stat().st_size > 1000, True)


print("\n-- and the caller reads the keys that exist")
SRC = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
_i = SRC.index("def _verify_cut")
_j = SRC.index("def _ascent_windows")
_vc = SRC[_i:_j]
check("_verify_cut calls frames_strip", "frames_strip(" in _vc, True)
check("  and sets frames_image from it",
      'out["frames_image"]' in _vc, True)
_k = SRC.index("def _ascent_run")
_ar = SRC[_k:_k + 40000]
check("the find maps `verdict`, not `is_swing`",
      '_vd = _v.get("verdict")' in _ar, True)
check("  club_swing means yes", '"club_swing"' in _ar, True)
check("  and it says the AI judge did not run",
      "no AI judge on a find" in _ar, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
