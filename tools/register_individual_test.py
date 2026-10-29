"""Sign-up is one golfer, at a time they choose, on the right clock.

Three things changed together and each can fail quietly:

  * the tee time is now a moment the golfer picks, not a row chosen from
    a list the course never actually published;
  * it arrives as UTC and must be STORED as naive UTC, because
    `admin.py`'s candidate search compares `TeeTime.starts_at` against
    `clip.captured_at` and `frontend/src/time.js` renders it assuming
    the same clock. Store the wall-clock instead and every tee time is
    off by the viewer's offset -- which looks plausible, which is the
    problem;
  * one row per golfer, so `max_players` can never refuse a sign-up for
    a tee time two strangers happened to pick.
"""
import os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Course, Participant, TeeTime
from app.services.tee_times import parse_tee_time, tee_time_for

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def main():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    course = Course(name="Rivertowne Country Club", par3_holes=[8])
    db.add(course); db.flush()

    print("the clock")
    # 2:24 PM in Mount Pleasant (EDT, UTC-4) is 18:24 UTC. The browser
    # sends the instant; we must keep the UTC clock, naive.
    tt = tee_time_for(db, course, "2026-10-30T18:24:00.000Z")
    check("a Z-suffixed instant stores the UTC clock",
          (tt.starts_at.hour, tt.starts_at.minute) == (18, 24),
          tt.starts_at.isoformat())
    check("and stores it NAIVE, like every other datetime here",
          tt.starts_at.tzinfo is None, str(tt.starts_at.tzinfo))

    # An explicit offset must be converted, not truncated. Truncating
    # would store 14:24 and put the round four hours early.
    tt2 = tee_time_for(db, course, "2026-10-30T14:24:00-04:00")
    check("an offset is CONVERTED to UTC, not stripped",
          (tt2.starts_at.hour, tt2.starts_at.minute) == (18, 24),
          tt2.starts_at.isoformat())
    check("both spellings of the same instant agree",
          tt.starts_at == tt2.starts_at)

    # A tz-less string is taken at face value (already UTC).
    tt3 = tee_time_for(db, course, "2026-10-30T18:24:00")
    check("a tz-less value is read as UTC already",
          tt3.starts_at == tt.starts_at, tt3.starts_at.isoformat())

    print("\none row per golfer")
    a = tee_time_for(db, course, "2026-10-30T18:24:00Z")
    b = tee_time_for(db, course, "2026-10-30T18:24:00Z")
    check("two golfers at the same instant get different rows",
          a.id != b.id, f"{a.id} vs {b.id}")
    check("each row holds exactly one player",
          a.max_players == 1 and b.max_players == 1)
    # The gate that used to refuse the second one.
    for row in (a, b):
        taken = db.query(Participant).filter(Participant.tee_time_id == row.id).count()
        check(f"row {row.id} is not full before anyone joins it",
              taken < row.max_players, f"{taken}/{row.max_players}")
    check("rows belong to the course they were made for",
          a.course_id == course.id and b.course_id == course.id)

    print("\nbad input is refused, not stored")
    for bad, why in [
        ("", "empty"),
        ("not a date", "nonsense"),
        ("30/10/2026 2:24pm", "human-written"),
        ("1970-01-01T00:00:00Z", "far past"),
        ("2126-10-30T18:24:00Z", "far future"),
    ]:
        try:
            tee_time_for(db, course, bad)
            check(f"rejects {why} ({bad!r})", False, "accepted it")
        except ValueError as e:
            check(f"rejects {why}", True, str(e))

    print("\nthe edges of the year window are inside it")
    now = datetime.utcnow()
    for delta, label in [(timedelta(days=300), "ten months out"),
                         (timedelta(days=-300), "ten months back")]:
        when = (now + delta).replace(microsecond=0)
        try:
            row = tee_time_for(db, course, when.isoformat() + "Z")
            check(f"accepts {label}", abs((row.starts_at - when).total_seconds()) < 2)
        except ValueError as e:
            check(f"accepts {label}", False, str(e))

    print("\nwhat the matcher actually keys on is untouched")
    # starts_at is a record, not a join key: the candidate window is
    # anchored to Participant.created_at and the course.
    src = (Path(__file__).resolve().parents[1]
           / "backend/app/services/matcher.py").read_text()
    check("matcher still windows on Participant.created_at",
          "Participant.created_at" in src)
    check("matcher does NOT window on TeeTime.starts_at",
          "TeeTime.starts_at" not in src)

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
