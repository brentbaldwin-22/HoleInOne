#!/usr/bin/env python3
"""A busy produce queue must not mean nothing is ever recovered.

The recovery sweep refuses to run while produce is busy, and that rule
is right: the produce queue sorts oldest-first, so anything the sweep
adds goes in FRONT of the shot somebody has just hit. Feeding it on a
timer put a day's backlog ahead of live golf.

But on a busy morning the queue has something in it all day. "Wait for
a gap" became "never", and events sat at "Both clips in - STUCK" with
nothing coming for them. The gate was there to protect live golf from
the backlog; it ended up protecting the backlog from ever being
cleared. Seen as a run of events 32-34 minutes old, every clip
uploaded, nothing produced.

So past camera_recover_urgent_seconds an event is taken anyway -- ONE
at a time rather than _RECOVER_PER_SWEEP, because the point is to
share the queue rather than own it.

    python3 tools/recover_urgent_test.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import cameras  # noqa: E402
import app.routers.admin as admin  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


NOW = datetime(2026, 10, 20, 12, 0, 0)
URGENT = 1800


class Ev:
    def __init__(self, eid, age_sec):
        self.id = eid
        self.status = "paired_uploaded"
        self.triggered_at = NOW - timedelta(seconds=age_sec)
        self.produce_retries = 0
        self.tee_clip_filename = "tee.mp4"
        self.green_camera_id = None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conds):
        rows = self.rows
        for c in conds:
            rows = [r for r in rows if c(r)]
        return FakeQuery(rows)

    def order_by(self, key):
        return FakeQuery(sorted(self.rows, key=lambda r: r.triggered_at))

    def all(self):
        return list(self.rows)


class FakeCol:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return lambda r: getattr(r, self.name) == other

    def __lt__(self, other):
        return lambda r: getattr(r, self.name) < other

    def __ge__(self, other):
        return lambda r: getattr(r, self.name) >= other

    def isnot(self, other):
        return lambda r: getattr(r, self.name) is not other

    def asc(self):
        return "asc"


class FakeEventModel:
    status = FakeCol("status")
    triggered_at = FakeCol("triggered_at")
    tee_clip_filename = FakeCol("tee_clip_filename")


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _m):
        return FakeQuery(self.rows)

    def get(self, _m, pk):
        return next((r for r in self.rows if r.id == pk), None)

    def commit(self):
        pass

    def close(self):
        pass


class Knobs:
    camera_tee_only_fallback_seconds = 0
    camera_paired_stall_seconds = 600
    camera_produce_max_retries = 3
    camera_recover_max_age_hours = 24
    camera_green_alive_seconds = 0
    camera_tee_only_max_wait_seconds = 3600
    camera_recover_urgent_seconds = URGENT


BUSY = {"depth": 4, "running_upload_id": None, "queued_upload_ids": [1, 2]}
IDLE = {"depth": 0, "running_upload_id": None, "queued_upload_ids": []}


def sweep(rows, queue_state, urgent=URGENT):
    seen: list[int] = []
    Knobs.camera_recover_urgent_seconds = urgent
    saved = (cameras.CameraEvent, cameras.SessionLocal, cameras.settings,
             cameras._utcnow_naive, cameras._run_camera_event_job,
             admin.produce_queue_status)
    cameras.CameraEvent = FakeEventModel
    cameras.SessionLocal = lambda: FakeDB(rows)
    cameras.settings = Knobs()
    cameras._utcnow_naive = lambda: NOW
    # (event id, may it produce without a green). The fallback is the
    # one caller allowed to say yes; the stall path must not.
    cameras._run_camera_event_job = (
        lambda eid, allow_tee_only=False: seen.append(eid))
    admin.produce_queue_status = lambda: queue_state
    try:
        cameras._camera_event_recovery_sweep()
        for _ in range(500):
            if seen:
                break
            time.sleep(0.002)
        time.sleep(0.05)
    finally:
        (cameras.CameraEvent, cameras.SessionLocal, cameras.settings,
         cameras._utcnow_naive, cameras._run_camera_event_job,
         admin.produce_queue_status) = saved
    return sorted(seen)


def test_an_abandoned_event_is_taken_even_on_a_busy_queue():
    rows = [Ev(21, 2400)]          # forty minutes, both clips in
    check("past the urgent age it goes anyway", sweep(rows, BUSY), [21])
    # CONTROL: the same event with the override off is the old
    # behaviour -- a busy queue means nothing happens.
    for r in rows:
        r.produce_retries = 0
    check("control, with the override off it waits for ever",
          sweep(rows, BUSY, urgent=0), [])


def test_a_merely_slow_produce_is_still_left_alone():
    """The gate's real job, which must survive the escape hatch.

    Eleven minutes is past the stall window but nowhere near
    abandoned, and on a busy queue it is far more likely waiting its
    turn than dead. Recovering it would put it in front of live golf.
    """
    rows = [Ev(22, 660)]
    check("a recently stalled event waits while produce is busy",
          sweep(rows, BUSY), [])
    # CONTROL: the same event IS taken the moment the queue is idle.
    for r in rows:
        r.produce_retries = 0
    check("control, an idle queue takes it at once",
          sweep(rows, IDLE), [22])


def test_a_busy_sweep_takes_one_not_two():
    """Sharing the queue, not owning it."""
    rows = [Ev(31, 3600), Ev(32, 3500), Ev(33, 3400)]
    got = sweep(rows, BUSY)
    check("one event on a busy queue", len(got), 1)
    check("and it is the oldest", got, [31])
    # CONTROL: an idle queue takes the full allowance.
    for r in rows:
        r.produce_retries = 0
    check("control, an idle queue takes _RECOVER_PER_SWEEP",
          len(sweep(rows, IDLE)), cameras._RECOVER_PER_SWEEP)


def test_the_retry_budget_still_bounds_it():
    """The escape hatch must not become a crash loop.

    A clip that kills the process on produce would otherwise be handed
    back to produce every minute for ever, now that a busy queue no
    longer stops the sweep.
    """
    rows = [Ev(41, 3600)]
    rows[0].produce_retries = Knobs.camera_produce_max_retries
    check("an event out of retries is not taken, however old",
          sweep(rows, BUSY), [])
    # CONTROL: one retry below the budget and it goes.
    rows[0].produce_retries = Knobs.camera_produce_max_retries - 1
    check("control, one below the budget still goes",
          sweep(rows, BUSY), [41])


def test_the_default_is_on_and_sane():
    from app.config import settings
    _u = int(settings.camera_recover_urgent_seconds)
    check("the escape hatch ships on", _u > 0, True)
    check("and is longer than the stall window it overrides",
          _u > int(settings.camera_paired_stall_seconds), True)


for fn in (test_an_abandoned_event_is_taken_even_on_a_busy_queue,
           test_a_merely_slow_produce_is_still_left_alone,
           test_a_busy_sweep_takes_one_not_two,
           test_the_retry_budget_still_bounds_it,
           test_the_default_is_on_and_sane):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
