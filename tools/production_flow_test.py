#!/usr/bin/env python3
"""The production flow: five views, two units, and one honest funnel.

THE SHAPE OF THE THING is what this guards, because the arithmetic
invites two specific mistakes:

  TWO DIFFERENT UNITS. `raw` and `cuts` count recordings and cuts;
  `draft`, `final` and `broadcast` count CLIPS. One recording holding
  four swings is one row in the first tab and four in the third, so
  the strip is not a funnel and the numbers are not meant to subtract.
  The API says so in words rather than leaving a reader to discover it
  when they fail to.

  BROADCAST IS NOT A STAGE. A clip goes out from draft or from final.
  Were it a third stage, "final" would stop meaning "a person approved
  this" and start meaning "approved or went out", and neither question
  could be answered afterwards. It is an independent timestamp.

AND ONE THING ABOUT THE PAST: un-accepting a clip does not unsend it.

    python3 tools/production_flow_test.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import AudioCut, Course, LongVideoUpload, VideoClip  # noqa
from app.routers.production_flow import (  # noqa: E402
    accept_clip, broadcast_clip, flow_clips, flow_counts, flow_cuts,
    record_cut, unaccept_clip,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(Course(id=1, name="Rivertowne"))
up = LongVideoUpload(id=5, course_id=1, base_captured_at=datetime.now(),
                     tee_filename="t.mp4")
db.add(up)
db.commit()


# EVERY CLIP HERE CAME FROM A CUT, because the columns show only what
# this flow produced and a clip produced through it knows which cut it
# came from. A test clip without one is a clip from some other
# pipeline, which the flow is supposed to hide -- so building them
# that way would be testing the five views against rows none of them
# is meant to show.
_seed_cut = record_cut(db, upload=up, idx=0, peak_time_sec=1.0)


def clip(n, **kw):
    kw.setdefault("audio_cut_id", _seed_cut.id)
    kw.setdefault("flow_run_at", datetime.now())
    c = VideoClip(course_id=1, hole_number=n, captured_at=datetime.now(),
                  source_url="u%d.mp4" % n, long_upload_id=5, **kw)
    db.add(c)
    db.commit()
    return c


print("\n0. and a clip from another pipeline is not in any of them")
_other = VideoClip(course_id=1, hole_number=99, captured_at=datetime.now(),
                   source_url="old.mp4", long_upload_id=5)
db.add(_other)
db.commit()
check("it exists", bool(db.get(VideoClip, _other.id)), True)
check("but the draft view does not show it",
      [c["id"] for c in flow_clips(db=db)["clips"]], [])
check("...and asking for everything does",
      [c["id"] for c in flow_clips(flow_only=0, db=db)["clips"]],
      [_other.id])


print("\n1. a clip starts as a draft, because nobody has looked")
c1 = clip(1)
check("stage", c1.stage, "draft")
check("not accepted", c1.accepted_at, None)
check("not broadcast", c1.broadcast_at, None)

print("\n2. accepting is a person's act, and it is recorded as one")
accept_clip(c1.id, by="brent", db=db)
db.refresh(c1)
check("it is final", c1.stage, "final")
check("with a time", c1.accepted_at is not None, True)
check("and a name", c1.accepted_by, "brent")

print("\n3. BROADCAST IS NOT A STAGE")
c2 = clip(2)
broadcast_clip(c2.id, on=True, db=db)
db.refresh(c2)
check("a draft can go out", (c2.stage, c2.broadcast_at is not None),
      ("draft", True))
check("...and is still a draft nobody approved", c2.accepted_at, None)
broadcast_clip(c1.id, on=True, db=db)
db.refresh(c1)
check("a final can go out too", c1.broadcast_at is not None, True)
check("...and is still final", c1.stage, "final")

print("\n4. un-accepting changes a judgement, not the past")
unaccept_clip(c1.id, db=db)
db.refresh(c1)
check("back to draft", c1.stage, "draft")
check("the approval is gone", c1.accepted_at, None)
check("BUT IT WAS STILL BROADCAST — that cannot be unsent",
      c1.broadcast_at is not None, True)

print("\n5. the tabs count what they say they count")
clip(3)
clip(4, stage="final")
counts = flow_counts(db=db)
by = {t["key"]: t for t in counts["tabs"]}
check("five tabs", sorted(by), ["broadcast", "cuts", "draft", "final",
                                "raw"])
check("raw counts recordings", (by["raw"]["count"], by["raw"]["unit"]),
      (1, "recordings"))
# c1 (back to draft), c2, c3 = 3 drafts; c4 final.
check("drafts", by["draft"]["count"], 3)
check("finals", by["final"]["count"], 1)
check("broadcast counts both of the ones that went out",
      by["broadcast"]["count"], 2)
check("clips are counted in clips", by["draft"]["unit"], "clips")
check("AND THE MISMATCH IS SAID OUT LOUD, not left to be noticed",
      "not meant to add up" in counts["note"], True)

print("\n6. a clip with no stage at all is a draft")
# Every clip produced before this column existed has a null stage.
db.execute(VideoClip.__table__.update()
           .where(VideoClip.id == c2.id).values(stage=None))
db.commit()
out = flow_clips(stage="draft", db=db)
check("the null-stage clip is in the draft tab",
      any(x["id"] == c2.id for x in out["clips"]), True)
check("...and reads as draft rather than as nothing",
      [x["stage"] for x in out["clips"] if x["id"] == c2.id], ["draft"])

print("\n7. cuts are kept per swing, and re-cutting does not duplicate")
k = record_cut(db, upload=up, idx=0, peak_time_sec=28.1, start_sec=23.6,
               end_sec=35.6, detector="audio1", zone=2, ratio=12.4,
               tee_filename="_testcuts/a-tee.mp4",
               green_filename="_testcuts/a-green.mp4", duration_sec=12.0)
check("stored", k is not None, True)
record_cut(db, upload=up, idx=0, peak_time_sec=28.2, detector="audio1",
           tee_filename="_testcuts/a2-tee.mp4")
check("re-cutting the same swing updates, not duplicates",
      db.query(AudioCut).count(), 1)
check("...with the newer numbers",
      db.query(AudioCut).one().peak_time_sec, 28.2)
check("it has an expiry, because two angles per swing is bulky",
      db.query(AudioCut).one().expires_at is not None, True)
check("and it knows which tee box fired it",
      db.query(AudioCut).one().trigger_zone, 2)

print("\n8. a cut nothing was produced from is findable")
record_cut(db, upload=up, idx=1, peak_time_sec=61.0, detector="audio1",
           tee_filename="_testcuts/b-tee.mp4")
db.query(AudioCut).filter(AudioCut.idx == 0).update({"produced": True})
db.commit()
check("two cuts", flow_cuts(db=db)["cuts"].__len__(), 2)
un = flow_cuts(produced="0", db=db)["cuts"]
check("only one of them produced nothing", len(un), 1)
check("...and it is the right one", un[0]["idx"], 1)
check("a tee-only cut is still a cut", un[0]["green_url"], None)

print("\n9. record_cut never raises, whatever it is handed")
check("a broken upload object costs nothing",
      record_cut(db, upload=object(), idx=0), None)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
