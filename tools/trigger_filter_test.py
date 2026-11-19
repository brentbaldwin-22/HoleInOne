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
from backend.app.routers.admin import (  # noqa: E402
    _TRIGGER_KINDS, narrow_uploads,
)

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
    # zone 0 is "Zone 1" on the operator's screen, and so on. The
    # numbers below are the stored indexes.
    ("audio-1", {"kind": "audio", "ratio": 4.5, "verdict": True, "zone": 0}),
    ("audio-2", {"kind": "audio", "ratio": 6.9, "zone": 1}),
    ("dwell-1", {"kind": "dwell", "stood_sec": 3.1, "zone": 2,
                 "zone_label": "middle left"}),
    ("dwell-2", {"kind": "dwell", "zone": 2, "zone_label": "middle left"}),
    ("dwell-3", {"kind": "dwell", "zone": 0, "zone_label": "Back"}),
    ("capture", {"kind": "capture", "zone": 1}),
    # A Pi from before the trigger was described. No kind at all.
    ("silent", None),
    # AND ONE THAT FIRED WITH NO ZONE RECORDED. A dwell is always in a
    # zone, but the field is optional in the payload and a filter must
    # not guess which box it meant.
    ("zoneless", {"kind": "dwell"}),
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

    def names(trigger="", zone=""):
        """THE ENDPOINT'S OWN NARROWING, CALLED -- not re-implemented.

        This used to rebuild the predicate here, which proved the JSON
        operator worked on this dialect and proved nothing about the
        endpoint still doing the same thing.
        """
        return sorted(
            r.tee_filename
            for r in narrow_uploads(
                db.query(LongVideoUpload), trigger, zone).all())

    print("the JSON predicate runs at all on this dialect:")
    check("it returns rows rather than raising",
          len(names("audio")), 2)

    print()
    print("and it narrows to exactly one kind:")
    check("audio", names("audio"), ["audio-1", "audio-2"])
    check("dwell", names("dwell"),
          ["dwell-1", "dwell-2", "dwell-3", "zoneless"])
    check("capture", names("capture"), ["capture"])
    check("manual, of which there are none", names("manual"), [])

    print()
    print("everything means everything:")
    _all = ["audio-1", "audio-2", "capture", "dwell-1", "dwell-2", "dwell-3",
            "hand-upload", "silent", "zoneless"]
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
    print("THE ZONE PREDICATE, on the same real dialect:")
    # The reason a zone filter exists at all: a zone catching traffic
    # rather than golf fires all day, and the operator wants every clip
    # it made in one list so they can clear them out.
    check("zone 2 — the one on the cart path", names(zone="2"),
          ["dwell-1", "dwell-2"])
    check("zone 0", names(zone="0"), ["audio-1", "dwell-3"])
    check("zone 1", names(zone="1"), ["audio-2", "capture"])

    print()
    print("ZONE 0 IS A ZONE, not an absent one:")
    # `if zone:` and `zone or ""` both swallow index 0, which would
    # silently turn "show me Zone 1 on screen" into "show me
    # everything" -- and everything is what select-all then selects.
    check("it narrows rather than passing everything through",
          len(names(zone="0")) < len(names()), True)
    check("  and finds exactly its two", names(zone="0"),
          ["audio-1", "dwell-3"])

    print()
    print("a clip with no zone recorded is in NO zone bucket:")
    for _z in ("0", "1", "2"):
        check("  not in zone %s" % _z, "zoneless" in names(zone=_z), False)
    check("  ...but present unfiltered", "zoneless" in names(), True)
    check("nor is a trigger-less clip", "silent" in names(zone="0"), False)
    check("nor a hand upload", "hand-upload" in names(zone="2"), False)

    print()
    print("and a junk zone narrows NOTHING, as a junk kind does:")
    # An empty page reads as lost footage. The one source of a junk
    # value is a URL somebody typed.
    _all2 = ["audio-1", "audio-2", "capture", "dwell-1", "dwell-2",
             "dwell-3", "hand-upload", "silent", "zoneless"]
    check("blank", names(zone=""), _all2)
    check("None", names(zone=None), _all2)
    check("not a number", names(zone="middle left"), _all2)
    check("negative", names(zone="-1"), _all2)
    check("fractional", names(zone="1.5"), _all2)
    check("exponent notation", names(zone="1e0"), _all2)
    check("SQL in the parameter", names(zone="0 OR 1=1"), _all2)

    print()
    print("the two filters compose, and join only once:")
    # Both predicates live on CameraEvent. Joining it twice is an
    # error; an upload has one event, so a second join could only
    # duplicate rows.
    check("dwell AND zone 2", names("dwell", "2"), ["dwell-1", "dwell-2"])
    check("audio AND zone 0", names("audio", "0"), ["audio-1"])
    check("dwell AND zone 1 — no such clip", names("dwell", "1"), [])
    check("neither filter is lost to the other",
          names("capture", "1"), ["capture"])
    _dup = names("dwell", "2")
    check("and no row comes back twice", len(_dup), len(set(_dup)))

    print()
    print("and the join cannot duplicate a row:")
    # An upload has one event; a sloppier join condition would still
    # pass every test above while doubling counts on real data.
    check("one row per upload",
          len(names("dwell")), len(set(names("dwell"))))
    check("...and the count agrees with the table",
          db.scalar(select(func.count(LongVideoUpload.id))), 9)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
