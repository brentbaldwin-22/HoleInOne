#!/usr/bin/env python3
"""Can an operator take the box back from the queue?

Produce serialises and it is the heaviest thing here, so a backlog of
automatic camera captures means every Produce, Find Ascents or wizard
run somebody starts by hand sits behind minutes of other people's golf.
Pausing is for working ON production rather than waiting for it.

The trap is pausing the WRONG thing. A pause that also swallowed the
button the operator just pressed would be a broken queue, not a paused
one -- and using that button is the entire reason to pause. So:

  1. automatic work is HELD, not run and not dropped,
  2. the operator's own runs still go straight through,
  3. resuming replays what was held, in its original order,
  4. pressing Produce on a held capture promotes it out of the hold,
  5. and the recovery sweeper stands down, because every attempt it
     makes SPENDS A RETRY -- a long pause would otherwise quietly
     exhaust each stalled event's budget and leave them unrecoverable
     the moment work resumed.

    python3 tools/produce_pause_test.py
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


BASE = datetime(2026, 10, 20, 9, 0, 0)


class FakeRow:
    def __init__(self, i):
        self.id = i
        # Minutes apart, so queue order is well defined and the resume
        # assertion is about order rather than about luck.
        self.created_at = datetime(2026, 10, 20, 9, i, 0)


class FakeDB:
    def get(self, _model, pk):
        return FakeRow(pk)

    def close(self):
        pass


def drain(seen, want, timeout=5.0):
    """Wait until `want` jobs have run, or give up."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(seen) < want:
        time.sleep(0.01)
    return list(seen)


def held(want, timeout=5.0):
    """Wait for the hold to reach `want`.

    The worker does the deferring, not the enqueue, so "held" is true a
    moment after the call rather than during it. Waiting for the number
    rather than sleeping a guessed interval keeps the test about the
    hold instead of about the scheduler.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        n = admin.produce_queue_status()["held"]
        if n >= want:
            return n
        time.sleep(0.01)
    return admin.produce_queue_status()["held"]


def quiet(seen, for_sec=0.4):
    """Assert nothing runs for a while — the hold is the absence."""
    time.sleep(for_sec)
    return list(seen)


def setup():
    """Fresh queue state and a stubbed produce. Returns (seen, restore)."""
    seen: list = []
    gate = threading.Event()
    gate.set()

    def fake_produce(upload_id, hole_number=None, debug_artifacts=False):
        gate.wait(5)
        seen.append(upload_id)
        return {"ok": True}

    saved = admin.run_produce_job
    admin.run_produce_job = fake_produce
    admin.set_produce_paused(False)
    with admin._produce_lock:
        admin._produce_pending.clear()
        admin._produce_deferred.clear()
        while not admin._produce_q.empty():
            try:
                admin._produce_q.get_nowait()
                admin._produce_q.task_done()
            except Exception:  # noqa: BLE001
                break

    def restore():
        admin.set_produce_paused(False)
        admin.run_produce_job = saved

    return seen, gate, restore


def test_a_pause_holds_automatic_work_only():
    seen, _gate, restore = setup()
    try:
        admin.set_produce_paused(True)
        admin.enqueue_produce_job(upload_id=11, db=FakeDB(), manual=False)
        check("an automatic job does not run while paused",
              quiet(seen), [])
        check("  it is held, not dropped", held(1), 1)
        # THE POINT OF PAUSING. The operator's own run is why the queue
        # was paused in the first place.
        admin.enqueue_produce_job(upload_id=22, db=FakeDB(), manual=True)
        check("the operator's own run goes straight through",
              drain(seen, 1), [22])
        check("  and the automatic one is still held",
              admin.produce_queue_status()["held"], 1)
    finally:
        restore()


def test_resuming_replays_the_hold_in_order():
    seen, _gate, restore = setup()
    try:
        admin.set_produce_paused(True)
        for uid in (31, 32, 33):
            admin.enqueue_produce_job(upload_id=uid, db=FakeDB(),
                                      manual=False)
        check("three held", held(3), 3)
        out = admin.set_produce_paused(False)
        check("resuming releases them", out["released"], 3)
        # Oldest first, which is the queue's own rule: a capture from
        # this morning must not jump the one from an hour ago just
        # because a pause reshuffled them.
        check("  and they run in order", drain(seen, 3), [31, 32, 33])
        check("  with nothing left held",
              admin.produce_queue_status()["held"], 0)
    finally:
        restore()


def test_produce_on_a_held_card_promotes_it():
    seen, _gate, restore = setup()
    try:
        admin.set_produce_paused(True)
        admin.enqueue_produce_job(upload_id=41, db=FakeDB(), manual=False)
        check("held to begin with", held(1), 1)
        # Somebody is looking at this capture and has pressed Produce.
        # Still paused -- but no longer automatic.
        admin.enqueue_produce_job(upload_id=41, db=FakeDB(), manual=True)
        check("asking by hand runs it anyway", drain(seen, 1), [41])
        check("  and takes it out of the hold",
              admin.produce_queue_status()["held"], 0)
        check("  while the queue is still paused",
              admin.produce_queue_status()["paused"], True)
        # CONTROL: a SECOND automatic job is still held, so the promotion
        # above was about that one job and not a hidden un-pause.
        admin.enqueue_produce_job(upload_id=42, db=FakeDB(), manual=False)
        check("control, automatic work is still held",
              (quiet(seen), held(1)), ([41], 1))
    finally:
        restore()


def test_the_recovery_sweep_stands_down_while_paused():
    """Because every attempt it makes spends a retry."""
    from app.routers import cameras

    seen, _gate, restore = setup()
    kicked: list = []
    saved = (cameras.produce_queue_status if hasattr(
        cameras, "produce_queue_status") else None)
    saved_run = cameras._run_camera_event_job
    try:
        cameras._run_camera_event_job = (
            lambda eid, allow_tee_only=False: kicked.append(eid))
        admin.set_produce_paused(True)
        cameras._camera_event_recovery_sweep()
        check("a paused queue is not swept", kicked, [])
        # CONTROL: the same sweep with the same settings DOES reach the
        # database when the queue is running -- otherwise the empty list
        # above proves only that the sweep is disabled outright.
        admin.set_produce_paused(False)
        touched: list = []
        _saved_sl = cameras.SessionLocal

        class _Spy:
            def query(self, *_a, **_k):
                touched.append(1)
                raise RuntimeError("far enough")

            def close(self):
                pass

        cameras.SessionLocal = lambda: _Spy()
        try:
            cameras._camera_event_recovery_sweep()
        except RuntimeError:
            pass
        finally:
            cameras.SessionLocal = _saved_sl
        check("control, a running queue IS swept", len(touched) > 0, True)
    finally:
        cameras._run_camera_event_job = saved_run
        if saved is not None:
            cameras.produce_queue_status = saved
        restore()


for fn in (test_a_pause_holds_automatic_work_only,
           test_resuming_replays_the_hold_in_order,
           test_produce_on_a_held_card_promotes_it,
           test_the_recovery_sweep_stands_down_while_paused):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
