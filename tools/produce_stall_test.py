#!/usr/bin/env python3
"""Does a stalled camera event ever get picked back up?

Both clips land, the event goes to 'paired_uploaded', and the produce
job that finishes it runs in a daemon thread owned by that upload
request. A deploy, a restart or an OOM kills the thread; startup's
_reap_orphaned_jobs clears the LongVideoUpload row it left behind but
never looks at the event. Nothing else did either -- the only sweeper
filtered on status == 'tee_uploaded' -- so the event sat at
'paired_uploaded' for ever, showing as "Both clips in · STUCK" on the
Cameras page with the Re-process button the only way out.

Observed 19 Oct: four consecutive events on Rivertowne hole 8, every
clip uploaded, nothing produced.

So this test asserts four properties of the recovery sweep:

  1. a stalled 'paired_uploaded' event IS restarted,
  2. one still inside the stall window is NOT (a produce that is merely
     slow must not be shot at),
  3. the retry budget bounds it, so a clip that kills the process on
     produce cannot become a crash loop built out of the recovery,
  4. and one event never gets two produce jobs at once.

    python3 tools/produce_stall_test.py
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta
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


class Ev:
    def __init__(self, eid, status, age_sec, retries=0, tee="tee.mp4"):
        self.id = eid
        self.status = status
        self.triggered_at = datetime(2026, 10, 19, 12, 0, 0) - timedelta(
            seconds=age_sec)
        self.produce_retries = retries
        self.tee_clip_filename = tee


# A stand-in for the query chain _recoverable_events builds. It applies
# the same filters in python so the test is about WHICH events the sweep
# selects, not about SQLAlchemy.
class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conds):
        rows = self.rows
        for c in conds:
            rows = [r for r in rows if c(r)]
        return FakeQuery(rows)

    def order_by(self, key):
        return FakeQuery(sorted(self.rows, key=key))

    def all(self):
        return list(self.rows)


class FakeCol:
    """Turns `CameraEvent.status == x` into a predicate."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return lambda r: getattr(r, self.name) == other

    def __lt__(self, other):
        return lambda r: getattr(r, self.name) < other

    def __ge__(self, other):
        return lambda r: getattr(r, self.name) >= other

    def isnot(self, other):
        return lambda r: (getattr(r, self.name) is not other)

    def asc(self):
        return lambda r: getattr(r, self.name)


class FakeEventModel:
    status = FakeCol("status")
    triggered_at = FakeCol("triggered_at")
    tee_clip_filename = FakeCol("tee_clip_filename")


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.committed = 0

    def query(self, _model):
        return FakeQuery(self.rows)

    def get(self, _model, pk):
        return next((r for r in self.rows if r.id == pk), None)

    def commit(self):
        self.committed += 1

    def close(self):
        pass


NOW = datetime(2026, 10, 19, 12, 0, 0)


def select(rows, status, window_sec, budget=3, max_age_h=24,
           require_tee=False):
    db = FakeDB(rows)
    return cameras._recoverable_events(
        db, status,
        NOW - timedelta(seconds=window_sec),
        NOW - timedelta(hours=max_age_h),
        budget, require_tee=require_tee,
    )


def main() -> int:
    saved_model = cameras.CameraEvent
    cameras.CameraEvent = FakeEventModel
    try:
        # ---- 1 + 2: the window ----------------------------------------
        rows = [
            Ev(1, "paired_uploaded", 3600),   # an hour stuck
            Ev(2, "paired_uploaded", 120),    # two minutes in: producing
        ]
        got = select(rows, "paired_uploaded", 600)
        check("a paired event stuck past the window is restarted",
              got, [1])
        check("  and one still inside it is left alone (control)",
              2 in got, False)

        # The bug this whole file exists for: the old sweep only ever
        # queried 'tee_uploaded', so the stuck one above was invisible.
        check("the tee-only sweep does NOT also catch it (control)",
              select(rows, "tee_uploaded", 180), [])

        # ---- 3: the retry budget --------------------------------------
        spent = [Ev(3, "paired_uploaded", 3600, retries=3)]
        check("an event that has spent its budget is dropped",
              select(spent, "paired_uploaded", 600), [])
        check("  one retry short of it is still picked up (control)",
              select([Ev(3, "paired_uploaded", 3600, retries=2)],
                     "paired_uploaded", 600), [3])
        check("  and a NULL count from the ALTER reads as zero",
              select([Ev(4, "paired_uploaded", 3600, retries=None)],
                     "paired_uploaded", 600), [4])

        # ---- the age floor --------------------------------------------
        check("nothing older than the floor is resurrected",
              select([Ev(5, "paired_uploaded", 60 * 60 * 48)],
                     "paired_uploaded", 600), [])
        check("  just inside it is (control)",
              select([Ev(5, "paired_uploaded", 60 * 60 * 23)],
                     "paired_uploaded", 600), [5])

        # ---- oldest first, and the tee-only filter --------------------
        check("the oldest stall is restarted first",
              select([Ev(7, "paired_uploaded", 900),
                      Ev(6, "paired_uploaded", 3600)],
                     "paired_uploaded", 600), [6, 7])
        check("a tee_uploaded event with no tee file is not swept",
              select([Ev(8, "tee_uploaded", 3600, tee=None)],
                     "tee_uploaded", 180, require_tee=True), [])
        check("  with one, it is (control)",
              select([Ev(8, "tee_uploaded", 3600)],
                     "tee_uploaded", 180, require_tee=True), [8])
    finally:
        cameras.CameraEvent = saved_model

    # ---- 4: one produce job per event at a time -----------------------
    started = []
    release = threading.Event()

    def slow_job(event_id, allow_tee_only=False, manual=False):
        started.append(event_id)
        release.wait(5)

    saved_run = cameras._run_camera_event_job
    cameras._run_camera_event_job = slow_job
    try:
        first = threading.Thread(
            target=cameras._process_camera_event_job, args=(9,))
        first.start()
        for _ in range(500):             # wait for it to be registered
            if started:
                break
            time.sleep(0.002)
        cameras._process_camera_event_job(9)      # the sweep, kicking it
        check("a second job on the same event is refused while one runs",
              started, [9])
        cameras._process_camera_event_job(10)     # a different event
        check("  a different event still starts (control)",
              started, [9, 10])
        release.set()
        first.join(5)
        cameras._process_camera_event_job(9)      # now it is free again
        check("  and the same event may run again once it finishes",
              started, [9, 10, 9])
        check("  leaving nothing registered as in-flight",
              cameras._event_job_inflight, set())
    finally:
        cameras._run_camera_event_job = saved_run
        release.set()

    # ---- the sweep itself: the budget is SPENT BEFORE the job runs ----
    # A job that kills the process never comes back to have its retry
    # counted, and that is the exact failure the budget exists to bound.
    # So the count has to be committed before the thread starts, and
    # this reads it from inside the job to prove the order.
    rows = [Ev(11, "paired_uploaded", 3600), Ev(12, "paired_uploaded", 3500),
            Ev(13, "paired_uploaded", 3400)]
    seen: list[tuple[int, int]] = []

    def record(event_id, allow_tee_only=False, manual=False):
        ev = next(r for r in rows if r.id == event_id)
        seen.append((event_id, ev.produce_retries))

    class Knobs:
        camera_tee_only_fallback_seconds = 0
        camera_paired_stall_seconds = 600
        camera_produce_max_retries = 3
        camera_recover_max_age_hours = 24
        camera_green_alive_seconds = 0
        camera_tee_only_max_wait_seconds = 3600
        # This suite is about the IDLE-QUEUE rule, so the urgent
        # override is off here and tested on its own in
        # tools/recover_urgent_test.py.
        camera_recover_urgent_seconds = 0

    import app.routers.admin as admin           # noqa: E402

    def run_sweep(queue_state):
        seen.clear()
        for r in rows:
            r.produce_retries = 0
        saved = (cameras.CameraEvent, cameras.SessionLocal, cameras.settings,
                 cameras._utcnow_naive, cameras._run_camera_event_job,
                 admin.produce_queue_status)
        cameras.CameraEvent = FakeEventModel
        cameras.SessionLocal = lambda: FakeDB(rows)
        cameras.settings = Knobs()
        cameras._utcnow_naive = lambda: NOW
        cameras._run_camera_event_job = record
        admin.produce_queue_status = lambda: queue_state
        try:
            cameras._camera_event_recovery_sweep()
            for _ in range(500):
                if len(seen) >= cameras._RECOVER_PER_SWEEP:
                    break
                time.sleep(0.002)
            time.sleep(0.05)      # give a third job a chance to show up
        finally:
            (cameras.CameraEvent, cameras.SessionLocal, cameras.settings,
             cameras._utcnow_naive, cameras._run_camera_event_job,
             admin.produce_queue_status) = saved

    IDLE = {"depth": 0, "running_upload_id": None, "queued_upload_ids": []}

    # RECOVERY NEVER GOES IN FRONT OF LIVE GOLF. The produce queue is
    # ordered oldest-first, so anything the sweep adds sorts ahead of the
    # shot somebody just hit. It may only ever use an idle queue.
    run_sweep({"depth": 3, "running_upload_id": None,
               "queued_upload_ids": [1, 2, 3]})
    check("nothing is recovered while the produce queue has a backlog",
          seen, [])
    run_sweep({"depth": 0, "running_upload_id": 44, "queued_upload_ids": []})
    check("  nor while one is actually producing", seen, [])
    check("  and no retry is spent on a sweep that did nothing",
          [r.produce_retries for r in rows], [0, 0, 0])

    run_sweep(IDLE)
    check("one sweep of an idle queue restarts at most _RECOVER_PER_SWEEP",
          len(seen), cameras._RECOVER_PER_SWEEP)
    check("  the oldest ones", sorted(e for e, _ in seen), [11, 12])
    check("  each already charged a retry before its job ran",
          sorted(n for _, n in seen), [1, 1])
    check("  and the one it did not reach is untouched (control)",
          rows[2].produce_retries, 0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
