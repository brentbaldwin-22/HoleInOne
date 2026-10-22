"""An event whose upload finished must not keep saying it is queued.

The camera event's status is DERIVED from the upload row, written at
the moment the upload reaches a terminal status. That works only if
every writer remembers to call the mirror, and one did not -- so a pair
produced through the edit wizard read "Produced 1/1" on Production and
"Queued - STUCK" on Cameras at the same time, with no way out but
re-doing work that was already done.

Each assertion is paired with a control, because a reconciler that
rewrote every row would pass "the stuck one got fixed" and quietly
destroy the states that were right.
"""
import os, sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, CameraEvent, LongVideoUpload
from app.routers.admin import _reconcile_unfinished_events


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


_n = [0]


def _pair(db, ev_status, up_status, err=None):
    _n[0] += 1
    ev = CameraEvent(status=ev_status, session_id=f"s{_n[0]}",
                     tee_camera_id=1, course_id=1, hole_number=8)
    db.add(ev)
    db.flush()
    up = LongVideoUpload(
        camera_event_id=ev.id,
        processing_status=up_status,
        last_error=err,
        course_id=1,
        base_captured_at=datetime(2026, 10, 21, 17, 0, 0),
        tee_filename="tee.mp4",
    )
    db.add(up)
    db.flush()
    return ev, up


def main():
    db = _session()

    # The bug: produced, but the event never heard.
    done_ev, _ = _pair(db, "queued", "completed")
    # ...and the same for a produce that genuinely failed.
    failed_ev, _ = _pair(db, "queued", "failed", err="tee source missing")
    # A capture with no golf in it is NOT a failure: the Pi did its job.
    noswing_ev, _ = _pair(db, "queued", "failed",
                          err="no swings detected: 5 chain(s) rose")
    # CONTROLS — none of these may move.
    running_ev, _ = _pair(db, "queued", "processing")
    pending_ev, _ = _pair(db, "paired_uploaded", "pending")
    already_ev, _ = _pair(db, "processed", "completed")
    orphan = CameraEvent(status="queued", session_id="no-upload",
                         tee_camera_id=1, course_id=1, hole_number=8)
    db.add(orphan)
    db.commit()

    rows = db.query(CameraEvent).all()
    n = _reconcile_unfinished_events(db, rows)
    for e in (done_ev, failed_ev, noswing_ev, running_ev, pending_ev,
              already_ev, orphan):
        db.refresh(e)

    print(f"reconciled {n} event(s)")
    print(f"  completed upload  -> {done_ev.status}")
    print(f"  failed upload     -> {failed_ev.status} ({failed_ev.last_error})")
    print(f"  'no swings'       -> {noswing_ev.status}")
    print(f"  still processing  -> {running_ev.status}  (control)")
    print(f"  pending           -> {pending_ev.status}  (control)")
    print(f"  already processed -> {already_ev.status}  (control)")
    print(f"  no upload at all  -> {orphan.status}  (control)")

    assert done_ev.status == "processed", done_ev.status
    assert failed_ev.status == "failed", failed_ev.status
    assert failed_ev.last_error, "a failed event must say why"
    # The one that is easy to get wrong: produce reports "failed" for a
    # clip with no swing in it, which is right for Production and wrong
    # here -- the capture worked. Flagging those red teaches the
    # operator to ignore red.
    assert noswing_ev.status == "processed", (
        f"a capture with no golf in it is not a camera failure: "
        f"{noswing_ev.status}")

    # CONTROLS: an unfinished upload must leave its event alone, or the
    # reconciler marks jobs failed while they are still running.
    assert running_ev.status == "queued", running_ev.status
    assert pending_ev.status == "paired_uploaded", pending_ev.status
    assert already_ev.status == "processed", already_ev.status
    assert orphan.status == "queued", orphan.status

    # Reconciling twice must change nothing further: the listing calls
    # this on every poll.
    n2 = _reconcile_unfinished_events(db, db.query(CameraEvent).all())
    print(f"second pass reconciled {n2} (expected 0 — nothing left unfinished)")
    assert n2 == 0, n2

    print("\nALL PASS")


if __name__ == "__main__":
    main()
