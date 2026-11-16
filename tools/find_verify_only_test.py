#!/usr/bin/env python3
"""A find must not delete what Test cut just made.

Find ascents now runs TEST CUT -- `_test_cut_run`, the function behind
that endpoint, with `cut_clips=False`. That is the point: one
detector, one set of judges, one wording, so the two screens cannot
disagree about a swing.

What comes with it is everything else that function does, and one of
those things was wiping `testcut-<upload>-*.mp4` on the way in. It
reads as housekeeping -- a run about to write clips clearing the
previous set -- and it is, for the screen that writes clips. From a
find it is a delete: the operator has the Test cut panel open with
six videos in it, presses Find ascents on the other screen, and the
videos stop loading. Nothing says why, because from the find's side
nothing happened.

So the wipe belongs to the clip-writing half, and a verify-only run
must leave the directory alone. This checks it against the real
function, with a real file on disk.

    python3 tools/find_verify_only_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


from app.routers import admin  # noqa: E402

SRC = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
_i = SRC.index("def _test_cut_run")
_j = SRC.index("\ndef ", _i + 10)
RUN = SRC[_i:_j]

print("\n-- the wipe deletes, so it matters who calls it")
testcuts = admin.CLIPS_DIR / admin.TESTCUTS_SUBDIR
testcuts.mkdir(parents=True, exist_ok=True)
BAIT = testcuts / "testcut-987654321-bait.mp4"
OTHER = testcuts / "testcut-987654322-someone-elses.mp4"
try:
    BAIT.write_bytes(b"not a real mp4")
    OTHER.write_bytes(b"another upload's")
    check("it removes this upload's clips",
          admin._wipe_test_cuts(987654321), 1)
    check("  the file is gone", BAIT.exists(), False)
    # SCOPED TO THE UPLOAD. A glob one character wider would take the
    # whole directory with it.
    check("  and another upload's are untouched", OTHER.exists(), True)
    check("  a second call finds nothing to do",
          admin._wipe_test_cuts(987654321), 0)
finally:
    for _f in (BAIT, OTHER):
        try:
            _f.unlink()
        except OSError:
            pass


print("\n-- and only the clip-writing half calls it")
_i = SRC.index("def _test_cut_run")
_j = SRC.index("\ndef ", _i + 10)
RUN = SRC[_i:_j]
check("there is one call inside the run", RUN.count("_wipe_test_cuts("), 1)
# THE GUARD, read as the two lines it is rather than as indentation.
check("  and `if cut_clips:` is the line before it",
      "if cut_clips:\n        _wipe_test_cuts(upload_id)" in RUN, True)
check("no other caller anywhere",
      SRC.count("_wipe_test_cuts("), 2)  # the def, and that one call
# A VERIFY-ONLY RUN STILL NEEDS THE DIRECTORY: its debug images go
# under it, so moving the mkdir inside the guard would trade a delete
# for a crash.
check("the directory is made either way",
      0 <= RUN.find("mkdir(parents=True") < RUN.find("_wipe_test_cuts("),
      True)


print("\n-- and the find is the caller that depends on this")
_k = SRC.index("def _find_test_cut")
_ftc = SRC[_k:_k + 3000]
check("the find asks for cut_clips=False", "cut_clips=False" in _ftc, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
