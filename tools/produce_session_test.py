#!/usr/bin/env python3
"""Is a DB session held open across the slow part?

If it is, the transaction sits IDLE for minutes, Postgres kills the
connection on idle_in_transaction_timeout, and the next query dies with
"SSL connection has been closed unexpectedly". Observed in the field on
11 Oct, and duration-dependent, which is what made it look random:

    36 s, 79 s     -> processed
    114 s, 126 s   -> FAILED

Both failures were clips that had already survived the whole cellular
link — 50.2 MB and 55.4 MB landed on the server and the backend threw
them away.

WHAT THE SLOW PART IS HAS CHANGED. This test used to watch the
rehydrate and the re-encode inside _run_camera_event_job. Those are
gone from it: produce rehydrates its own sources, upload_event already
kicks the transcode as each clip lands, and doing them here only put
two ffmpeg-scale jobs in front of the produce queue. That they stay
gone is camera_queue_handoff_test's job.

What is left in this function that can block at all is the enqueue, so
that is what this watches now. The rule is the same one, and it is the
rule rather than the call site that matters: no session is checked out
while something slow runs.

    python3 tools/produce_session_test.py
"""
from __future__ import annotations

import sys
import types
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
        self.held_during_enqueue = []
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


class FakeQuery:
    def filter(self, *_a):
        return self

    def first(self):
        return None            # no linked upload yet: make a new one


def make_session_factory(tracker):
    class FakeSession:
        def __init__(self):
            tracker.open += 1
            self._closed = False

        def close(self):
            if not self._closed:
                self._closed = True
                tracker.open -= 1

        def get(self, _model, _pk):
            return tracker.event

        def query(self, *_a, **_k):
            return FakeQuery()

        def add(self, obj):
            obj.id = 501

        def refresh(self, _obj):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    return FakeSession


def run(tracker):
    """Drive the job with a stub admin module and count open sessions."""
    admin = types.ModuleType("app.routers.admin")

    def _enqueue(upload_id, hole_number=None, wait=False, **_k):
        # THE SLOW CALL, as far as this function is concerned. Even with
        # wait=False the queue takes a lock, and the rule does not
        # depend on how slow "slow" turns out to be.
        tracker.held_during_enqueue.append(tracker.open)
        return {"queued": True}

    admin.enqueue_produce_job = _enqueue

    class _LVUMeta(type):
        def __getattr__(cls, name):
            return _Col(name)

    class _Col:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return lambda r: getattr(r, self.name, None) == other

    class FakeLVU(metaclass=_LVUMeta):
        def __new__(cls, **kw):
            obj = types.SimpleNamespace(**kw)
            obj.id = None
            return obj

    models = types.ModuleType("app.models")
    models.LongVideoUpload = FakeLVU

    saved_mods = {k: sys.modules.get(k)
                  for k in ("app.routers.admin", "app.models")}
    saved = cameras.SessionLocal
    sys.modules["app.routers.admin"] = admin
    sys.modules["app.models"] = models
    try:
        cameras.SessionLocal = make_session_factory(tracker)
        cameras._process_camera_event_job(77)
    finally:
        cameras.SessionLocal = saved
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def main() -> int:
    tracker = Tracker()
    tracker.event = FakeEvent()
    run(tracker)

    # CONTROL FIRST. Without it, a function that never reached the
    # enqueue at all scores a perfect zero sessions held.
    check("the enqueue actually ran (control)",
          len(tracker.held_during_enqueue), 1)
    check("NO session is held across it",
          max(tracker.held_during_enqueue, default=-1), 0)
    check("and every session is closed when the job ends", tracker.open, 0)
    # ...and the event did reach the queue, so this is the real path and
    # not an early return that happens to hold nothing.
    check("  having marked the event queued", tracker.event.status, "queued")

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
