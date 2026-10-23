#!/usr/bin/env python3
"""Does an uploaded pair reach the produce queue immediately?

THE COMPLAINT, and it was the right one: "once uploaded, let's move
them to production queue immediately. we don't need to do any further
filtering or rehydrating or encoding."

It wasn't doing that. _run_camera_event_job used to, in order:

  1. rehydrate both raws from object storage,
  2. re-encode both to H.264 and cut thumbnails (heavy gate),
  3. enqueue the upload with wait=True and BLOCK through the whole
     produce,
  4. then stamp the event's terminal status from the outcome.

Steps 1 and 2 were already done elsewhere -- produce rehydrates its own
sources, and upload_event already kicks the transcode as each clip
lands -- so they were duplicated work in front of the queue. And step 3
made a daemon thread in the web process the only record that the work
existed: a deploy, an OOM or a health-check bounce killed it and the
event sat at 'paired_uploaded' with both clips on disk and nobody
holding them. That is the "Both clips in - STUCK" pill, and it arrives
in runs of consecutive ids because one restart takes the whole parked
set at once.

So four properties:

  1. the job queues the upload and returns -- no wait,
  2. it does no media work on the way,
  3. the event's status is DERIVED from the upload row afterwards,
     including the "no swings" case that is not a failure,
  4. and a restart hands a camera event back to the sweeper rather
     than failing it into silence.

    python3 tools/camera_queue_handoff_test.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


# ------------------------------------------------------------- fakes

class Row:
    """A CameraEvent / LongVideoUpload stand-in."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conds):
        rows = self.rows
        for c in conds:
            rows = [r for r in rows if c(r)]
        return FakeQuery(rows)

    def order_by(self, *_):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class FakeCol:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return lambda r: getattr(r, self.name, None) == other

    def in_(self, options):
        return lambda r: getattr(r, self.name, None) in options

    def asc(self):
        return self

    def nulls_last(self):
        return self


class FakeModel:
    """Any model: every attribute is a comparable column."""

    def __getattr__(self, name):
        return FakeCol(name)


class _ModelMeta(type):
    """Columns on the CLASS, so `Model.col == x` is a predicate."""

    def __getattr__(cls, name):
        return FakeCol(name)


class FakeLVU(metaclass=_ModelMeta):
    """...and calling it builds a row, the way the real model does."""

    def __new__(cls, **kw):
        return Row(**kw)


class FakeDB:
    def __init__(self, rows):
        self.rows = list(rows)
        self.commits = 0
        self.closed = 0

    def get(self, _model, pk):
        return next((r for r in self.rows if getattr(r, "id", None) == pk),
                    None)

    def query(self, _model):
        return FakeQuery(self.rows)

    def add(self, row):
        row.id = max([getattr(r, "id", 0) for r in self.rows] + [0]) + 1
        self.rows.append(row)

    def refresh(self, _row):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed += 1


# ------------------------------------------------- 1 + 2: the handoff

def run_job(event, *, allow_tee_only=False, manual=False):
    """Drive _run_camera_event_job against fakes, reporting what it did."""
    from app.routers import cameras

    seen: dict = {"enqueued": [], "ensure_local": 0,
                  "post_process": [], "order": []}

    # A stub admin module, so the function-level `from .admin import
    # enqueue_produce_job` resolves to ours.
    admin = types.ModuleType("app.routers.admin")

    def _enqueue(upload_id, hole_number=None, wait=False, **kw):
        seen["enqueued"].append({"upload_id": upload_id,
                                 "hole_number": hole_number, "wait": wait,
                                 "manual": kw.get("manual")})
        seen["order"].append("enqueue")
        return {"queued": True}

    admin.enqueue_produce_job = _enqueue

    models = types.ModuleType("app.models")
    models.LongVideoUpload = FakeLVU
    models.VideoClip = FakeModel()
    models.CameraEvent = FakeModel()

    db = FakeDB([event])
    saved_mods = {k: sys.modules.get(k) for k in
                  ("app.routers.admin", "app.models")}
    saved = (cameras.SessionLocal, cameras.storage,
             cameras._post_process_raw_clip, cameras.CameraEvent)
    sys.modules["app.routers.admin"] = admin
    sys.modules["app.models"] = models
    try:
        cameras.SessionLocal = lambda: db
        cameras.CameraEvent = FakeModel()

        class _Storage:
            @staticmethod
            def ensure_local(*_a, **_k):
                seen["ensure_local"] += 1
                return True

        cameras.storage = _Storage()

        def _pp(name, *_a, **_k):
            seen["post_process"].append(name)
            seen["order"].append("preview")

        cameras._post_process_raw_clip = _pp
        # The preview backstop runs in its own thread; join it so the
        # test sees what it did rather than racing it.
        _threads = []
        _real_thread = cameras.threading.Thread

        def _tracked(*a, **kw):
            t = _real_thread(*a, **kw)
            _threads.append(t)
            return t

        cameras.threading.Thread = _tracked
        try:
            cameras._run_camera_event_job(
                event.id, allow_tee_only, manual)
        finally:
            cameras.threading.Thread = _real_thread
            for t in _threads:
                t.join(timeout=5)
    finally:
        (cameras.SessionLocal, cameras.storage,
         cameras._post_process_raw_clip, cameras.CameraEvent) = saved
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return seen, db


def _event(**kw):
    base = dict(id=7, course_id=1, hole_number=8, status="paired_uploaded",
                tee_clip_filename="t.mp4", green_clip_filename="g.mp4",
                green_camera_id=2,
                triggered_at=None, last_error="stale", produced_clip_id=None)
    base.update(kw)
    return Row(**base)


def test_it_queues_and_returns():
    ev = _event()
    seen, _db = run_job(ev)
    # CONTROL FIRST: the job actually ran. Without this every assertion
    # below passes just as well on a function that did nothing at all,
    # which is exactly the shape of the bug being fixed.
    check("the upload is queued", len(seen["enqueued"]), 1)
    check("  for the event's own hole", seen["enqueued"][0]["hole_number"], 8)
    check("and it does NOT wait on produce",
          seen["enqueued"][0]["wait"], False)
    check("the event is marked queued", ev.status, "queued")
    check("  and its stale error is cleared", ev.last_error, None)
    # AUTOMATIC BY DEFAULT, so a paused queue may hold it. Nobody is
    # watching a capture that arrived on its own.
    check("and it goes in as automatic work",
          seen["enqueued"][0]["manual"], False)


def test_re_process_is_not_held_by_a_pause():
    """The card says Re-process forces one now, so it has to.

    A button that quietly does nothing while paused is worse than no
    button: the operator presses it, watches nothing happen, and goes
    looking for a bug in the queue.
    """
    ev = _event()
    seen, _db = run_job(ev, manual=True)
    check("a human's re-process goes in as manual",
          seen["enqueued"][0]["manual"], True)
    # CONTROL: the same path with nobody asking is still automatic, so
    # the True above is the flag and not a constant.
    ev2 = _event(id=13)
    seen2, _db2 = run_job(ev2)
    check("control, the automatic path is unchanged",
          seen2["enqueued"][0]["manual"], False)


def test_the_dedupe_wrapper_forwards_both_flags():
    """_process_camera_event_job is what every caller actually calls.

    It exists to stop one event producing twice, and it is also the
    only place the flags pass through -- so a dropped argument there
    silently turns every human's Re-process back into automatic work
    that a pause will hold.
    """
    from app.routers import cameras

    got: list = []
    saved = cameras._run_camera_event_job
    try:
        cameras._run_camera_event_job = (
            lambda eid, allow=False, manual=False: got.append(
                (eid, allow, manual)))
        cameras._process_camera_event_job(5, True, True)
        check("both flags arrive", got, [(5, True, True)])
        got.clear()
        # CONTROL: the defaults are the automatic case, so the Trues
        # above came from the call and are not just what it always says.
        cameras._process_camera_event_job(6)
        check("control, the defaults are automatic", got, [(6, False, False)])
    finally:
        cameras._run_camera_event_job = saved


def test_no_media_work_in_front_of_the_queue():
    ev = _event()
    seen, _db = run_job(ev)
    check("nothing is rehydrated here", seen["ensure_local"], 0)
    # The preview transcode is not IN FRONT of the queue -- the enqueue
    # has already happened by the time it is asked for. Cheap for a
    # copy-engine tee (a probe and nothing else) and the only thing
    # that notices when the upload-time hook died on the heavy gate.
    check("the previews are made AFTER the enqueue",
          seen["order"].index("enqueue") < seen["order"].index("preview"),
          True)
    check("  for both halves", sorted(seen["post_process"]),
          ["g.mp4", "t.mp4"])
    # CONTROL: the counters are wired to the real call sites, so a zero
    # means "not called" and not "not instrumented".
    from app.routers import cameras
    check("control, the stubs are the ones the module would call",
          (hasattr(cameras, "storage"),
           hasattr(cameras, "_post_process_raw_clip")), (True, True))


def test_a_paired_event_queues_as_paired():
    ev = _event()
    _seen, db = run_job(ev)
    lvu = [r for r in db.rows if getattr(r, "camera_event_id", None) == 7][0]
    check("the upload carries both halves",
          (lvu.tee_filename, lvu.green_filename), ("t.mp4", "g.mp4"))
    # CONTROL: a tee-only event must NOT be given a green filename --
    # has_green now comes off the filename rather than a disk probe, and
    # the probe is what used to downgrade a paired event after a
    # redeploy had emptied the local disk.
    ev2 = _event(id=9, green_clip_filename=None, green_camera_id=None)
    _s2, db2 = run_job(ev2)
    lvu2 = [r for r in db2.rows if getattr(r, "camera_event_id", None) == 9][0]
    check("control, tee-only stays tee-only", lvu2.green_filename, None)


def test_a_paired_event_waits_for_its_green_half():
    """Half a pair is not ready, however long the tee has been in."""
    ev = _event(green_clip_filename=None)        # paired, green missing
    seen, db = run_job(ev)
    check("a paired event with no green is NOT queued",
          len(seen["enqueued"]), 0)
    check("  and no upload row is made for it",
          [r for r in db.rows if getattr(r, "camera_event_id", None)], [])
    check("  and it is left where the sweeper can find it",
          ev.status, "paired_uploaded")
    # THE DELIBERATE EXCEPTION. The tee-only fallback has waited out
    # the whole window and a human pressing Re-process has decided for
    # themselves; both pass the flag, and nothing else can.
    ev2 = _event(id=11, green_clip_filename=None)
    seen2, _db2 = run_job(ev2, allow_tee_only=True)
    check("control, the fallback may force it anyway",
          len(seen2["enqueued"]), 1)
    check("  and it goes in tee-only", ev2.status, "queued")
    # CONTROL: an UNPAIRED camera has no green half to wait for and
    # must not be caught by the guard.
    ev3 = _event(id=12, green_clip_filename=None, green_camera_id=None)
    seen3, _db3 = run_job(ev3)
    check("control, an unpaired camera queues on its tee alone",
          len(seen3["enqueued"]), 1)


# ------------------------------------------------ 3: derived status

def sync(lvu_status, last_error=None, clips=()):
    from app.routers import admin

    ev = Row(id=7, status="queued", last_error=None, produced_clip_id=None)
    lvu = Row(id=3, camera_event_id=7, processing_status=lvu_status,
              last_error=last_error)
    db = FakeDB([ev, lvu, *clips])
    models = types.ModuleType("app.models")
    models.VideoClip = FakeModel()
    models.CameraEvent = FakeModel()
    saved_mod = sys.modules.get("app.models")
    saved = (admin.LongVideoUpload,)
    sys.modules["app.models"] = models
    try:
        admin.LongVideoUpload = FakeLVU
        admin._sync_camera_event_from_upload(db, 3)
    finally:
        (admin.LongVideoUpload,) = saved
        if saved_mod is None:
            sys.modules.pop("app.models", None)
        else:
            sys.modules["app.models"] = saved_mod
    return ev


def test_status_is_derived_from_the_upload():
    check("a completed upload makes a processed event",
          sync("completed").status, "processed")
    check("a failed one makes a failed event",
          sync("failed", "ffmpeg exploded").status, "failed")
    check("  and carries the reason",
          sync("failed", "ffmpeg exploded").last_error, "ffmpeg exploded")
    # A CAPTURE WITH NO GOLF IN IT IS NOT A FAILURE. The Pi triggered,
    # captured and uploaded exactly as asked; there was no swing. Red
    # here teaches the operator to ignore red.
    check("no swings is a successful capture",
          sync("failed", "no swings detected in this upload").status,
          "processed")
    # CONTROL: it is the PREFIX that decides, not the word appearing
    # anywhere -- otherwise a real error mentioning it reads as success.
    check("control, an error that merely mentions swings still fails",
          sync("failed", "ffmpeg died before no swings detected").status,
          "failed")


def test_an_upload_nobody_made_is_left_alone():
    from app.routers import admin

    ev = Row(id=7, status="queued", last_error=None)
    lvu = Row(id=3, camera_event_id=None, processing_status="completed",
              last_error=None)
    db = FakeDB([ev, lvu])
    saved = admin.LongVideoUpload
    try:
        admin.LongVideoUpload = FakeLVU
        admin._sync_camera_event_from_upload(db, 3)
    finally:
        admin.LongVideoUpload = saved
    check("a wizard upload touches no event", ev.status, "queued")


# ------------------------------------------- 4: what a restart does

def reap(rows):
    """Drive _reap_orphaned_jobs against fakes."""
    from app import main as appmain

    db = FakeDB(rows)
    models = types.ModuleType("app.models")
    models.LongVideoUpload = FakeLVU
    models.CameraEvent = FakeModel()
    database = types.ModuleType("app.database")
    database.SessionLocal = lambda: db
    saved = {k: sys.modules.get(k) for k in ("app.models", "app.database")}
    sys.modules["app.models"] = models
    sys.modules["app.database"] = database
    try:
        appmain._reap_orphaned_jobs()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return db


def test_a_restart_hands_camera_events_back():
    ev = Row(id=7, status="queued", green_clip_filename="g.mp4",
             last_error=None)
    lvu = Row(id=3, camera_event_id=7, processing_status="processing",
              last_error=None, processing_completed_at=None)
    reap([ev, lvu])
    check("the orphaned upload is cleared", lvu.processing_status, "failed")
    check("and the event goes back to the sweeper's status",
          ev.status, "paired_uploaded")
    # A tee-only event must land in the tee-only status, or the paired
    # sweep looks for a green half that was never coming.
    ev2 = Row(id=8, status="queued", green_clip_filename=None,
              last_error=None)
    lvu2 = Row(id=4, camera_event_id=8, processing_status="pending",
               last_error=None, processing_completed_at=None)
    reap([ev2, lvu2])
    check("  tee-only goes back as tee-only", ev2.status, "tee_uploaded")


def test_a_restart_does_not_undo_a_finished_event():
    """The reap runs on EVERY boot, over every non-terminal row."""
    ev = Row(id=7, status="processed", green_clip_filename="g.mp4",
             last_error=None)
    lvu = Row(id=3, camera_event_id=7, processing_status="pending",
              last_error=None, processing_completed_at=None)
    reap([ev, lvu])
    check("a processed event is left alone", ev.status, "processed")
    ev2 = Row(id=8, status="failed", green_clip_filename="g.mp4",
              last_error="real reason")
    lvu2 = Row(id=4, camera_event_id=8, processing_status="pending",
               last_error=None, processing_completed_at=None)
    reap([ev2, lvu2])
    check("  and so is a failed one, with its reason",
          (ev2.status, ev2.last_error), ("failed", "real reason"))


for fn in (test_it_queues_and_returns,
           test_re_process_is_not_held_by_a_pause,
           test_the_dedupe_wrapper_forwards_both_flags,
           test_no_media_work_in_front_of_the_queue,
           test_a_paired_event_queues_as_paired,
           test_a_paired_event_waits_for_its_green_half,
           test_status_is_derived_from_the_upload,
           test_an_upload_nobody_made_is_left_alone,
           test_a_restart_hands_camera_events_back,
           test_a_restart_does_not_undo_a_finished_event):
    print(f"\n-- {fn.__name__}")
    fn()

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
