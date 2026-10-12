#!/usr/bin/env python3
"""Is a DB session held open across the media work?

If it is, the transaction sits IDLE for minutes while ffmpeg re-encodes
a clip, Postgres kills the connection on idle_in_transaction_timeout,
and the next query dies with "SSL connection has been closed
unexpectedly". Observed in the field on 11 Oct, and duration-dependent,
which is what made it look random:

    36 s, 79 s     -> processed
    114 s, 126 s   -> FAILED

Both of those failures were clips that had already survived the whole
cellular link — 50.2 MB and 55.4 MB landed on the server and the
backend threw them away.

The same rule was already applied further down the same function, for
the produce wait, with the reason spelled out in a comment. The
rehydrate and the re-encode above it were missed. So this test is not
about ffmpeg or Postgres: it asserts the ONE property that matters —
no session is checked out while slow media work runs.

    python3 tools/produce_session_test.py
"""
from __future__ import annotations

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


class Tracker:
    def __init__(self):
        self.open = 0
        self.peak_during_media = []
        self.event = None


class FakeEvent:
    id = 77
    hole_number = 8
    status = "paired_uploaded"
    last_error = None
    tee_clip_filename = "tee.mp4"
    green_clip_filename = None
    course_id = 1
    triggered_at = None
    session_id = "s"
    produced_clip_id = None


class Stop(RuntimeError):
    """Ends the run once the interesting part is over."""


def make_session_factory(tracker):
    class FakeSession:
        def __init__(self):
            tracker.open += 1
            self._closed = False

        def close(self):
            if not self._closed:
                self._closed = True
                tracker.open -= 1

        def get(self, model, pk):
            return tracker.event

        def query(self, *a, **k):
            # Everything past the media work needs a real database.
            # Stop here: the property under test has already been
            # observed, and the function's own except clause handles it.
            raise Stop("far enough")

        def commit(self):
            pass

        def rollback(self):
            pass

        def add(self, obj):
            pass

        def refresh(self, obj):
            pass

    return FakeSession


def main() -> int:
    tracker = Tracker()
    tracker.event = FakeEvent()

    with tempfile.TemporaryDirectory() as td:
        clips = Path(td)
        (clips / "tee.mp4").write_bytes(b"\0" * 1024)

        def probe(name):
            """Stand-in for the re-encode; records sessions held."""
            tracker.peak_during_media.append(tracker.open)

        def probe_fetch(d, name):
            tracker.peak_during_media.append(tracker.open)

        saved = (cameras.SessionLocal, cameras._post_process_raw_clip,
                 cameras.CLIPS_DIR, cameras.storage.ensure_local)
        cameras.SessionLocal = make_session_factory(tracker)
        cameras._post_process_raw_clip = probe
        cameras.CLIPS_DIR = clips
        cameras.storage.ensure_local = probe_fetch
        try:
            cameras._process_camera_event_job(77)
        finally:
            (cameras.SessionLocal, cameras._post_process_raw_clip,
             cameras.CLIPS_DIR, cameras.storage.ensure_local) = saved

    check("the media work actually ran (control)",
          len(tracker.peak_during_media) >= 2, True)
    check("NO session is held while rehydrating or re-encoding",
          max(tracker.peak_during_media) if tracker.peak_during_media
          else -1, 0)
    check("and every session is closed when the job ends",
          tracker.open, 0)

    # ---- the missing-clip path still reports, with a fresh session ----
    tracker2 = Tracker()
    tracker2.event = FakeEvent()
    tracker2.event.status = "paired_uploaded"
    with tempfile.TemporaryDirectory() as td:
        clips = Path(td)              # deliberately empty: no tee.mp4
        saved = (cameras.SessionLocal, cameras._post_process_raw_clip,
                 cameras.CLIPS_DIR, cameras.storage.ensure_local)
        cameras.SessionLocal = make_session_factory(tracker2)
        cameras._post_process_raw_clip = lambda n: None
        cameras.CLIPS_DIR = clips
        cameras.storage.ensure_local = lambda d, n: None
        try:
            cameras._process_camera_event_job(77)
        finally:
            (cameras.SessionLocal, cameras._post_process_raw_clip,
             cameras.CLIPS_DIR, cameras.storage.ensure_local) = saved
    check("a missing tee clip is still marked failed",
          tracker2.event.status, "failed")
    check("  with the reason", tracker2.event.last_error,
          "tee clip missing on disk")
    check("  and no session left open", tracker2.open, 0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
