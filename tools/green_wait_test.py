#!/usr/bin/env python3
"""Don't produce a paired hole from the tee alone while the green is
still uploading.

WHAT WENT WRONG. camera_tee_only_fallback_seconds was 180. A two-
minute tee clip is 56 MB and the green has one of its own to push over
the same cellular link, so three minutes is not long enough for either
to arrive -- never mind both. The sweep would reach a paired event
still waiting on its green, decide the green was never coming, and
produce tee-only. When the green landed, upload_event flipped the
event back to paired and produced it AGAIN. Twice the work, and the
first answer was the worse one: no landing, no descent, tee-only.

Seen as upload #823 sitting QUEUED with the green pane reading "Not
Uploaded".

SO: a paired event whose green camera has been seen recently is left
alone -- its clip is most likely still on the wire. With a ceiling,
because a rig can heartbeat perfectly well while its upload is dead,
and a tee-only clip is worth more than no clip.

    python3 tools/green_wait_test.py
"""
from __future__ import annotations

import sys
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


NOW = datetime(2026, 10, 20, 12, 0, 0)


class Ev:
    def __init__(self, eid, age_sec, green_id=None, retries=0):
        self.id = eid
        self.status = "tee_uploaded"
        self.triggered_at = NOW - timedelta(seconds=age_sec)
        self.produce_retries = retries
        self.tee_clip_filename = "tee.mp4"
        self.green_camera_id = green_id


class Cam:
    def __init__(self, cid, seen_sec_ago):
        self.id = cid
        self.last_seen_at = (None if seen_sec_ago is None
                             else NOW - timedelta(seconds=seen_sec_ago))


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


cameras.CameraEvent.status = FakeCol("status")
cameras.CameraEvent.triggered_at = FakeCol("triggered_at")
cameras.CameraEvent.tee_clip_filename = FakeCol("tee_clip_filename")


class FakeDB:
    def __init__(self, events, cams=()):
        self.rows = events
        self.cams = {c.id: c for c in cams}
        self.cam_lookups: list = []

    def query(self, _model):
        return FakeQuery(self.rows)

    def get(self, model, pk):
        if model is cameras.Camera:
            self.cam_lookups.append(pk)
            return self.cams.get(pk)
        return next((r for r in self.rows if r.id == pk), None)

    def commit(self):
        pass

    def close(self):
        pass


def select(events, cams=(), window=900, alive=240, ceiling=3600):
    return cameras._recoverable_events(
        FakeDB(events, cams), "tee_uploaded",
        NOW - timedelta(seconds=window),
        NOW - timedelta(hours=24), 3, require_tee=True,
        green_alive_since=(None if alive is None
                           else NOW - timedelta(seconds=alive)),
        wait_floor=(None if ceiling is None
                    else NOW - timedelta(seconds=ceiling)))


def test_a_live_green_is_waited_for():
    ev = Ev(1, age_sec=1200, green_id=9)
    check("green seen a minute ago — keep waiting",
          select([ev], [Cam(9, 60)]), [])
    # CONTROL: everything else about this event says produce it. It is
    # past the window, inside the age floor, has its tee clip and has
    # budget left -- the green being alive is the only thing holding it.
    check("control, the same event with a silent green produces",
          select([ev], [Cam(9, 9999)]), [1])


def test_a_green_that_never_checked_in_is_not_waited_for():
    check("no last_seen at all", select([Ev(2, 1200, 9)], [Cam(9, None)]),
          [2])
    check("and no camera row at all", select([Ev(3, 1200, 9)], []), [3])


def test_an_unpaired_event_is_never_held():
    """It was not waiting for anything in the first place.

    And it is not looked up either: `green_camera_id` is None, so
    there is no camera to ask about. Asserted on the lookup rather
    than the result, because a db.get(Camera, None) returns nothing
    anyway and the outcome is identical either way -- which is exactly
    how a pointless query survives review.
    """
    db = FakeDB([Ev(4, 1200, None)], [Cam(9, 10)])
    got = cameras._recoverable_events(
        db, "tee_uploaded", NOW - timedelta(seconds=900),
        NOW - timedelta(hours=24), 3, require_tee=True,
        green_alive_since=NOW - timedelta(seconds=240),
        wait_floor=NOW - timedelta(seconds=3600))
    check("no green camera, no wait", got, [4])
    check("and no camera was looked up", db.cam_lookups, [])
    # CONTROL: a paired event in the same call does look one up.
    db2 = FakeDB([Ev(5, 1200, 9)], [Cam(9, 10)])
    cameras._recoverable_events(
        db2, "tee_uploaded", NOW - timedelta(seconds=900),
        NOW - timedelta(hours=24), 3, require_tee=True,
        green_alive_since=NOW - timedelta(seconds=240),
        wait_floor=NOW - timedelta(seconds=3600))
    check("control, a paired one does", db2.cam_lookups, [9])


def test_patience_has_a_ceiling():
    """A rig can heartbeat happily while its upload is dead."""
    ev = Ev(5, age_sec=5000, green_id=9)
    check("past the ceiling, produce from the tee",
          select([ev], [Cam(9, 5)]), [5])
    # CONTROL: the same healthy green, inside the ceiling, is waited on.
    check("control, inside the ceiling it is not",
          select([Ev(6, 1200, 9)], [Cam(9, 5)]), [])


def test_the_window_still_applies():
    """The green rule only ever makes the sweep MORE patient."""
    check("too new to be a problem at all",
          select([Ev(7, 60, 9)], [Cam(9, 9999)]), [])


def test_off_switch_restores_the_old_behaviour():
    """green_alive_since=None is the rule not being applied."""
    check("with the rule off, a live green does not save it",
          select([Ev(8, 1200, 9)], [Cam(9, 5)], alive=None), [8])


def test_the_default_wait_is_long_enough_to_upload_in():
    """180s was the bug. A 56MB clip does not move in three minutes."""
    from app.config import settings
    check("the fallback is no longer three minutes",
          int(settings.camera_tee_only_fallback_seconds) >= 600, True)
    check("and the ceiling is longer than the fallback",
          int(settings.camera_tee_only_max_wait_seconds)
          > int(settings.camera_tee_only_fallback_seconds), True)


for fn in (test_a_live_green_is_waited_for,
           test_a_green_that_never_checked_in_is_not_waited_for,
           test_an_unpaired_event_is_never_held,
           test_patience_has_a_ceiling,
           test_the_window_still_applies,
           test_off_switch_restores_the_old_behaviour,
           test_the_default_wait_is_long_enough_to_upload_in):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
