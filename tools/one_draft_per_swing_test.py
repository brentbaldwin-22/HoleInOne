#!/usr/bin/env python3
"""A swing has one draft, and producing one by hand says so out loud.

WHAT THE OPERATOR SAW. Press Produce Clip on a cut, plot it, press
Add clip: the recording's tile says "running", the CUT TILE VANISHES,
and when it settles there are two drafts both reading "swing 5".

TWO TILES FOR ONE SWING IS NOT A DISPLAY PROBLEM. Producing a swing
by hand ADDS a swing to the recording's edit list -- a slot nothing
has produced yet -- and the renderer's single-swing replace only
drops the clip belonging to the slot it is producing. The clip an
earlier run made for that same cut is not its business, so it
survives, and the column shows the shot twice: the one just
corrected and the one it was meant to replace, indistinguishable.

So the newest draft of a cut wins and the older ones go, through the
console's own delete -- the only thing that knows the four foreign
keys pointing at a clip. A broadcast draft is the exception: taking
one down is a retraction, decided on that clip, not a tidy-up.

    python3 tools/one_draft_per_swing_test.py
"""
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AudioCut, CameraEvent, Course, LongVideoUpload, VideoClip,
)
from app.routers.production_flow import (  # noqa: E402
    claim_clips, flow_clips, record_cut,
)

logging.disable(logging.CRITICAL)
FAILED = []
BASE = datetime(2026, 11, 17, 15, 36)


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rivertowne"))
    db.add(LongVideoUpload(id=1762, course_id=1, tee_filename="t.mp4",
                           base_captured_at=BASE))
    db.commit()
    return db


def a_clip(db, cid, at_sec, bcast=False):
    c = VideoClip(id=cid, course_id=1, hole_number=8, camera_type="tee",
                  captured_at=BASE + timedelta(seconds=at_sec),
                  long_upload_id=1762, flow_run_at=datetime.utcnow(),
                  broadcast_at=datetime.utcnow() if bcast else None,
                  source_url="http://x/d%d.mp4" % cid)
    db.add(c)
    db.commit()
    return c


print(__doc__.strip().splitlines()[0])

print("\n1. the newer draft of a swing replaces the older")
db = a_db()
up = db.get(LongVideoUpload, 1762)
k = record_cut(db, upload=up, idx=4, peak_time_sec=63.0, start_sec=59.0,
               end_sec=71.0, fps=60.0)
a_clip(db, 10, 59.5)          # the earlier run's draft of swing 5
a_clip(db, 11, 59.5)          # the one just plotted by hand
check("both are there to begin with",
      sorted(c["id"] for c in flow_clips(db=db)["clips"]), [10, 11])
out = claim_clips(1762, db=db)
check("the older one is superseded",
      [g["clip_id"] for g in out["superseded"]], [10])
# BY SWING, NAMED, because that is what the operator is looking at --
# a clip id means nothing on this page.
check("...named by the swing it was of",
      [g["swing"] for g in out["superseded"]], [5])
check("...and why", "replaced by a newer draft"
      in out["superseded"][0]["why"], True)
check("one tile is left", [c["id"] for c in flow_clips(db=db)["clips"]],
      [11])
check("...and it is the newest", db.get(VideoClip, 10) is None, True)
check("...still matched to its swing",
      flow_clips(db=db)["clips"][0]["cut_idx"], 4)

print("\n2. a broadcast draft is not tidied away")
db2 = a_db()
up2 = db2.get(LongVideoUpload, 1762)
record_cut(db2, upload=up2, idx=4, peak_time_sec=63.0, start_sec=59.0,
           end_sec=71.0, fps=60.0)
a_clip(db2, 20, 59.5, bcast=True)      # older AND broadcast
a_clip(db2, 21, 59.5)
out2 = claim_clips(1762, db=db2)
check("it is left where it is",
      db2.get(VideoClip, 20) is not None, True)
check("...and not reported as superseded",
      [g["clip_id"] for g in out2["superseded"]], [])
# TAKING ONE DOWN IS A RETRACTION, decided on that clip. Both tiles
# stay, which is honest: one of them went out.
check("both tiles remain", len(flow_clips(db=db2)["clips"]), 2)

print("\n3. a draft matched to no cut is not evidence of anything")
db3 = a_db()
up3 = db3.get(LongVideoUpload, 1762)
record_cut(db3, upload=up3, idx=0, peak_time_sec=10.0, start_sec=6.0,
           end_sec=18.0, fps=60.0)
# Two clips from well outside any cut window: the time match fails for
# both, so neither names a swing.
a_clip(db3, 30, 300.0)
a_clip(db3, 31, 400.0)
out3 = claim_clips(1762, db=db3)
check("neither is matched to a cut", out3["matched"], 0)
check("...so neither supersedes the other",
      [g["clip_id"] for g in out3["superseded"]], [])
check("...and both are still shown",
      len(flow_clips(db=db3)["clips"]), 2)

print("\n4. and it is idempotent")
# Called once per produce the page starts, so a second call over a
# tidy recording must do nothing at all.
out4 = claim_clips(1762, db=db)
check("nothing more to supersede", out4["superseded"], [])
check("...and the draft is untouched",
      [c["id"] for c in flow_clips(db=db)["clips"]], [11])

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
