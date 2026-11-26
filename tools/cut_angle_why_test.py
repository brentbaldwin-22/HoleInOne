#!/usr/bin/env python3
"""Why a cut has no tee angle, said on the card rather than guessed.

"WHY DO SOME CLIPS NOT HAVE THE TEE ANGLE ENABLED?" There are four
answers and the operator's next move is different for every one of
them -- and the button looked identical for all four, so the question
could only be settled by reading the source.

  SWEPT. The cuts are scratch: two angles per swing, a few megabytes
  each, every swing of every round. They expire after
  CUT_RETENTION_DAYS and the sweep deletes the MP4s and CLEARS the
  columns -- deliberately, because a row still naming a deleted file
  offers a link that plays nothing. The ROW survives where a produced
  clip points at it, because which swing of a three-minute recording
  a clip came from is worth keeping when the footage is not.

  NO GREEN CAMERA. A tee-only pair has no green half to cut. An
  ordinary state, not a failure.

  THE CUT FAILED. ffmpeg returned nothing for that angle. Logged at
  the time and otherwise invisible.

  GONE FROM DISK. The row names a file that is not there. The worst
  of the four, because the name alone would have built a link that
  404s -- which looks like footage.

    python3 tools/cut_angle_why_test.py
"""
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
    CUT_RETENTION_DAYS, flow_cuts, record_cut,
)

FAILED = []
SUB = CLIPS_DIR / "_flowcuts"
HERE = ("_flowcuts/angle-why-tee.mp4", "_flowcuts/angle-why-green.mp4")


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def has(reason, *words):
    return bool(reason) and all(w in reason for w in words)


print(__doc__.strip().splitlines()[0])

SUB.mkdir(parents=True, exist_ok=True)
for _n in HERE:
    (CLIPS_DIR / _n).write_bytes(b"x" * 64)
try:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    pair = LongVideoUpload(id=1, course_id=1, tee_filename="t.mp4",
                           green_filename="g.mp4",
                           base_captured_at=datetime.now())
    tee_only = LongVideoUpload(id=2, course_id=1, tee_filename="t2.mp4",
                               base_captured_at=datetime.now())
    db.add_all([pair, tee_only])
    db.commit()

    _both = record_cut(db, upload=pair, idx=0, peak_time_sec=1.0,
                       tee_filename=HERE[0], green_filename=HERE[1])
    _tee_only = record_cut(db, upload=tee_only, idx=0, peak_time_sec=1.0,
                           tee_filename=HERE[0])
    _no_green = record_cut(db, upload=pair, idx=1, peak_time_sec=2.0,
                           tee_filename=HERE[0])
    _swept = record_cut(db, upload=pair, idx=2, peak_time_sec=3.0)
    _swept.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    _gone = record_cut(db, upload=pair, idx=3, peak_time_sec=4.0,
                       tee_filename="_flowcuts/angle-why-vanished.mp4")
    rows = {c["id"]: c for c in flow_cuts(limit=50, db=db)["cuts"]}

    print("\n1. a cut with its footage says nothing")
    _r = rows[_both.id]
    check("both angles link", bool(_r["tee_url"] and _r["green_url"]))
    check("...and neither explains itself",
          (_r["tee_why"], _r["green_why"]), (None, None))

    print("\n2. a tee-only pair had no green to cut")
    _r = rows[_tee_only.id]
    check("the tee still links", bool(_r["tee_url"]))
    check("the green does not", _r["green_url"], None)
    check("...and says it is a tee-only pair",
          has(_r["green_why"], "no green half"))

    print("\n3. a green cut that FAILED is not the same thing")
    # THE DISTINCTION THAT MATTERS: one is how the pair is set up and
    # one is a job to re-run. Told apart by whether the RECORDING has
    # a green half, which the cut row cannot know on its own.
    _r = rows[_no_green.id]
    check("the green does not link", _r["green_url"], None)
    check("...and says the cut failed", has(_r["green_why"], "cut failed"))
    check("...and says what to do about it",
          has(_r["green_why"], "re-run the flow"))

    print("\n4. a swept cut says so, and says the row is deliberate")
    _r = rows[_swept.id]
    check("neither angle links",
          (_r["tee_url"], _r["green_url"]), (None, None))
    check("...both say swept", has(_r["tee_why"], "swept")
          and has(_r["green_why"], "swept"))
    check("...naming the window", str(CUT_RETENTION_DAYS) in _r["tee_why"])
    check("...and why the row is still here",
          has(_r["tee_why"], "records which swing"))

    print("\n5. a name whose file is gone is the worst of the four")
    # Worst because the NAME alone would have built a link, and a link
    # that 404s looks like footage.
    _r = rows[_gone.id]
    check("no link is offered", _r["tee_url"], None)
    check("...and it says the file is gone",
          has(_r["tee_why"], "not on disk"))
    check("...and names what the row still points at",
          "vanished" in _r["tee_why"])
    # NOT MISREAD AS SWEPT. This row has not expired; a sweep would
    # have CLEARED the name, and the name is exactly what is left --
    # so it says so by contrast rather than by omitting the word.
    check("...rather than blaming the sweep",
          has(_r["tee_why"], "rather than swept"))
    check("...and it is not the swept message",
          _r["tee_why"] == rows[_swept.id]["tee_why"], False)
finally:
    for _n in HERE:
        (CLIPS_DIR / _n).unlink(missing_ok=True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
