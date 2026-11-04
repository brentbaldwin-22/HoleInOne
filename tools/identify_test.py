"""Putting a name on a shot by hand, and taking it back off.

The operator's view of a day has to be exactly one day. Every datetime in
this database is naive UTC and Course carries no timezone, so the caller
hands in the window and the only thing worth testing hard is that the
edges hold: a shot at the first instant belongs to the day, a shot at the
last does not, and nothing from the course next door leaks in either way.

Shaped like a real morning at Rivertowne: two foursomes out ten minutes
apart, a camera on the eighth, and one clip already paired so the
correction path has something to correct.
"""
import os, sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, ClipProcessingStatus, Course, Participant,
                        TeeTime, VideoClip)
from app.services import identify

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def main():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()

    river = Course(name="Rivertowne Country Club", par3_holes=[8], minutes_per_hole=14)
    other = Course(name="Somewhere Else CC", par3_holes=[3], minutes_per_hole=14)
    db.add_all([river, other]); db.flush()

    # The operator's local Monday, as a UTC window. 00:00 EDT -> 04:00 UTC.
    START = datetime(2026, 11, 2, 4, 0)
    END = START + timedelta(hours=24)
    MORNING = datetime(2026, 11, 2, 12, 0)      # 8am local

    def clip(at, course=None, owner=None, hole=8):
        c = VideoClip(course_id=(course or river).id, hole_number=hole,
                      camera_type="tee", captured_at=at,
                      source_url=f"s{at.isoformat()}.mp4", participant_id=owner,
                      processing_status=(ClipProcessingStatus.assigned.value if owner
                                         else ClipProcessingStatus.unassigned.value))
        db.add(c); db.flush(); return c

    def register(name, tee_off, course=None, selfie=True):
        tt = TeeTime(course_id=(course or river).id, starts_at=tee_off, max_players=1)
        db.add(tt); db.flush()
        p = Participant(tee_time_id=tt.id, name=name, email=f"{name}@t.test",
                        group_size=1, created_at=tee_off - timedelta(minutes=5),
                        selfie_path=(f"selfies/{name}.jpg" if selfie else None))
        db.add(p); db.flush(); return p

    alex = register("Alex", MORNING)
    bo = register("Bo", MORNING + timedelta(minutes=10))
    elsewhere = register("Casey", MORNING, course=other)
    # Tomorrow's golfer, to hold the far edge of the roster window: a
    # count that only checks `starts_at >= start` would sweep them in.
    register("Erin", MORNING + timedelta(days=1))

    shots = [clip(MORNING + timedelta(minutes=14 * 7, seconds=40 * i)) for i in range(4)]
    clip(MORNING, course=other)                      # another course, same morning
    yesterday = clip(START - timedelta(minutes=1))   # one minute before the day
    tomorrow = clip(END)                             # the first instant of the next

    print("the dropdown counts GOLFERS left to do, not shots")
    # Two golfers out at Rivertowne, neither with anything yet. The
    # thirteen-shots-on-one-foursome case is exactly what this must not
    # report: the number is the roster's, so it moves with sign-ups.
    def counts(a=START, b=END):
        return {c["name"]: c["unidentified"] for c in identify.unidentified_counts(db, a, b)}

    c0 = counts()
    check("two signed up, two unidentified", c0["Rivertowne Country Club"] == 2,
          f'{c0["Rivertowne Country Club"]} (4 loose clips that day)')
    check("tomorrow's golfer is not today's work",
          c0["Rivertowne Country Club"] == 2, f'{c0["Rivertowne Country Club"]}')
    check("the other course counts only its own", c0["Somewhere Else CC"] == 1,
          f'{c0["Somewhere Else CC"]}')
    check("a day nobody signed up for reads clear",
          all(v == 0 for v in counts(START + timedelta(days=30),
                                     END + timedelta(days=30)).values()))

    print("\nand it falls as golfers are done, not as clips are")
    identify.pair(db, shots[0].id, alex.id)
    check("one golfer sorted, one left", counts()["Rivertowne Country Club"] == 1,
          f'{counts()["Rivertowne Country Club"]}')
    identify.pair(db, shots[1].id, alex.id)
    check("a second shot for the same golfer changes nothing",
          counts()["Rivertowne Country Club"] == 1,
          f'{counts()["Rivertowne Country Club"]}')
    check("three of the day's four clips are still loose, and that is NOT the number",
          identify.day(db, river.id, START, END)["unidentified"] == 2)
    identify.pair(db, shots[2].id, bo.id)
    check("both golfers sorted reads clear", counts()["Rivertowne Country Club"] == 0,
          f'{counts()["Rivertowne Country Club"]}')
    check("even with a shot still unclaimed",
          identify.day(db, river.id, START, END)["unidentified"] == 1)

    print("\nthe dropdown agrees with the roster underneath it")
    pending = sum(1 for p in identify.day(db, river.id, START, END)["players"]
                  if p["assigned"] == 0)
    check("same number, counted two different ways",
          counts()["Rivertowne Country Club"] == pending, f"{pending}")

    identify.unpair(db, shots[0].id)
    identify.unpair(db, shots[1].id)
    identify.unpair(db, shots[2].id)
    check("undoing it all puts both golfers back on the list",
          counts()["Rivertowne Country Club"] == 2,
          f'{counts()["Rivertowne Country Club"]}')
    try:
        identify.unidentified_counts(db, END, START)
        check("a backwards day", False, "no error raised")
    except ValueError as e:
        check("the counts refuse a backwards day too", True, str(e))

    print("\nthe nav badge is the sum of the dropdown, not its own opinion")
    # It counted clips once and read 12 on the nav while every course in
    # the dropdown said clear. One question, one answer.
    def badge(a=START, b=END):
        return identify.pending_total(db, a, b)["golfers"]

    check("two golfers at Rivertowne, one elsewhere, badge says three",
          badge() == 3, f"{badge()}")
    check("it equals the dropdown added up",
          badge() == sum(c["unidentified"]
                         for c in identify.unidentified_counts(db, START, END)))
    loose = identify.day(db, river.id, START, END)["unidentified"]
    check("golfers, not clips: Rivertowne alone has four loose shots",
          badge() == 3 and loose == 4, f"badge={badge()} loose_at_river={loose}")
    identify.pair(db, shots[0].id, alex.id)
    check("sorting a golfer takes one off the badge", badge() == 2, f"{badge()}")
    identify.pair(db, shots[1].id, alex.id)
    check("a second shot for the same golfer does not", badge() == 2, f"{badge()}")
    identify.unpair(db, shots[0].id)
    identify.unpair(db, shots[1].id)
    check("undoing puts them back", badge() == 3, f"{badge()}")
    check("a day nobody played reads zero, so the badge goes green",
          badge(START + timedelta(days=30), END + timedelta(days=30)) == 0)
    try:
        identify.pending_total(db, END, START)
        check("a backwards day", False, "no error raised")
    except ValueError as e:
        check("the badge refuses a backwards day too", True, str(e))

    print("\nthe day is exactly the window it was given")
    d = identify.day(db, river.id, START, END)
    ids = {c["id"] for c in d["clips"]}
    check("four swings on the day", len(d["clips"]) == 4, f'{len(d["clips"])}')
    check("the minute before midnight is not today", yesterday.id not in ids)
    check("nor is the first instant of tomorrow", tomorrow.id not in ids)
    check("the other course stays on its own page",
          all(c["id"] in {s.id for s in shots} for c in d["clips"]))

    print("\nthe roster, which is what the operator actually reads")
    names = [p["name"] for p in d["players"]]
    check("everyone who went out that day", names == ["Alex", "Bo"], f"{names}")
    check("the golfer at the other course is not on it", "Casey" not in names)
    check("each one carries the photo they took",
          all(p["selfie_url"] == f'/uploads/selfies/{p["name"]}.jpg' for p in d["players"]))
    check("a player with no photo says so, rather than breaking",
          identify._player_row(register("Dana", MORNING, selfie=False), 0)["selfie_url"] is None)
    check("nothing is paired yet", d["unidentified"] == 4, f'{d["unidentified"]}')

    print("\npairing a shot with the golfer who hit it")
    identify.pair(db, shots[0].id, alex.id)
    d = identify.day(db, river.id, START, END)
    first = next(c for c in d["clips"] if c["id"] == shots[0].id)
    check("the clip now carries the name", first["participant_name"] == "Alex")
    check("and the status followed it",
          db.get(VideoClip, shots[0].id).processing_status == ClipProcessingStatus.assigned.value)
    check("the paired clip stays on the page so it can be undone",
          shots[0].id in {c["id"] for c in d["clips"]})
    check("the roster counts it against that player",
          next(p for p in d["players"] if p["name"] == "Alex")["assigned"] == 1)
    check("and not against anyone else",
          next(p for p in d["players"] if p["name"] == "Bo")["assigned"] == 0)
    check("one fewer loose clip", d["unidentified"] == 3, f'{d["unidentified"]}')

    print("\nthe mistakes an operator will actually make")
    identify.pair(db, shots[0].id, bo.id)
    check("a mis-tap can be re-pointed at the right golfer",
          db.get(VideoClip, shots[0].id).participant_id == bo.id)
    identify.unpair(db, shots[0].id)
    check("and taken off entirely", db.get(VideoClip, shots[0].id).participant_id is None)
    check("returning the clip to the unidentified pile",
          db.get(VideoClip, shots[0].id).processing_status == ClipProcessingStatus.unassigned.value)

    print("\nwhat it refuses")
    for label, fn in [
        ("a clip that does not exist", lambda: identify.pair(db, 999999, alex.id)),
        ("a player who does not exist", lambda: identify.pair(db, shots[1].id, 999999)),
        ("a golfer who played somewhere else", lambda: identify.pair(db, shots[1].id, elsewhere.id)),
        ("a course that does not exist", lambda: identify.day(db, 999999, START, END)),
        ("a day that ends before it starts", lambda: identify.day(db, river.id, END, START)),
    ]:
        try:
            fn(); check(label, False, "no error raised")
        except ValueError as e:
            check(label, True, str(e))

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
