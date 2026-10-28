#!/usr/bin/env python3
"""Narrowing the production list to one kind of trigger.

IT RUNS IN SQL, NOT IN THE BROWSER, because the list is paginated:
a filter applied to the twenty-five rows already loaded would answer
"audio" with however many of THOSE happened to be audio, and "load
more" would then fetch twenty-five more of everything.

Which puts the predicate on a JSON column, and that is dialect
territory -- SQLite here, Postgres on Render. So this builds a real
database, puts real rows in it and runs the real query, rather than
asserting that a string appears in a source file.

    python3 tools/trigger_filter_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import create_engine, select, func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app.models import (  # noqa: E402
    Base, Camera, CameraEvent, Course, LongVideoUpload,
)
from backend.app.routers.admin import _TRIGGER_KINDS  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


_NOW = datetime.now(timezone.utc)
engine = create_engine("sqlite://")
Base.metadata.create_all(engine)

PLAN = [
    ("audio-1", {"kind": "audio", "ratio": 4.5, "verdict": True}),
    ("audio-2", {"kind": "audio", "ratio": 6.9}),
    ("dwell-1", {"kind": "dwell", "stood_sec": 3.1}),
    ("dwell-2", {"kind": "dwell"}),
    ("dwell-3", {"kind": "dwell"}),
    ("capture", {"kind": "capture"}),
    # A Pi from before the trigger was described. No kind at all.
    ("silent", None),
]

with Session(engine) as db:
    _course = Course(name="Rivertowne", location="SC", qr_token="qr0")
    db.add(_course)
    db.flush()
    _cam = Camera(course_id=_course.id, assigned_hole=8,
                  assigned_role="tee", auth_token="t0k3n", name="tee")
    db.add(_cam)
    db.flush()
    for name, detail in PLAN:
        ev = CameraEvent(session_id=name, tee_camera_id=_cam.id,
                         course_id=_course.id, hole_number=8,
                         trigger_detail=detail)
        db.add(ev)
        db.flush()
        db.add(LongVideoUpload(camera_event_id=ev.id, tee_filename=name,
                               course_id=_course.id,
                               base_captured_at=_NOW))
    # AND ONE NOBODY UPLOADED FROM A CAMERA. A hand-uploaded video has
    # no event behind it, so it has no trigger either.
    db.add(LongVideoUpload(tee_filename="hand-upload",
                           course_id=_course.id, base_captured_at=_NOW))
    db.commit()

    def names(trigger):
        """The endpoint's own narrowing, lifted exactly."""
        q = db.query(LongVideoUpload)
        trig = (trigger or "").strip().lower()
        if trig in _TRIGGER_KINDS:
            q = q.join(
                CameraEvent,
                CameraEvent.id == LongVideoUpload.camera_event_id,
            ).filter(CameraEvent.trigger_detail["kind"].as_string() == trig)
        return sorted(r.tee_filename for r in q.all())

    print("the JSON predicate runs at all on this dialect:")
    check("it returns rows rather than raising",
          len(names("audio")), 2)

    print()
    print("and it narrows to exactly one kind:")
    check("audio", names("audio"), ["audio-1", "audio-2"])
    check("dwell", names("dwell"), ["dwell-1", "dwell-2", "dwell-3"])
    check("capture", names("capture"), ["capture"])
    check("manual, of which there are none", names("manual"), [])

    print()
    print("everything means everything:")
    _all = ["audio-1", "audio-2", "capture", "dwell-1", "dwell-2", "dwell-3",
            "hand-upload", "silent"]
    check("blank", names(""), _all)
    check("None", names(None), _all)
    # AN UNRECOGNISED VALUE MUST SHOW EVERYTHING, NOT NOTHING. A filter
    # that silently empties the page reads as "the round is gone", and
    # the one place a junk value arrives from is a URL somebody typed.
    check("a kind nobody has heard of", names("banana"), _all)
    check("SQL in the parameter", names("audio' OR '1'='1"), _all)

    print()
    print("a clip with no recorded trigger is in NEITHER bucket:")
    # Not audio and not dwell -- which is right, and is why the page
    # says so underneath the filter rather than leaving an empty list
    # to be read as missing footage.
    check("not in audio", "silent" in names("audio"), False)
    check("not in dwell", "silent" in names("dwell"), False)
    check("...but present unfiltered", "silent" in names(""), True)
    check("nor is a hand upload in one",
          "hand-upload" in names("dwell"), False)

    print()
    print("the buckets partition what has a kind:")
    _kinded = set()
    for k in _TRIGGER_KINDS:
        _b = set(names(k))
        check("  %s does not overlap the others" % k,
              bool(_b & _kinded), False)
        _kinded |= _b
    check("together they are every event with a kind",
          sorted(_kinded),
          sorted(n for n, d in PLAN if d))

    print()
    print("and the join cannot duplicate a row:")
    # An upload has one event; a sloppier join condition would still
    # pass every test above while doubling counts on real data.
    check("one row per upload",
          len(names("dwell")), len(set(names("dwell"))))
    check("...and the count agrees with the table",
          db.scalar(select(func.count(LongVideoUpload.id))), 8)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
