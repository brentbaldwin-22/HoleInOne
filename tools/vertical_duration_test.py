"""The 9:16 must be the same length as the clip it was cropped from.

The landscape deliverable ends on DIST_TAIL_SEC of held frame so the
distance can be read, and that tail is added by the distance-plate pass
-- which runs AFTER the clean copy is taken. So the stamped deliverable
carries the tail and the clean copy does not.

While the vertical was cropped from the stamped file that did not
matter: the tail was already in the source, and the vertical's own plate
pass passed tail_sec=0 so as not to add a second one. Cropping from the
clean copy instead made that zero wrong, and the 9:16 came out exactly
DIST_TAIL_SEC short -- 9s against 11s -- with nothing in either picture
to explain it.

This pins the arithmetic with real ffmpeg on real files, because the
whole failure was a reasonable-looking constant that no test disagreed
with.
"""
import os, shutil, subprocess, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["PATH"] = str(Path(FFMPEG).parent) + os.pathsep + os.environ["PATH"]
    _shim = Path(FFMPEG).parent / "ffmpeg"
    if not _shim.exists():
        _shim.symlink_to(FFMPEG)
except ImportError:
    FFMPEG = "ffmpeg"

from app.services.intro_overlay import (
    DIST_TAIL_SEC, apply_distance_plate_inplace, clean_copy_path)

BASE_SECS = 9.0
W, H = 1280, 720
fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def secs(path: Path) -> float:
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        return float(n) / float(fps) if fps else 0.0
    finally:
        cap.release()


def build(path: Path, dur: float) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=0x2e5d34:s={W}x{H}:r=30:d={dur}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True)


def main(tmp: Path):
    print(f"DIST_TAIL_SEC = {DIST_TAIL_SEC}s, base clip = {BASE_SECS}s\n")

    # ---- the produce path: clean copy, then the plate (which pads) ----
    final = tmp / "d3prod-1-aa-0.mp4"
    build(final, BASE_SECS)
    clean = clean_copy_path(final)
    shutil.copy2(final, clean)
    base = secs(final)
    check("base clip built", abs(base - BASE_SECS) < 0.1, f"{base:.2f}s")

    ok = apply_distance_plate_inplace(final, "323 FT")   # tail_sec=None -> default
    check("landscape plate applied", ok)
    if not ok:
        print("\n  (no plate — cannot judge the rest)")
        return 1
    land = secs(final)
    check("the landscape gained the held tail",
          abs(land - (base + DIST_TAIL_SEC)) < 0.12,
          f"{land:.2f}s vs {base + DIST_TAIL_SEC:.2f}s")
    check("the clean copy did NOT gain it (it predates the pass)",
          abs(secs(clean) - base) < 0.05, f"{secs(clean):.2f}s")

    # ---- the vertical, cropped from the clean copy ----
    # THE BUG: tail_sec=0 on a source that never had a tail.
    v_bug = tmp / "vert_tail0.mp4"
    shutil.copy2(clean, v_bug)
    apply_distance_plate_inplace(v_bug, "323 FT", tail_sec=0.0)
    bug = secs(v_bug)
    check("tail_sec=0 leaves the 9:16 short — the reported fault",
          bug < land - 1.0, f"{bug:.2f}s vs landscape {land:.2f}s")
    check("and short by exactly the tail",
          abs((land - bug) - DIST_TAIL_SEC) < 0.12,
          f"short by {land - bug:.2f}s")

    # THE FIX: supply the tail the clean source is missing.
    v_fix = tmp / "vert_tailed.mp4"
    shutil.copy2(clean, v_fix)
    apply_distance_plate_inplace(v_fix, "323 FT", tail_sec=DIST_TAIL_SEC)
    fix = secs(v_fix)
    check("with the tail supplied, the 9:16 matches the landscape",
          abs(fix - land) < 0.12, f"{fix:.2f}s vs {land:.2f}s")
    check("and the card's 0.05s tolerance calls that a match",
          abs(round(fix, 2) - round(land, 2)) <= 0.05,
          f"|{fix:.2f} - {land:.2f}| = {abs(fix - land):.3f}")

    # ---- a stamped source must NOT be padded twice ----
    v_stamped = tmp / "vert_from_stamped.mp4"
    shutil.copy2(final, v_stamped)          # final already carries the tail
    apply_distance_plate_inplace(v_stamped, "323 FT", tail_sec=0.0)
    st = secs(v_stamped)
    check("a stamped/delogo source stays matched on tail_sec=0",
          abs(st - land) < 0.12, f"{st:.2f}s vs {land:.2f}s")
    v_double = tmp / "vert_double.mp4"
    shutil.copy2(final, v_double)
    apply_distance_plate_inplace(v_double, "323 FT", tail_sec=DIST_TAIL_SEC)
    db = secs(v_double)
    check("and would run LONG if it were padded again — so the branch "
          "on source kind is load-bearing",
          db > land + 1.0, f"{db:.2f}s vs {land:.2f}s")

    print()
    if fails:
        print("FAILURES:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sys.exit(main(Path(td)))
