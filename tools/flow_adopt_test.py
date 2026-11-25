#!/usr/bin/env python3
"""A draft edited on the flow page stays on the flow page.

TWO FAILURES, AND BOTH LOOK LIKE THE EDIT DOING NOTHING.

  THE SAVE IS NOT THE RENDER. The wizard's produce returns as soon as
  the job is QUEUED; the clip takes a minute on a thread. The page
  reloaded its columns the instant the modal closed, read the draft
  that was still being replaced, and never asked again -- so the
  corrected clip only appeared on a manual page reload. `flow_status`
  now reports a wizard produce on the recording as well as a flow
  run, under one `running`, so the page's existing tick waits for it.

  AND THE NEW CLIP IS A NEW ROW. The produce does not edit a clip; it
  writes a fresh one and drops the old. The columns filter on
  `flow_run_at` -- the stamp that says the flow produced this, and the
  only thing stopping a thousand unrelated clips burying the two an
  operator is looking at -- and a wizard produce stamps nothing. So
  the corrected draft did not come back at all: it read as the edit
  having DELETED the clip. The flow now adopts what it asked for.

    python3 tools/flow_adopt_test.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import AudioCut, Course, LongVideoUpload, VideoClip  # noqa
from app.routers import admin as A  # noqa: E402
from app.routers.production_flow import (  # noqa: E402
    claim_clips, flow_clips, flow_status, record_cut,
)

FAILED = []
BASE = datetime(2026, 11, 25, 14, 0, 0)


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    db.add(LongVideoUpload(id=1916, course_id=1, tee_filename="t.mp4",
                           base_captured_at=BASE))
    db.commit()
    return db


def a_clip(db, cid, at_sec, flow=True):
    """A produced clip of the recording, at `at_sec` into it."""
    c = VideoClip(id=cid, course_id=1, hole_number=8, camera_type="tee",
                  captured_at=BASE + timedelta(seconds=at_sec),
                  long_upload_id=1916,
                  flow_run_at=datetime.utcnow() if flow else None,
                  source_url="http://x/uploads/clips/d3prod-1916-%d.mp4" % cid)
    db.add(c)
    db.commit()
    return c


print(__doc__.strip().splitlines()[0])

print("\n1. the page can see a wizard produce, not just a flow run")
A._debugx_set("produce", 1916, stage="Queued", done=0, total=0,
              running=True, error=None)
st = flow_status(1916)
check("the status says something is running", st.get("running"))
check("...and says which kind", st.get("produce_running"))
check("...and the stage it is on", st.get("produce_stage"), "Queued")
# THE FLOW'S OWN REPORT IS NOT TOUCHED. Only `running` is folded
# together: a produce finishing must not make this claim the flow
# produced it.
check("the flow's report is not borrowed from the produce",
      st.get("report"), None)
A._debugx_set("produce", 1916, stage="Done", running=False)
st2 = flow_status(1916)
check("and when it stops, so does the page's tick",
      bool(st2.get("running")), False)
check("...with no stale stage left behind",
      st2.get("produce_stage"), None)

print("\n2. the clip the wizard writes is adopted")
db = a_db()
up = db.get(LongVideoUpload, 1916)
k = record_cut(db, upload=up, idx=0, peak_time_sec=33.48,
               start_sec=29.48, end_sec=41.48, fps=60.0, lag_sec=0.0)
# The draft the FLOW produced, matched to the cut.
old = a_clip(db, 5, 29.5)
old.audio_cut_id = k.id
db.commit()
check("the flow's own draft is in the column",
      [r["id"] for r in flow_clips(db=db)["clips"]], [5])

# NOW THE EDIT. The produce drops the old clip and writes a new row
# -- no stamp, no cut, invisible.
db.delete(old)
db.commit()
a_clip(db, 9, 29.5, flow=False)
check("the replacement is NOT in the column yet",
      [r["id"] for r in flow_clips(db=db)["clips"]], [])
check("...though it exists", db.get(VideoClip, 9) is not None, True)

out = claim_clips(1916, db=db)
check("claiming reports what it took", (out["claimed"], out["matched"]),
      (1, 1))
check("and the corrected draft is back in the column",
      [r["id"] for r in flow_clips(db=db)["clips"]], [9])
check("...matched to the swing it was cut from",
      flow_clips(db=db)["clips"][0]["cut_idx"], 0)
db.refresh(k)
check("...and the cut counts as produced again", k.produced, True)

print("\n3. claiming twice changes nothing")
# Called once per produce the page starts, so it must be a no-op over
# clips already claimed -- and must NOT move their stamp, which is
# the answer to "when did the flow produce this".
_was = db.get(VideoClip, 9).flow_run_at
out2 = claim_clips(1916, db=db)
check("nothing new is claimed", out2["claimed"], 0)
check("the stamp is left where it was",
      db.get(VideoClip, 9).flow_run_at, _was)

print("\n4. a swing ADDED here lands in the column too")
# An operator plotting a strike the cutter missed has no cut row to
# match against. The clip is still the flow's output and still has to
# be visible; it simply cannot name a swing, which is a visible gap
# rather than a wrong answer.
a_clip(db, 11, 95.0, flow=False)
out3 = claim_clips(1916, db=db)
check("it is adopted", out3["claimed"], 1)
check("...with no cut matched", out3["matched"], 0)
_ids = sorted(r["id"] for r in flow_clips(db=db)["clips"])
check("both drafts are in the column", _ids, [9, 11])
_added = [r for r in flow_clips(db=db)["clips"] if r["id"] == 11][0]
check("...and the added one says it has no swing number",
      _added["cut_idx"], None)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
