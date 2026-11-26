#!/usr/bin/env python3
"""A recording with no video is not a recording you can work on.

IT SAT IN THE LIST LOOKING LIKE ONE. "—" where its duration should
be, two greyed-out angle buttons, and a Start Production Flow that
could only fail -- which reads as a recording waiting its turn rather
than one that is gone. And the day dropdown counted it: "2026-11-14
(27)" is a promise the list cannot keep.

LOCAL *OR* BUCKET, because the disk here is wiped on every deploy. A
clip that is only in object storage is pulled back the moment
anything needs it, so "not under CLIPS_DIR" is the normal state of
anything older than the last restart and is not "gone".

AND NOT ONE `ensure_local` PER ROW. That is what the duration probe
does, correctly, for one recording -- and asking it per row to find
out whether a file is there would try to DOWNLOAD every clip on the
course. One directory scan and one bucket listing answer it for all
of them.

    python3 tools/flow_source_present_test.py
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
from app.models import Course, LongVideoUpload  # noqa: E402
from app.routers.admin import CLIPS_DIR  # noqa: E402
from app.routers.production_flow import (  # noqa: E402
    _have_source, flow_counts, flow_raw,
)

# The duration probe shouts about every file that is not a video, and
# this fixture writes 32 bytes on purpose.
logging.disable(logging.CRITICAL)

FAILED = []
BASE = datetime(2026, 11, 10, 15, 0)
NAMES = ["srcpresent-%d.mp4" % i for i in range(60)]


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print(__doc__.strip().splitlines()[0])

print("\n1. what counts as being there")
check("a file on local disk is there",
      _have_source("a.mp4", {"a.mp4"}, set()))
# THE DISK IS EPHEMERAL. A clip only in the bucket is rehydrated by
# anything that reads it, so it is workable footage.
check("...and so is one only in the bucket",
      _have_source("a.mp4", set(), {"a.mp4"}))
check("a name in neither place is not", _have_source("a.mp4", set(),
                                                     set()), False)
check("a row naming no file at all is not",
      _have_source(None, {"a.mp4"}, set()), False)
# THE PATH IS NOT THE NAME. Cuts live in a subdirectory and the
# listing holds basenames, so a stored "_flowcuts/x.mp4" has to be
# matched on its last component.
check("a stored path matches on its basename",
      _have_source("_flowcuts/a.mp4", {"a.mp4"}, set()))

eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(Course(id=1, name="Rolling Hills"))
db.commit()
_present = []
try:
    # 60 recordings over 6 days; every third has its video.
    for _i, _n in enumerate(NAMES):
        if _i % 3 == 0:
            (CLIPS_DIR / _n).write_bytes(b"x" * 32)
            _present.append(3000 + _i)
        db.add(LongVideoUpload(id=3000 + _i, course_id=1, tee_filename=_n,
                               base_captured_at=BASE + timedelta(
                                   days=_i // 10, minutes=_i)))
        db.commit()

    print("\n2. the list shows only what is there")
    r = flow_raw(limit=40, db=db)
    check("every row shown has its video",
          all(x["id"] in _present for x in r["raw"]), True)
    check("...all twenty of them", len(r["raw"]), 20)
    check("and the forty without are not shown",
          r["hidden_no_source"], 40)
    check("...but are named, so a band can explain itself",
          len(r["hidden_ids"]) > 0, True)
    check("...and the names are the ones with no video",
          set(r["hidden_ids"]).isdisjoint(_present), True)

    print("\n3. the number in brackets is a promise the list keeps")
    check("the day counts add up to what is shown",
          sum(d["n"] for d in r["days"]), 20)
    check("...across every day", len(r["days"]), 6)
    check("the header agrees with the column",
          flow_counts(db=db)["tabs"][0]["count"], 20)

    print("\n4. a page is a full page")
    # DROPPED BEFORE THE LIMIT, not after it. Filtered afterwards, a
    # page of eight shows two or three, `total` promises rows that
    # never appear, and the next offset steps over the gap.
    _p1 = flow_raw(limit=8, db=db)
    _p2 = flow_raw(limit=8, offset=8, db=db)
    check("eight asked for is eight given", len(_p1["raw"]), 8)
    check("...and the next eight are different",
          len(_p2["raw"]) == 8
          and not ({x["id"] for x in _p1["raw"]}
                   & {x["id"] for x in _p2["raw"]}), True)
    check("total is what can actually be shown", _p1["total"], 20)
    # 20 of them, so the third page is the last and holds four.
    check("...and the last page holds the remainder",
          len(flow_raw(limit=8, offset=16, db=db)["raw"]), 4)

    print("\n5. and the filters still work over what is left")
    _day = r["days"][0]["day"]
    _n = r["days"][0]["n"]
    check("a day gives exactly what it promised",
          len(flow_raw(limit=40, day=_day, db=db)["raw"]), _n)
    check("...and nothing without footage",
          all(x["id"] in _present
              for x in flow_raw(limit=40, day=_day, db=db)["raw"]), True)
finally:
    for _n in NAMES:
        (CLIPS_DIR / _n).unlink(missing_ok=True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
