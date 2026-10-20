#!/usr/bin/env python3
"""Does the backlog of missing previews actually get made?

A raw's preview is two artefacts: the poster JPG the Production card
shows, and the H.264 the browser will play. Both are made by a thread
upload_event starts, which parks on the heavy gate behind whatever is
producing -- and a restart kills it there. The clip itself is
untouched: it sits on its card, intact, with "No preview" where its
tile should be, and until this sweep nothing ever came back for it.

The two ways a sweep like this is useless:

  it finds nothing          -- and the backlog stays broken, silently;
  it finds everything       -- and re-encodes every raw on the box,
                               which on a redeploy is every raw there
                               is, because the local-only marker went
                               with the ephemeral disk.

So the test is about SELECTION, which is where both failures live.

    python3 tools/preview_sweep_test.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import cameras  # noqa: E402

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


SRC = ("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1")


def h264(out: Path) -> None:
    ffmpeg(*SRC, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out))


def mpeg4(out: Path) -> None:
    ffmpeg(*SRC, "-c:v", "mpeg4", "-vtag", "mp4v", "-pix_fmt", "yuv420p",
           str(out))


class Bucket:
    """Object storage that knows about some names and not others."""

    def __init__(self, names=()):
        self.names = set(names)
        self.fetched = []

    def exists(self, name):
        return name in self.names

    def ensure_local(self, _dir, name):
        self.fetched.append(name)
        return True


def missing(d: Path, name: str, bucket=None) -> bool:
    saved = (cameras.CLIPS_DIR, cameras.storage)
    cameras.CLIPS_DIR = d
    cameras.storage = bucket or Bucket()
    try:
        return cameras._preview_is_missing(name)
    finally:
        (cameras.CLIPS_DIR, cameras.storage) = saved


def test_it_finds_the_broken_ones():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # A clip with no poster and no playable form: the broken case.
        mpeg4(d / "a.mp4")
        check("no poster, not playable -> needs making",
              missing(d, "a.mp4"), True)
        # Playable but with no poster: the card still has an empty tile.
        h264(d / "b.mp4")
        check("playable but no poster -> still needs making",
              missing(d, "b.mp4"), True)
        # ...AND THE OTHER WAY ROUND, which is the likelier half of a
        # killed post-process rather than a hypothetical: the thumbnail
        # is one frame and lands immediately, the transcode is minutes
        # and is what the restart interrupts. So the usual wreckage is
        # a poster sitting next to a file no browser will play, and a
        # sweep that only looked for missing posters would walk past
        # every one of them.
        mpeg4(d / "d.mp4")
        (d / "d.jpg").write_bytes(b"\xff\xd8\xff")
        check("poster made but not playable -> still needs making",
              missing(d, "d.mp4"), True)


def test_it_leaves_the_finished_ones_alone():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        h264(d / "c.mp4")
        (d / "c.jpg").write_bytes(b"\xff\xd8\xff")
        check("playable with a poster -> nothing to do",
              missing(d, "c.mp4"), False)
        # THE REDEPLOY CASE, and the reason this is not just a marker
        # check. The .h264-ok marker is local-only: the storage sweeper
        # skips it, so it goes with the ephemeral disk. If its absence
        # counted as evidence, the first sweep after a deploy would
        # re-encode every raw on the box.
        check("  control, the marker is absent and it still says no",
              (d / "c.mp4.h264-ok").exists(), False)
        # A poster that lives only in the bucket is still a poster.
        d2 = Path(td) / "two"
        d2.mkdir()
        h264(d2 / "e.mp4")
        check("a poster in the bucket counts",
              missing(d2, "e.mp4", Bucket({"e.jpg"})), False)
        check("  control, without it in the bucket it does not",
              missing(d2, "e.mp4", Bucket()), True)


def test_a_marker_is_taken_at_its_word():
    """It means a transcode already ran, whatever the probe now says."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        mpeg4(d / "f.mp4")               # NOT playable...
        (d / "f.mp4.h264-ok").touch()    # ...but marked done
        (d / "f.jpg").write_bytes(b"\xff\xd8\xff")
        check("a marked clip is not swept again", missing(d, "f.mp4"), False)


def test_a_file_that_is_not_here_is_not_work():
    with tempfile.TemporaryDirectory() as td:
        check("a name with no local file is skipped",
              missing(Path(td), "gone.mp4"), False)


def test_the_sweep_only_touches_what_is_broken():
    """End to end: the selection, the fetch, and the count it reports."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        mpeg4(d / "broken.mp4")                       # needs work
        h264(d / "fine.mp4")
        (d / "fine.jpg").write_bytes(b"\xff\xd8\xff")  # does not

        class Ev:
            def __init__(self, tee, green):
                self.tee_clip_filename = tee
                self.green_clip_filename = green

        class Q:
            def filter(self, *_a):
                return self

            def order_by(self, *_a):
                return self

            def limit(self, *_a):
                return self

            def all(self):
                return [Ev("broken.mp4", "fine.mp4")]

        class DB:
            def query(self, *_a):
                return Q()

            def close(self):
                pass

        done = []
        bucket = Bucket()
        saved = (cameras.CLIPS_DIR, cameras.storage, cameras.SessionLocal,
                 cameras._post_process_raw_clip)
        cameras.CLIPS_DIR = d
        cameras.storage = bucket
        cameras.SessionLocal = lambda: DB()
        cameras._post_process_raw_clip = done.append
        try:
            out = cameras.sweep_missing_previews(limit=10, max_age_hours=72)
        finally:
            (cameras.CLIPS_DIR, cameras.storage, cameras.SessionLocal,
             cameras._post_process_raw_clip) = saved

        check("only the broken one is made", done, ["broken.mp4"])
        check("  and that is what it reports", out["made"], 1)
        check("  having looked at both", out["scanned"], 2)
        # Both are fetched: a file that is not on local disk cannot even
        # be inspected, so the rehydrate comes before the decision.
        check("  control, both were pulled down first",
              sorted(bucket.fetched), ["broken.mp4", "fine.mp4"])


def test_the_limit_is_a_limit():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i in range(4):
            mpeg4(d / f"n{i}.mp4")

        class Ev:
            def __init__(self, t, g):
                self.tee_clip_filename = t
                self.green_clip_filename = g

        class Q:
            def filter(self, *_a): return self
            def order_by(self, *_a): return self
            def limit(self, *_a): return self
            def all(self):
                return [Ev("n0.mp4", "n1.mp4"), Ev("n2.mp4", "n3.mp4")]

        class DB:
            def query(self, *_a): return Q()
            def close(self): pass

        done = []
        saved = (cameras.CLIPS_DIR, cameras.storage, cameras.SessionLocal,
                 cameras._post_process_raw_clip)
        cameras.CLIPS_DIR = d
        cameras.storage = Bucket()
        cameras.SessionLocal = lambda: DB()
        cameras._post_process_raw_clip = done.append
        try:
            out = cameras.sweep_missing_previews(limit=2, max_age_hours=72)
        finally:
            (cameras.CLIPS_DIR, cameras.storage, cameras.SessionLocal,
             cameras._post_process_raw_clip) = saved
        check("it stops at the limit", out["made"], 2)
        # CONTROL: there really were more to do, so the 2 above is the
        # limit biting and not the supply running out.
        check("  control, four were available", len(done) < 4, True)


if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    print("SKIP: ffmpeg/ffprobe not on PATH")
    sys.exit(0)

for fn in (test_it_finds_the_broken_ones,
           test_it_leaves_the_finished_ones_alone,
           test_a_marker_is_taken_at_its_word,
           test_a_file_that_is_not_here_is_not_work,
           test_the_sweep_only_touches_what_is_broken,
           test_the_limit_is_a_limit):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
