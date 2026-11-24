#!/usr/bin/env python3
"""A cut's whole life: recorded, produced from, expired, swept.

THE TWO FAILURES THIS GUARDS both look like success from the outside.

  A WRONG INDEX JOIN. The cuts are keyed on the audio window that
  found the swing; the produce path knows the swing's slot in
  edit_metrics. Those are the same number because the swings are built
  from the windows -- but if they ever drift apart, linking anyway
  labels a clip with another swing's tee box and time, and nothing
  about the result looks wrong. So the join is checked against the
  window the clip was actually produced from, and a mismatch links
  nothing: an empty column is a visible gap, a wrong one is not.

  A LINK THAT PLAYS NOTHING. The cuts tab builds a URL from whatever
  the filename column holds, so a swept row still holding the name of
  a deleted file offers a link that looks like a cut. The sweep clears
  what it empties.

AND: a row a produced clip points at survives the sweep WITHOUT its
files. The footage is scratch; which swing of a three-minute recording
a clip came from is not.

    python3 tools/cut_lifecycle_test.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import AudioCut, Course, LongVideoUpload, VideoClip  # noqa
from app.routers.production_flow import (  # noqa: E402
    _cut_row, flow_clips, link_produced_cut, record_cut,
)
from app.services.retention import sweep_audio_cuts  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def fresh():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rivertowne"))
    db.add(LongVideoUpload(id=5, course_id=1, tee_filename="t.mp4",
                           base_captured_at=datetime.now()))
    db.commit()
    return db


def a_clip(db, n=1):
    c = VideoClip(id=n, course_id=1, hole_number=4, camera_type="tee",
                  captured_at=datetime.now(), long_upload_id=5,
                  source_url="http://x/%d.mp4" % n)
    db.add(c)
    db.commit()
    return c


print(__doc__.strip().splitlines()[0])

print("\n1. producing from a cut marks the cut and links the clip")
db = fresh()
up = db.get(LongVideoUpload, 5)
record_cut(db, upload=up, idx=2, peak_time_sec=41.0, start_sec=36.0,
           end_sec=48.0, detector="audio1", zone=1, tee_filename="_t/a.mp4")
c = a_clip(db)
k = link_produced_cut(db, upload=up, idx=2, clip=c, start_sec=36.2,
                      end_sec=47.8, zone=1)
check("the cut is found, not duplicated", db.query(AudioCut).count(), 1)
check("it is marked produced", bool(k and k.produced))
check("the clip points back at it", c.audio_cut_id, k.id)
check("the detector that found the swing is not overwritten",
      k.detector, "audio1")
check("...nor is the tee box it fired from", k.trigger_zone, 1)

print("\n2. a swing nothing was produced from stays unproduced")
record_cut(db, upload=up, idx=3, peak_time_sec=70.0, start_sec=65.0,
           end_sec=77.0, detector="audio1", zone=1)
k3 = db.query(AudioCut).filter(AudioCut.idx == 3).one()
check("not produced", bool(k3.produced), False)
check("and no clip claims it",
      db.query(VideoClip).filter(VideoClip.audio_cut_id == k3.id).count(), 0)

print("\n3. a peak outside the produced window links NOTHING")
db = fresh()
up = db.get(LongVideoUpload, 5)
record_cut(db, upload=up, idx=2, peak_time_sec=41.0, start_sec=36.0,
           end_sec=48.0, detector="audio1", zone=2)
c = a_clip(db)
k = link_produced_cut(db, upload=up, idx=2, clip=c, start_sec=96.0,
                      end_sec=108.0, zone=2)
check("the link is refused", k, None)
check("the clip is left unlinked", c.audio_cut_id, None)
check("and the cut is NOT claimed as produced",
      bool(db.query(AudioCut).filter(AudioCut.idx == 2).one().produced), False)
check("...nor is its window overwritten with the clip's",
      db.query(AudioCut).filter(AudioCut.idx == 2).one().start_sec, 36.0)

print("\n4. a margin, because the two windows are built differently")
db = fresh()
up = db.get(LongVideoUpload, 5)
record_cut(db, upload=up, idx=0, peak_time_sec=36.4, start_sec=36.0,
           end_sec=48.0, detector="audio1")
c = a_clip(db)
check("a peak just before the produce window still links",
      bool(link_produced_cut(db, upload=up, idx=0, clip=c, start_sec=37.0,
                             end_sec=49.0)))

print("\n5. a cut with no stored peak cannot be checked, so it links")
db = fresh()
up = db.get(LongVideoUpload, 5)
c = a_clip(db)
k = link_produced_cut(db, upload=up, idx=1, clip=c, start_sec=10.0,
                      end_sec=22.0, zone=0)
check("the produce path records the swing itself", bool(k))
check("with its window", (k.start_sec, k.end_sec), (10.0, 22.0))
check("and no invented detector", k.detector, None)

print("\n6. the sweep takes expired footage and clears what it empties")
with TemporaryDirectory() as tmp:
    clips = Path(tmp)
    (clips / "_testcuts").mkdir()
    db = fresh()
    up = db.get(LongVideoUpload, 5)
    for i, name in ((0, "a"), (1, "b")):
        for half in ("tee", "green"):
            (clips / "_testcuts" / f"{name}-{half}.mp4").write_bytes(b"x" * 2048)
        record_cut(db, upload=up, idx=i, peak_time_sec=10.0 + i,
                   tee_filename=f"_testcuts/{name}-tee.mp4",
                   green_filename=f"_testcuts/{name}-green.mp4")
    # One expired, one not.
    old = db.query(AudioCut).filter(AudioCut.idx == 0).one()
    old.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    dry = sweep_audio_cuts(db, clips_dir=clips, dry_run=True)
    check("the dry run finds the expired cut", dry["expired"], 1)
    check("...and both of its halves", dry["files_deleted"], 2)
    check("but deletes nothing", (clips / "_testcuts" / "a-tee.mp4").exists())
    check("...and keeps the row", db.query(AudioCut).count(), 2)

    rep = sweep_audio_cuts(db, clips_dir=clips, dry_run=False)
    check("the real sweep takes the expired halves", rep["files_deleted"], 2)
    check("the files are gone",
          (clips / "_testcuts" / "a-tee.mp4").exists(), False)
    check("the row is gone too", rep["rows_deleted"], 1)
    check("only one cut remains", db.query(AudioCut).count(), 1)
    check("and it is the one still inside its horizon",
          db.query(AudioCut).one().idx, 1)
    check("whose footage was not touched",
          (clips / "_testcuts" / "b-tee.mp4").exists())

print("\n7. a row a produced clip points at keeps its provenance")
with TemporaryDirectory() as tmp:
    clips = Path(tmp)
    (clips / "_testcuts").mkdir()
    (clips / "_testcuts" / "p-tee.mp4").write_bytes(b"x" * 4096)
    db = fresh()
    up = db.get(LongVideoUpload, 5)
    record_cut(db, upload=up, idx=0, peak_time_sec=12.0, start_sec=8.0,
               end_sec=20.0, zone=1, tee_filename="_testcuts/p-tee.mp4")
    c = a_clip(db)
    k = link_produced_cut(db, upload=up, idx=0, clip=c, start_sec=8.0,
                          end_sec=20.0, zone=1)
    db.query(AudioCut).one().expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    rep = sweep_audio_cuts(db, clips_dir=clips, dry_run=False)
    check("the footage goes", rep["files_deleted"], 1)
    check("the row is kept", rep["rows_kept_for_clips"], 1)
    check("...and no row was dropped", rep["rows_deleted"], 0)
    k = db.query(AudioCut).one()
    check("the clip still knows which swing it is", c.audio_cut_id, k.id)
    check("the tee box survives", k.trigger_zone, 1)
    # THE POINT OF CLEARING THE COLUMN: no link that plays nothing.
    check("but the filename is cleared", k.tee_filename, None)
    check("so the tab offers no dead link",
          _cut_row(k, "http://x")["tee_url"], None)

print("\n8. the sweep never walks out of the clips folder")
with TemporaryDirectory() as tmp:
    clips = Path(tmp) / "clips"
    clips.mkdir()
    outside = Path(tmp) / "secret.mp4"
    outside.write_bytes(b"x" * 512)
    db = fresh()
    up = db.get(LongVideoUpload, 5)
    record_cut(db, upload=up, idx=0, peak_time_sec=1.0,
               tee_filename="../secret.mp4")
    db.query(AudioCut).one().expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    rep = sweep_audio_cuts(db, clips_dir=clips, dry_run=False)
    check("a path pointing outside is refused", rep["errors"], 1)
    check("nothing outside was deleted", outside.exists())
    check("...and it is not counted as swept", rep["files_deleted"], 0)

print("\n9. a sweep with nothing expired is cheap and silent")
db = fresh()
up = db.get(LongVideoUpload, 5)
record_cut(db, upload=up, idx=0, peak_time_sec=5.0)
rep = sweep_audio_cuts(db, dry_run=False)
check("nothing expired", rep["expired"], 0)
check("nothing deleted", rep["rows_deleted"], 0)
check("the cut is still there", db.query(AudioCut).count(), 1)

print("\n10. linking never raises, whatever it is handed")
db = fresh()
check("a broken upload costs nothing",
      link_produced_cut(db, upload=object(), idx=0, clip=None), None)
check("...and the session is usable afterwards",
      db.query(AudioCut).count(), 0)

print("\n11. and the clip list says which swing of the recording it is")
db = fresh()
up = db.get(LongVideoUpload, 5)
record_cut(db, upload=up, idx=2, peak_time_sec=41.0, start_sec=36.0,
           end_sec=48.0, detector="audio1", zone=1)
c = a_clip(db)
link_produced_cut(db, upload=up, idx=2, clip=c, start_sec=36.0, end_sec=48.0)
unlinked = a_clip(db, 2)
got = {r["id"]: r["cut_idx"] for r in flow_clips(db=db)["clips"]}
check("the swing index travels to the page, not the row id", got[1], 2)
# THE DEFAULT VIEW HIDES IT. A clip with no cut did not come from this
# flow, and the columns show only what this flow produced -- so asking
# what the page says about its swing number means asking for
# everything first.
check("a clip from no cut is not in the flow's own view", 2 in got, False)
_all = {r["id"]: r["cut_idx"] for r in flow_clips(flow_only=0, db=db)["clips"]}
check("and when everything is asked for, it says so rather than guessing",
      _all[2], None)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
