#!/usr/bin/env python3
"""Groups, the flow's clip filter, and deleting a draft.

THREE THINGS THAT GO WRONG QUIETLY.

  A GROUP MEASURED FROM THE PREVIOUS SWING NEVER CLOSES. Swings
  arriving every six minutes would stay in Group 1 all morning, and a
  group number beside a clip would stop meaning anything. The window
  runs from the FIRST swing in the group, so a group is at most seven
  minutes long.

  A DAY IN UTC IS NOT THE DAY THEY PLAYED. The columns are naive UTC
  and the course is in Central time: an evening swing at 19:30 is the
  next day in UTC. Grouping on that splits one evening across two days
  and numbers both from Group 1.

  A DELETED CLIP'S FILES MUST GO WITH IT. `protected_names` is built
  from the clip rows, so a row deleted while its mp4 stays behind
  leaves footage nothing points at -- which the sweep then protects
  forever, because nothing points at it to say it is a clip.

    python3 tools/flow_groups_test.py
"""
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import AudioCut, Course, LongVideoUpload, VideoClip  # noqa
from app.routers.production_flow import (  # noqa: E402
    GROUP_WINDOW_SEC, _ordinal, assign_groups, flow_clips, flow_counts,
    record_cut,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def rows(*isos):
    return [{"id": i, "captured_at": s} for i, s in enumerate(isos, 1)]


print(__doc__.strip().splitlines()[0])

print("\n1. the window runs from the first swing, not the last")
g = assign_groups(rows("2026-11-24T20:00:00", "2026-11-24T20:05:00",
                       "2026-11-24T20:06:30", "2026-11-24T20:08:00"))
check("three swings inside seven minutes are one group",
      [r["group_number"] for r in g[:3]], [1, 1, 1])
check("the one past the window starts the next", g[3]["group_number"], 2)
# THE ROLLING-GAP BUG, STATED AS A CASE: six-minute spacing forever.
g = assign_groups(rows(*["2026-11-24T20:%02d:00" % m
                         for m in (0, 6, 12, 18, 24)]))
check("swings every six minutes do not stay in one group forever",
      [r["group_number"] for r in g], [1, 1, 2, 2, 3])

print("\n2. the day is the course's, not UTC's")
g = assign_groups(rows("2026-11-25T01:30:00"))   # 19:30 Central the 24th
check("an evening swing is the day they played it",
      g[0]["group_label"], "Tuesday, November 24th — Group 1")
g = assign_groups(rows("2026-11-25T01:30:00", "2026-11-25T18:00:00"))
check("and the next day starts again at Group 1",
      [r["group_number"] for r in g], [1, 1])
check("...on its own date",
      g[1]["group_label"], "Wednesday, November 25th — Group 1")

print("\n3. a swing with no capture time is placed nowhere")
g = assign_groups(rows("2026-11-24T20:00:00", None))
check("it gets no group", "group_number" in g[1], False)
check("...rather than being called the first of the morning",
      g[1].get("group_label"), None)
check("and the dated one is unaffected", g[0]["group_number"], 1)

print("\n4. the label reads like a date somebody would say")
for n, want in ((1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
                (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"),
                (22, "22nd"), (23, "23rd"), (30, "30th")):
    check("  %d -> %s" % (n, want), _ordinal(n), want)

print("\n5. the window is seven minutes")
check("as specified", GROUP_WINDOW_SEC, 420)

print("\n6. the columns show only what this flow produced")
eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(Course(id=1, name="Rivertowne"))
up = LongVideoUpload(id=5, course_id=1, tee_filename="t.mp4",
                     base_captured_at=datetime(2026, 11, 24, 20, 0))
db.add(up)
db.commit()
k = record_cut(db, upload=up, idx=0, peak_time_sec=10.0)


def clip(n, **kw):
    c = VideoClip(id=n, course_id=1, hole_number=4, camera_type="tee",
                  captured_at=datetime(2026, 11, 24, 20, 0),
                  source_url="http://x/%d.mp4" % n, **kw)
    db.add(c)
    db.commit()
    return c


old = clip(1)                        # every other pipeline ever
# THE FLOW STAMP IS WHAT THE COLUMNS FILTER ON, not the swing link:
# a clip whose swing could not be identified is still a clip the flow
# produced, and hiding it was the bug.
mine = clip(2, audio_cut_id=k.id, flow_run_at=datetime.utcnow())
got = flow_clips(db=db)
check("a clip from another pipeline is not shown",
      [c["id"] for c in got["clips"]], [2])
check("...and the filter says so", got["flow_only"], True)
check("it can be turned off deliberately",
      sorted(c["id"] for c in flow_clips(flow_only=0, db=db)["clips"]), [1, 2])
check("the count over the column agrees with the column",
      next(t["count"] for t in flow_counts(db=db)["tabs"]
           if t["key"] == "draft"), 1)
check("and the clip carries its group",
      got["clips"][0]["group_label"], "Tuesday, November 24th — Group 1")

print("\n6b. the run claims what it produced, even when the swing is unclear")
# THE BUG. The columns filtered on `audio_cut_id`, which is set only
# when the clip can be matched to a cut with confidence. A clip the
# flow had just produced vanished whenever the swing was uncertain:
# the run reported "1 draft" and the column reported "0 clips".
from app.routers.production_flow import _claim_clips  # noqa: E402

eng2 = create_engine("sqlite://")
Base.metadata.create_all(eng2)
db2 = sessionmaker(bind=eng2)()
db2.add(Course(id=1, name="Rivertowne"))
base = datetime(2026, 11, 20, 17, 4, 39)
up2 = LongVideoUpload(id=1916, course_id=1, tee_filename="t.mp4",
                      base_captured_at=base)
db2.add(up2)
db2.commit()
k2 = record_cut(db2, upload=up2, idx=4, peak_time_sec=47.88,
                start_sec=43.9, end_sec=55.9)
cuts = [{"id": k2.id, "idx": 4, "start_sec": 43.9, "end_sec": 55.9}]


def at(n, offset):
    from datetime import timedelta as _td
    c = VideoClip(id=n, course_id=1, hole_number=4, camera_type="tee",
                  captured_at=base + _td(seconds=offset), long_upload_id=1916,
                  source_url="http://x/%d.mp4" % n)
    db2.add(c)
    db2.commit()
    return c


inside = at(10, 45.0)     # starts inside the cut's window
outside = at(11, 120.0)   # nowhere near any cut
claimed, matched = _claim_clips(db2, up2, cuts)
check("both clips are claimed by the run", claimed, 2)
check("one of them is matched to its swing", matched, 1)
db2.refresh(inside); db2.refresh(outside)
check("the matched clip knows its swing", inside.audio_cut_id, k2.id)
check("...and the cut is no longer unproduced",
      bool(db2.query(AudioCut).one().produced), True)
check("the unmatched clip is still claimed", outside.flow_run_at is not None,
      True)
check("...but is NOT given a swing it cannot be shown to be",
      outside.audio_cut_id, None)
check("and it is visible in the column anyway, which is the point",
      sorted(c["id"] for c in flow_clips(db=db2)["clips"]), [10, 11])
check("the count agrees with the column",
      next(t["count"] for t in flow_counts(db=db2)["tabs"]
           if t["key"] == "draft"), 2)
check("a second run does not re-match what is already matched",
      _claim_clips(db2, up2, cuts)[1], 0)

print("\n6c. re-running the flow starts over rather than piling up")
# THE CUTS ARE KEYED ON (upload, idx). A second run that found three
# strikes where the first found five left the last two sitting there,
# pointing at files this run was about to overwrite -- so the column
# showed five cuts for a run that made three, with no way to tell
# which two were ghosts.
from app.routers.production_flow import _clear_previous  # noqa: E402

with TemporaryDirectory() as tmp:
    import app.routers.admin as _adm
    _was = _adm.CLIPS_DIR
    _adm.CLIPS_DIR = Path(tmp)
    try:
        (Path(tmp) / "_flowcuts").mkdir()
        eng3 = create_engine("sqlite://")
        Base.metadata.create_all(eng3)
        db3 = sessionmaker(bind=eng3)()
        db3.add(Course(id=1, name="R"))
        up3 = LongVideoUpload(id=7, course_id=1, tee_filename="t.mp4",
                              base_captured_at=datetime(2026, 11, 24, 20, 0))
        db3.add(up3)
        db3.commit()

        # An earlier run: five cuts, with files, one of them produced.
        for i in range(5):
            (Path(tmp) / "_flowcuts" / ("old-%d.mp4" % i)).write_bytes(b"x" * 64)
            record_cut(db3, upload=up3, idx=i, peak_time_sec=float(i),
                       tee_filename="_flowcuts/old-%d.mp4" % i)
        k0 = db3.query(AudioCut).filter(AudioCut.idx == 0).one()
        old_clip = VideoClip(id=90, course_id=1, hole_number=4,
                             camera_type="tee", captured_at=datetime.now(),
                             long_upload_id=7, audio_cut_id=k0.id,
                             source_url="http://x/90.mp4")
        db3.add(old_clip)
        db3.commit()
        check("five cuts before", db3.query(AudioCut).count(), 5)

        rep = _clear_previous(db3, up3)
        check("all five rows are gone", rep["cuts"], 5)
        check("and their files with them", rep["files"], 5)
        check("none left in the database", db3.query(AudioCut).count(), 0)
        check("no file left behind",
              list((Path(tmp) / "_flowcuts").iterdir()), [])
        # A CLIP MUST NOT KEEP POINTING AT A CUT THAT IS GONE. The
        # produce clears the clips too, but it runs after this and can
        # fail; a dangling id in between reads as provenance.
        db3.refresh(old_clip)
        check("a clip that pointed at one is unpointed",
              old_clip.audio_cut_id, None)
        check("...rather than left claiming a swing that does not exist",
              bool(db3.get(VideoClip, 90)), True)

        # And a second clear on an upload with nothing is harmless.
        #
        # ASSERTED FIELD BY FIELD, NOT AS A WHOLE DICT. The report
        # grew a `kept_manual` count when cuts made BY HAND stopped
        # being cleared -- they are work, not an earlier run's
        # leftovers -- and an equality check on the whole dict fails
        # the moment the report learns to say one more true thing.
        _again = _clear_previous(db3, up3)
        check("clearing twice costs nothing",
              (_again["cuts"], _again["files"]), (0, 0))
        check("...and keeps no hand-made cut it did not have",
              _again.get("kept_manual"), 0)
    finally:
        _adm.CLIPS_DIR = _was

print("\n6d. and the run calls it before it cuts anything")
FLOW = (ROOT / "backend/app/routers/production_flow.py").read_text()
_run = FLOW[FLOW.index("def flow_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("the clear happens in the run",
      'rep["cleared"] = _clear_previous(db, row)' in _run, True)
check("...before the first record_cut",
      _run.index("_clear_previous") < _run.index("record_cut("), True)

print("\n7. deleting a draft takes its files and frees its cut")
with TemporaryDirectory() as tmp:
    import app.routers.admin as _adm
    _was = _adm.CLIPS_DIR
    _adm.CLIPS_DIR = Path(tmp)
    try:
        (Path(tmp) / "shot-2.mp4").write_bytes(b"x" * 1024)
        outside = Path(tmp).parent / "notmine.mp4"
        outside.write_bytes(b"x" * 16)
        mine.source_url = "http://x/uploads/clips/shot-2.mp4"
        mine.thumbnail_url = "http://x/uploads/clips/../notmine.mp4"
        db.commit()
        from app.routers.production_flow import delete_flow_clip
        rep = delete_flow_clip(2, db=db)
        check("the clip is gone", db.get(VideoClip, 2), None)
        check("its file is gone", (Path(tmp) / "shot-2.mp4").exists(), False)
        check("a path pointing outside is not followed", outside.exists(),
              True)
        check("the cut it came from survives",
              db.query(AudioCut).count(), 1)
        check("...and is unproduced again, which is the point of the column",
              bool(db.query(AudioCut).one().produced), False)
        check("the other pipeline's clip is untouched",
              bool(db.get(VideoClip, 1)), True)
    finally:
        _adm.CLIPS_DIR = _was

print("\n8. a broadcast clip is not deleted by accident")
c3 = clip(3, audio_cut_id=k.id, broadcast_at=datetime(2026, 11, 24, 21, 0))
from fastapi import HTTPException  # noqa: E402
from app.routers.production_flow import delete_flow_clip  # noqa: E402
try:
    delete_flow_clip(3, db=db)
    check("it is refused", False)
except HTTPException as exc:
    check("it is refused", exc.status_code, 409)
    check("...and says what to do instead", "un-broadcast" in exc.detail, True)
check("and it is still there", bool(db.get(VideoClip, 3)), True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
