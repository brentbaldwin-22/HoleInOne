#!/usr/bin/env python3
"""The flow's audio cuts survive a deploy.

THE DISK HERE IS WIPED EVERY TIME THE APP IS PUBLISHED. Everything in
`uploads/clips/` is mirrored into object storage against exactly that
-- except that the mirror had three holes, and the flow's cuts fell
through all three:

  THE SWEEPER NEVER SAW THEM. It walked `clips_dir.glob("*")`, which
  is the top level only, and a cut lives in `_flowcuts/`. So no cut
  was ever uploaded.

  THE KEY WOULD HAVE BEEN WRONG. `ensure_local` took `Path(name).name`
  -- the bare filename -- so even a mirrored cut would have come back
  down as `clips/flowcut-1406-00-tee.mp4` while the row that named it
  says `clips/_flowcuts/flowcut-1406-00-tee.mp4`. The download
  "succeeds" and the file is still missing: the worst kind of failure,
  the kind that reports success.

  AND THE URL NEVER REACHED THE HANDLER. `/uploads/clips/{name}`
  matches ONE path segment, and a cut's URL has two. It fell through
  to the static mount, which reads local disk and nothing else.

The visible symptom was a cut made yesterday saying "the file is not
on disk any more" today -- which read as data loss, because it was.

    python3 tools/flowcut_mirror_test.py
"""
import inspect
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import storage as S  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class FakeBucket:
    """Enough of the object-storage client to watch what is asked of it."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_from_filename(self, name, path):
        self.objects[name] = Path(path).read_bytes()

    def download_to_filename(self, name, path):
        if name not in self.objects:
            raise Exception("object %s could not be found" % name)
        Path(path).write_bytes(self.objects[name])

    def delete(self, name):
        if name not in self.objects:
            raise Exception("object %s could not be found" % name)
        del self.objects[name]
        self.deleted.append(name)

    def list(self):
        return list(self.objects)

    def exists(self, name):
        return name in self.objects


def with_bucket():
    bucket = FakeBucket()
    S._client = bucket
    S._enabled = True
    return bucket


print(__doc__.strip().splitlines()[0])

print("\n1. the key keeps the folder, and only for the folders that want it")
check("a plain clip is keyed by its name", S.key_for("a.mp4"), "a.mp4")
check("a flow cut keeps its folder",
      S.key_for("_flowcuts/a.mp4"), "_flowcuts/a.mp4")
# `_testcuts/` IS SCRATCH -- the next press of Test Cut deletes it --
# so mirroring it would push megabytes per press into a bucket nothing
# reads back. It flattens, which is the old behaviour, and the sweeper
# never offers it at all.
check("scratch does not", S.key_for("_testcuts/a.mp4"), "a.mp4")
check("an absolute path keeps only what matters",
      S.key_for("/srv/uploads/clips/_flowcuts/a.mp4"), "_flowcuts/a.mp4")
check("nothing is not a key", S.key_for(None), "")

print("\n2. the sweeper walks into the mirrored folder")
with tempfile.TemporaryDirectory() as _d:
    clips = Path(_d)
    (clips / "_flowcuts").mkdir()
    (clips / "_testcuts").mkdir()
    (clips / "plain.mp4").write_bytes(b"x")
    (clips / "_flowcuts" / "flowcut-1406-00-tee.mp4").write_bytes(b"y")
    (clips / "_testcuts" / "scratch.mp4").write_bytes(b"z")
    keys = dict(S._sweepable(clips))
    check("the top level is still swept", "plain.mp4" in keys)
    check("...and so is the cut, under its folder",
          "_flowcuts/flowcut-1406-00-tee.mp4" in keys)
    check("...pointing at the file it actually is",
          keys["_flowcuts/flowcut-1406-00-tee.mp4"].read_bytes(), b"y")
    # NOT `rglob`. A sweep that walked everything would carry the
    # scratch folder with it.
    check("the scratch folder is left alone",
          [k for k in keys if "scratch" in k], [])

print("\n3. a cut comes back where its row says it is")
with tempfile.TemporaryDirectory() as _d:
    clips = Path(_d)
    bucket = with_bucket()
    (clips / "_flowcuts").mkdir()
    src = clips / "_flowcuts" / "flowcut-1406-00-tee.mp4"
    src.write_bytes(b"the cut")
    S.upload(S.key_for("_flowcuts/flowcut-1406-00-tee.mp4"), src)
    check("it is in the bucket under its folder",
          "_flowcuts/flowcut-1406-00-tee.mp4" in bucket.objects)
    # THE DEPLOY.
    src.unlink()
    (clips / "_flowcuts").rmdir()
    got = S.ensure_local(clips, "_flowcuts/flowcut-1406-00-tee.mp4")
    check("it is pulled back down", got)
    check("...to the path the AudioCut row names",
          (clips / "_flowcuts" / "flowcut-1406-00-tee.mp4").exists())
    check("...and NOT flattened into the clips folder",
          (clips / "flowcut-1406-00-tee.mp4").exists(), False)
    check("...with the footage intact",
          (clips / "_flowcuts" / "flowcut-1406-00-tee.mp4").read_bytes(),
          b"the cut")

print("\n4. and a deleted cut is forgotten, not resurrected")
with tempfile.TemporaryDirectory() as _d:
    clips = Path(_d)
    bucket = with_bucket()
    (clips / "_flowcuts").mkdir()
    p = clips / "_flowcuts" / "flowcut-1406-00-tee.mp4"
    p.write_bytes(b"old")
    S.upload(S.key_for("_flowcuts/flowcut-1406-00-tee.mp4"), p)
    p.unlink()
    S.remove("_flowcuts/flowcut-1406-00-tee.mp4")
    check("the bucket's copy is gone too", bucket.objects, {})
    check("...and so nothing pulls it back",
          S.ensure_local(clips, "_flowcuts/flowcut-1406-00-tee.mp4"), False)
    # A SWEEP MUST NOT FAIL ON A FILE THE BUCKET NEVER HELD.
    check("removing what was never mirrored is not an error",
          S.remove("_flowcuts/never-there.mp4"))

S._enabled = False
S._client = None

print("\n5. every delete un-mirrors")
import app.routers.production_flow as F  # noqa: E402
from app.services import retention as R  # noqa: E402

_clear = inspect.getsource(F._clear_previous)
check("a re-cut clears the bucket copy of what it deleted",
      "_storage.remove(name)" in _clear)
# A RE-CUT WRITES THE SAME NAMES AGAIN: `flowcut-<upload>-<idx>`. A
# stale object left behind is the PREVIOUS run's footage sitting under
# the new run's row, waiting for the next deploy to hand it back.
check("...and says why", re.search(r"same name again", _clear) is not None)
_ret = inspect.getsource(R)
check("the retention sweep does too", "_storage.remove(name)" in _ret)
# ── AND THE THIRD ONE ────────────────────────────────────────────
#
# `delete_flow_cut` is the per-cut delete behind "back to Raw clips"
# on the cuts column. It arrived before the mirror existed and took
# the local file only, which is the same stale-object bug as a re-cut
# and MORE likely to fire: that button exists so a mis-marked swing
# can be thrown away and marked again, and `flow_manual_cut` numbers
# from the highest index in use -- so deleting the last hand-made cut
# frees its number for the very next one, which then writes the exact
# name the bucket is still holding.
_one = inspect.getsource(F.delete_flow_cut)
check("deleting one cut clears its bucket copy too",
      "_storage.remove(name)" in _one)
check("...for both angles, not just the tee",
      'for field in ("tee_filename", "green_filename"):' in _one)
# The un-mirror must be INSIDE the per-field loop. Outside it, only
# the last name is cleared and the tee copy survives the delete.
_loop = _one.split('for field in ("tee_filename", "green_filename"):')[1]
check("...inside the loop over the two names",
      "_storage.remove(name)" in _loop)
check("...and says why", re.search(r"same name again", _one) is not None)

print("\n6. a cut is mirrored when it is made, not a minute later")
_src = inspect.getsource(F)
check("there is an immediate mirror", "def _mirror_cut(" in _src)
check("the flow cutter calls it",
      len(re.findall(r"_mirror_cut\(", _src)) >= 3)
# THE SWEEPER RUNS EVERY 30 SECONDS AND SKIPS ANYTHING WRITTEN IN THE
# LAST 20, so a cut is unprotected for up to a minute -- and a deploy
# inside that minute takes it. ffmpeg has already returned; there is
# nothing to wait for.
check("...and says what the wait would cost",
      "unprotected" in inspect.getsource(F._mirror_cut))

print("\n7. the page offers a cut that is only in the bucket")
# BEFORE: `(CLIPS_DIR / fn).exists()` -- so on a fresh deploy every
# cut on the page lost its link and gained "the file is not on disk
# any more", which is the sentence for a file that was cleared out
# from under the row. It had not been. It was in the bucket.
_has = inspect.getsource(F._cut_has_file)
check("presence means disk OR bucket", "_bucket" in _has)
check("the link is built from that", "_cut_has_file(fn, index)"
      in inspect.getsource(F._cut_row))
check("...and so is the reason there is none",
      "_cut_has_file(_fn, index)" in inspect.getsource(F._cut_why_no_file))
# ONE SCAN AND ONE LISTING FOR THE WHOLE PAGE. Asking the bucket per
# row would be two network calls per cut on a page of forty.
check("the index is taken once per page",
      len(re.findall(r"_index = _source_index\(\)",
                     inspect.getsource(F.flow_cuts))), 1)
check("...and the cuts folder is in it",
      "MIRRORED_SUBDIRS" in inspect.getsource(F._source_index))

print("\n8. and the URL reaches a handler that knows about the bucket")
_main = (ROOT / "backend" / "app" / "main.py").read_text()
# `/uploads/clips/{name}` MATCHES ONE SEGMENT. `_flowcuts/x.mp4` is
# two, so every cut URL fell past it to the static mount -- local disk
# only, no bucket, 404 after every deploy.
check("two segments are routed",
      '@app.get("/uploads/clips/{sub}/{name}"' in _main)
check("...before the static mount gets them",
      _main.index('"/uploads/clips/{sub}/{name}"')
      < _main.index('app.mount("/uploads"'))
check("...and it rehydrates from the bucket",
      'storage.ensure_local(CLIPS_DIR, "%s/%s" % (sub, safe))' in _main)
# ONLY THE MIRRORED FOLDERS, and only a bare filename under one: this
# is a path from a URL.
check("...only for folders that are mirrored",
      "if sub not in storage.MIRRORED_SUBDIRS:" in _main)
check("...and cannot be walked outwards",
      "safe = Path(name).name" in _main.split("def serve_clip_in_subdir")[1]
      .split("return _ranged_file")[0])

print("\n" + ("all ok" if not FAILED
               else "%d FAILED:\n  %s" % (len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
