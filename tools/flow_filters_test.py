#!/usr/bin/env python3
"""The flow's filters, its zone names, and throwing a recording away.

"ZONE 2" IS THE RIG'S COUNT, NOT A PLACE. The operator drew the box
and typed "middle" on it; every screen that prints the index back is
asking them to translate -- and into the one thing it is most easily
confused with, since the rig counts zones from 0 and the editor
labels them from 1. So the name travels with the index.

THE DAY IS THE COURSE'S OWN DAY. A swing at 19:30 Central is the 25th
in UTC and the 24th to everybody who was standing there, so filtering
on the stored UTC date splits one evening across two days -- the same
reckoning the groups already use.

AND DELETING A RECORDING TAKES ITS CHILDREN WITH IT. The console's
upload delete says in its own docstring that produced clips are kept,
which is right where a clip is an independent thing and wrong here,
where the row being deleted is the recording those clips are OF.

    python3 tools/flow_filters_test.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AudioCut, Camera, CameraEvent, Course, LongVideoUpload, VideoClip,
)
from app.routers.admin import _trigger_zone_label  # noqa: E402
from app.routers.production_flow import (  # noqa: E402
    delete_flow_raw, flow_cuts, flow_raw, record_cut,
)

FAILED = []
BOXES = {"boxes": [{"x": 0, "y": 0, "w": 9, "h": 9, "label": "back"},
                   {"x": 9, "y": 0, "w": 9, "h": 9, "label": "middle"},
                   {"x": 18, "y": 0, "w": 9, "h": 9}]}


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    db.add(Camera(id=1, course_id=1, assigned_hole=8, assigned_role="tee",
                  auth_token="t1", tee_box_roi=BOXES))
    db.commit()
    return db


def a_recording(db, uid, zone, when, label=None, roi_then=None):
    _d = {"zone": zone}
    if label:
        _d["zone_label"] = label
    ev = CameraEvent(id=uid - 1000, session_id="s%d" % uid, tee_camera_id=1,
                     green_camera_id=1, course_id=1, hole_number=8,
                     trigger_detail=_d,
                     tee_box_roi_at_trigger=roi_then,
                     triggered_at=datetime.fromisoformat(when))
    db.add(ev)
    db.commit()
    up = LongVideoUpload(id=uid, course_id=1, tee_filename="t%d.mp4" % uid,
                         camera_event_id=ev.id,
                         base_captured_at=datetime.fromisoformat(when))
    db.add(up)
    db.commit()
    return up


print(__doc__.strip().splitlines()[0])

print("\n1. the zone's own name, from wherever it is recorded")
db = a_db()
# THE EVENT'S OWN LABEL FIRST: the name as it was AT THE TRIGGER, which
# is right even if the boxes have been redrawn since.
_a = a_recording(db, 1901, 1, "2026-11-25 14:00", label="middle tee")
check("the event's label wins", _trigger_zone_label(db, _a), "middle tee")
# AND THE CAMERA'S BOXES BY INDEX for a rig too old to record one, or
# an operator who typed it afterwards.
_b = a_recording(db, 1902, 1, "2026-11-25 15:00")
check("...the camera's box is the fallback",
      _trigger_zone_label(db, _b), "middle")
_c = a_recording(db, 1903, 2, "2026-11-25 16:00")
check("an unnamed box has no name", _trigger_zone_label(db, _c), None)
# A BOX REDRAWN SINCE IS A DIFFERENT PIECE OF GRASS, so the drawing
# the event kept wins over the camera's current one.
_d = a_recording(db, 1904, 0, "2026-11-25 17:00", roi_then={"boxes": [
    {"x": 0, "y": 0, "w": 9, "h": 9, "label": "back as it was"}]})
check("the boxes as they were at the trigger win",
      _trigger_zone_label(db, _d), "back as it was")
check("a recording with no camera event has none",
      _trigger_zone_label(db, LongVideoUpload(id=9, course_id=1)), None)

print("\n2. it reaches both columns")
_r = flow_raw(db=db)
_by = {x["id"]: x for x in _r["raw"]}
check("the raw row carries the name",
      _by[1902]["trigger_zone_label"], "middle")
check("...beside the index it is counted by",
      _by[1902]["trigger_zone"], 1)
record_cut(db, upload=_b, idx=0, peak_time_sec=33.48, zone=1)
_k = (flow_cuts(db=db).get("cuts") or [{}])[0]
check("and the cut row does too", _k.get("trigger_zone_label"), "middle")

print("\n3. the day, in the course's own clock")
check("every day is offered, not just this page's",
      [d["day"] for d in _r["days"]], ["2026-11-25"])
_e = a_recording(db, 1905, 1, "2026-11-24 19:30")
_r2 = flow_raw(db=db)
check("a second day appears", [d["day"] for d in _r2["days"]],
      ["2026-11-25", "2026-11-24"])
check("...with a count each", {d["day"]: d["n"] for d in _r2["days"]},
      {"2026-11-25": 4, "2026-11-24": 1})
check("filtering to one day gives that day",
      sorted(x["id"] for x in flow_raw(day="2026-11-24", db=db)["raw"]),
      [1905])
check("...and the other gives the rest",
      sorted(x["id"] for x in flow_raw(day="2026-11-25", db=db)["raw"]),
      [1901, 1902, 1903, 1904])
# THE DROPDOWN IS BUILT FROM `days`, WHICH IS NOT FILTERED. Built from
# the rows on screen, choosing a day would leave only that day in the
# list -- and then no way back to the others.
check("the day list does not shrink when a day is chosen",
      len(flow_raw(day="2026-11-24", db=db)["days"]), 2)

print("\n4. and the tee box")
check("filtering to zone 1", sorted(x["id"] for x in
                                    flow_raw(zone=1, db=db)["raw"]),
      [1901, 1902, 1905])
# ZONE 0 IS A ZONE. A falsy test here drops the box printed "Zone 1"
# and silently widens the filter to everything -- the off-by-one that
# `zones.js` exists for.
check("zone 0 filters, it does not mean 'all'",
      sorted(x["id"] for x in flow_raw(zone=0, db=db)["raw"]), [1904])
check("both filters together",
      sorted(x["id"] for x in flow_raw(day="2026-11-25", zone=1,
                                       db=db)["raw"]), [1901, 1902])
# TWO RECORDINGS OF ONE ZONE CAN DISAGREE ABOUT ITS NAME -- one kept
# the name it had at its trigger, the other reads today's box -- and a
# dropdown can only show one. The newest row's wins, because the rows
# come newest-first and the first non-empty name is kept; an empty one
# is still replaced by a later name, so an unlabelled box does not
# shadow a labelled one.
check("the zone options carry their names",
      {z["zone"]: z["label"] for z in flow_raw(db=db)["zones"]},
      {0: "back as it was", 1: "middle", 2: ""})
check("...the newest one's, where they disagree",
      flow_raw(db=db)["raw"][2]["trigger_zone_label"], "middle")
check("no filter means everything",
      len(flow_raw(db=db)["raw"]), 5)

print("\n5. throwing a recording away takes its children")
db2 = a_db()
up = a_recording(db2, 1916, 1, "2026-11-25 14:00")
k = record_cut(db2, upload=up, idx=0, peak_time_sec=33.4, start_sec=29.4,
               end_sec=41.4, tee_filename="_flowcuts/x-tee.mp4")
c = VideoClip(id=5, course_id=1, hole_number=8, camera_type="tee",
              captured_at=datetime.now(), long_upload_id=1916,
              audio_cut_id=k.id, flow_run_at=datetime.utcnow(),
              source_url="http://x/uploads/clips/d.mp4")
db2.add(c)
db2.commit()
db2.add(CameraEvent(id=99, session_id="other", tee_camera_id=1,
                    green_camera_id=1, course_id=1, hole_number=8,
                    produced_clip_id=5, triggered_at=datetime.now()))
db2.commit()
out = delete_flow_raw(1916, db=db2)
check("it reports what it took", (out["clips"], out["cuts"]), (1, 1))
check("the recording is gone", db2.get(LongVideoUpload, 1916) is None)
check("...and its draft with it", db2.get(VideoClip, 5) is None)
check("...and its cut", db2.query(AudioCut).count(), 0)
# THE FOUR FOREIGN KEYS ARE STILL THE CLIP DELETE'S JOB. A draft
# removed any other way leaves an event pointing at a row that is not
# there.
check("an event pointing at the draft is cleared, not deleted",
      db2.get(CameraEvent, 99) is not None
      and db2.get(CameraEvent, 99).produced_clip_id is None, True)

print("\n6. nothing broadcast goes by a stray click")
db3 = a_db()
up3 = a_recording(db3, 1917, 1, "2026-11-25 14:00")
k3 = record_cut(db3, upload=up3, idx=0, peak_time_sec=33.4)
db3.add(VideoClip(id=7, course_id=1, hole_number=8, camera_type="tee",
                  captured_at=datetime.now(), long_upload_id=1917,
                  audio_cut_id=k3.id, broadcast_at=datetime.utcnow(),
                  source_url="http://x/uploads/clips/d7.mp4"))
db3.commit()
try:
    delete_flow_raw(1917, db=db3)
    check("a recording with a broadcast clip is refused", "deleted", "409")
except HTTPException as e:
    check("a recording with a broadcast clip is refused", e.status_code, 409)
    check("...naming what went out", "has been broadcast" in e.detail, True)
check("...and it is still there",
      db3.get(LongVideoUpload, 1917) is not None, True)
check("...with its clip untouched", db3.get(VideoClip, 7) is not None, True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
