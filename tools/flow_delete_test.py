#!/usr/bin/env python3
"""Throwing a draft away actually throws it away.

THE FAILURE THIS GUARDS was a 500 with the clip still on the page.
Production Flow had its OWN copy of the clip delete: remove the row,
remove the obvious files, commit. But four real foreign keys point at
video_clips.id, and one of them -- camera_events.produced_clip_id --
is stamped on EVERY clip the moment it is produced. So the delete the
flow offered could not succeed on any clip the flow had produced: the
commit raised IntegrityError and the handler 500'd.

A second copy of a delete is the bug. The console's delete already
knows the four keys; the flow's now delegates to it and keeps only its
own policy on top (don't throw away something already broadcast, and
hand the audio cut back so the swing can be produced again).

    python3 tools/flow_delete_test.py
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
from app.routers.production_flow import delete_flow_clip, record_cut  # noqa

FAILED = []


def check(what, got, want):
    ok = got == want
    print("%s %s%s" % ("ok  " if ok else "FAIL", what,
                       "" if ok else "  (got %r, wanted %r)" % (got, want)))
    if not ok:
        FAILED.append(what)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    for _i, _r in ((1, "tee"), (2, "green")):
        db.add(Camera(id=_i, course_id=1, assigned_hole=8,
                      assigned_role=_r, auth_token="t%d" % _i))
    db.commit()
    return db


def a_produced_draft(db, clip_id=5, broadcast=False):
    """An upload, a cut, a clip produced from it, and the camera event
    that points at the clip -- the shape the flow actually leaves."""
    up = db.get(LongVideoUpload, 1916)
    if up is None:
        up = LongVideoUpload(id=1916, course_id=1, tee_filename="t.mp4",
                             base_captured_at=datetime.now())
        db.add(up)
        db.commit()
    k = record_cut(db, upload=up, idx=clip_id, peak_time_sec=33.48)
    c = VideoClip(id=clip_id, course_id=1, hole_number=8, camera_type="tee",
                  captured_at=datetime.now(), long_upload_id=up.id,
                  audio_cut_id=k.id, flow_run_at=datetime.utcnow(),
                  broadcast_at=datetime.utcnow() if broadcast else None,
                  source_url="http://x/uploads/clips/d3prod-1916-ab-0.mp4")
    db.add(c)
    db.commit()
    k.produced = True
    db.commit()
    db.add(CameraEvent(session_id="s%d" % clip_id, tee_camera_id=1,
                       green_camera_id=2, course_id=1, hole_number=8,
                       produced_clip_id=clip_id,
                       triggered_at=datetime.now()))
    db.commit()
    return up, k, c


print(__doc__.strip().splitlines()[0])
print()

# THE REPRODUCTION. A clip a camera event points at -- which is every
# produced clip -- used to raise IntegrityError here.
db = a_db()
up, k, c = a_produced_draft(db)
ev_id = db.query(CameraEvent).one().id
out = delete_flow_clip(5, db=db)
check("the delete succeeds on a clip a camera event points at",
      out.get("ok"), True)
check("the clip is gone", db.get(VideoClip, 5) is None, True)
check("the event that pointed at it is still there",
      db.get(CameraEvent, ev_id) is not None, True)
check("...with its pointer cleared, not dangling",
      db.get(CameraEvent, ev_id).produced_clip_id, None)

# THE CUT GOES BACK IN THE QUEUE. Deleting the draft is how you ask
# for a different one, so the swing has to be producible again.
db.refresh(k)
check("the audio cut is unproduced again", k.produced, False)
check("...and the caller is told which one was freed",
      out.get("freed_cut"), k.id)

# POLICY THE FLOW KEEPS. A broadcast clip is public; deleting it is
# not an edit, it is a retraction, and it needs saying out loud.
db2 = a_db()
a_produced_draft(db2, clip_id=7, broadcast=True)
try:
    delete_flow_clip(7, db=db2)
    check("a broadcast clip is refused", "deleted it", "409")
except HTTPException as e:
    check("a broadcast clip is refused", e.status_code, 409)
    check("...and says to un-broadcast first", "un-broadcast" in e.detail,
          True)
check("...and the clip is still there", db2.get(VideoClip, 7) is not None,
      True)

try:
    delete_flow_clip(404, db=db2)
    check("a clip that was never there is a 404", "deleted it", "404")
except HTTPException as e:
    check("a clip that was never there is a 404", e.status_code, 404)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
