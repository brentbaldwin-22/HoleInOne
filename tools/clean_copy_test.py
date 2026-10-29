"""The 9:16 crop had no clean source to crop from, so it painted instead.

Clip 1049 shipped with the big landscape logo standing in the frame at
three times its intended size, and clip 1051 shipped with a block of
interpolated sky-to-grass gradient smeared where that logo had been.
Opposite symptoms, one cause: neither clip had a `_clean` copy, so both
fell back to ffmpeg's delogo over a rectangle MEASURED from the picture
-- a guess that leaves the logo standing when it comes in small and
smears picture that was never covered when it comes in large.

The clean copy was missing because the overlay stamps in place and only
ONE of the two produce paths saved a copy first. The path that did was
not the path producing anything: every recent clip is a `d3prod-` file,
and the d3 path went straight to apply_intro_overlay_inplace.

What matters here is that the writer and the reader agree on the
filename, because that agreement is what the delogo fallback was
silently covering for. The real clip names are used, not invented ones.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.intro_overlay import clean_copy_path

# Clip 1049's actual deliverable, and the name the vertical render
# probed for and did not find.
REAL = "d3prod-944-eb43dc22-0.mp4"
WANTED = "d3prod-944-eb43dc22-0_clean.mp4"

ADMIN = Path(__file__).resolve().parents[1] / "backend/app/routers/admin.py"


def main():
    fails = []

    def check(label, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + label
              + (f"   {detail}" if detail else ""))
        if not cond:
            fails.append(label)

    print("the name the two sides have to agree on")
    got = clean_copy_path(Path("/var/data/uploads/clips") / REAL)
    check("1049's clean copy is the name the render looked for",
          got.name == WANTED, f"{got.name} vs {WANTED}")
    check("it stays beside the clip, not in some other directory",
          got.parent.as_posix() == "/var/data/uploads/clips", got.parent.as_posix())

    # The suffix has to survive: the vertical probes
    # clean_copy_path(src).name against files on disk, so a copy written
    # as .MP4 or with the dot eaten is a copy nobody finds.
    print("\nnaming holds for the shapes these filenames actually take")
    for name, want in [
        ("d3prod-944-eb43dc22-0.mp4", "d3prod-944-eb43dc22-0_clean.mp4"),
        # A composite deliverable -- the tee/green splice.
        ("d3prod-12-ab.0_composite.mp4", "d3prod-12-ab.0_composite_clean.mp4"),
        # Dots in the stem must not be mistaken for the extension.
        ("swingtest-1.2.3-produced.mp4", "swingtest-1.2.3-produced_clean.mp4"),
    ]:
        out = clean_copy_path(Path(name)).name
        check(f"{name} -> {out}", out == want, f"wanted {want}")

    # Idempotence matters because the writers guard on
    # `if not _clean_copy.exists()`: if the function did not round-trip,
    # a second produce run would write _clean_clean and leave the real
    # one stale.
    once = clean_copy_path(Path(REAL))
    twice = clean_copy_path(once)
    check("a clean copy of a clean copy is a NEW name, not the same one",
          twice.name == "d3prod-944-eb43dc22-0_clean_clean.mp4", twice.name)

    print("\nboth produce paths and the render all call the one function")
    src = ADMIN.read_text()
    # Any line that both names _clean and builds a path is a second
    # spelling of this filename. ("image_clean" is a heat-map debug key
    # and has nothing to do with video, so match on path-building, not
    # on the word.)
    hand_rolled = [
        (n, ln.strip()) for n, ln in enumerate(src.splitlines(), 1)
        if "_clean" in ln and ("with_name(" in ln or ".suffix" in ln)
    ]
    check("no site still builds this filename by hand",
          not hand_rolled, "; ".join(f"line {n}: {t}" for n, t in hand_rolled))
    check("the helper is called in three places (two writers, one reader)",
          src.count("clean_copy_path(") == 3, str(src.count("clean_copy_path(")))

    # The ordering is the whole fix: a copy taken AFTER the overlay is a
    # copy of the stamped frame, which is worse than none -- the render
    # would trust it and crop a logo straight back in.
    print("\nthe copy is taken BEFORE the overlay stamps, in both paths")
    for path_name, marker in [
        ("d3 produce", "d3 produce: clean-copy failed"),
        ("_intro_overlay_for_clip", "clean-copy failed for %s"),
    ]:
        i_copy = src.find(marker)
        check(f"{path_name}: has a clean-copy step", i_copy > 0)
        if i_copy > 0:
            # the next overlay call after the copy
            i_overlay = src.find("apply_intro_overlay_inplace(", i_copy)
            check(f"{path_name}: copy comes before the overlay call",
                  i_overlay > i_copy, f"copy@{i_copy} overlay@{i_overlay}")

    print()
    if fails:
        print("FAILURES:", fails)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
