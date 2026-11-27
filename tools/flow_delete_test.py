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
    # THE SWING THE PRODUCE LEFT ON THE RECORDING, pointing at the
    # clip it made. This is what the edit wizard opens on.
    up.edit_metrics = dict(up.edit_metrics or {}, swings=[
        {"idx": 0, "impact_frame": 1920, "clip_id": clip_id,
         "ball": {"x": 900, "y": 700}},
    ])
    db.commit()
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

# THE SWING STAYS. On the Broadcast page the tile an operator
# deletes IS the swing; here it is a DRAFT of one. Taking the swing
# out of the edit list would mean the only way back to a draft is to
# re-run the whole flow, audio detection included.
_em = db.get(LongVideoUpload, 1916).edit_metrics or {}
_sw = (_em.get("swings") or [])
check("the swing is still in the recording's edit list", len(_sw), 1)
check("...and no longer points at the clip that is gone",
      "clip_id" in (_sw[0] if _sw else {}), False)
check("...but keeps what was measured", _sw[0].get("impact_frame"), 1920)

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

# AND THE CONSOLE'S OWN DELETE IS UNCHANGED. There the tile an
# operator deletes IS the swing -- leaving it in the edit list meant
# the wizard still offered both swings after one had been deleted and
# the next produce put the clip straight back. `keep_swing` is off by
# default for exactly that reason.
from app.routers.admin import delete_clip  # noqa: E402

db3 = a_db()
a_produced_draft(db3, clip_id=9)
delete_clip(9, db=db3)
_em3 = db3.get(LongVideoUpload, 1916).edit_metrics or {}
check("the broadcast page's delete still takes the swing with it",
      len(_em3.get("swings") or []), 0)

try:
    delete_flow_clip(404, db=db2)
    check("a clip that was never there is a 404", "deleted it", "404")
except HTTPException as e:
    check("a clip that was never there is a 404", e.status_code, 404)

print("\nX. and the button that starts all this is red")
# IT WAS THE HOUSE BLUE -- the same colour as "produce" and "edit",
# which is the colour for "go on then" -- on the one control in the
# card that cannot be undone. Both halves: the trigger and the
# confirm inside the armed state.
_page = (Path(__file__).resolve().parents[1] / "frontend" / "src"
         / "pages" / "AdminProductionFlow.jsx").read_text()
check("the trigger is a destructive ghost",
      'className="btn ghost danger small"' in _page
      and 'delete recording' in _page, True)
check("...and the confirm is filled red",
      'className="btn danger small"' in _page, True)
# THE STYLESHEET'S OWN CLASSES, not a colour typed into this file.
# `.btn.danger` and `.btn.ghost.danger` already exist and already
# have their hover states; an inline hex here would be a fourth red
# that nothing else follows and that no theme can change.
_css = (Path(__file__).resolve().parents[1] / "frontend" / "src"
        / "styles.css").read_text()
check("those classes exist to be used",
      ".btn.danger, .danger {" in _css
      and ".btn.ghost.danger, button.ghost.danger, .ghost.danger {" in _css,
      True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
