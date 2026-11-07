"""What a returning golfer's form should already say.

The account holds an email and a name and no phone number at all, so a
signed-in golfer re-typed their mobile every single round. Their previous
registration has it. What is worth pinning is whose it is and which one:
one golfer's details must never reach another's form, and "most recent"
has to mean most recently FILLED IN, not the round furthest in the future.
"""
import os, sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Course, Participant, TeeTime, User
from app.services.auth import last_registration

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
    NOW = datetime(2026, 11, 7, 12, 0)

    ben = User(email="ben@t.test", password_hash="x", name="Ben Baldwin")
    pat = User(email="pat@t.test", password_hash="x", name="Pat")
    db.add_all([ben, pat]); db.flush()

    def register(user, name, mobile, email, filled_in, tees_off):
        tt = TeeTime(course_id=course.id, starts_at=tees_off, max_players=1)
        db.add(tt); db.flush()
        p = Participant(tee_time_id=tt.id, user_id=user.id if user else None,
                        name=name, mobile=mobile, email=email, group_size=1,
                        created_at=filled_in)
        db.add(p); db.flush(); return p

    print("a golfer who has never registered")
    check("has nothing to prefill, and that is not an error",
          last_registration(db, ben.id) is None)

    print("\nafter one round")
    register(ben, "Ben Baldwin", "+1 843 555 0100", "ben@t.test",
             filled_in=NOW - timedelta(days=30), tees_off=NOW - timedelta(days=30))
    got = last_registration(db, ben.id)
    check("the mobile comes back, which the account does not hold",
          got["mobile"] == "+1 843 555 0100", f'{got["mobile"]}')
    check("with the name and email they used", (got["name"], got["email"]) == ("Ben Baldwin", "ben@t.test"))

    print("\nafter changing their number")
    register(ben, "Benny", "+1 843 555 0199", "benny@t.test",
             filled_in=NOW - timedelta(days=1), tees_off=NOW - timedelta(days=1))
    got = last_registration(db, ben.id)
    check("the newer number wins", got["mobile"] == "+1 843 555 0199", f'{got["mobile"]}')
    check("and so does the name they chose over the account's",
          got["name"] == "Benny", f'{got["name"]} (account says {ben.name})')

    print("\na round booked for next week, filled in before the last one")
    register(ben, "Stale", "+1 843 555 0000", "stale@t.test",
             filled_in=NOW - timedelta(days=10), tees_off=NOW + timedelta(days=7))
    got = last_registration(db, ben.id)
    check("most recent means most recently FILLED IN, not the latest tee time",
          got["mobile"] == "+1 843 555 0199", f'{got["mobile"]}')

    print("\nsomebody else's round")
    register(pat, "Pat", "+1 843 555 0222", "pat@t.test",
             filled_in=NOW, tees_off=NOW)
    check("does not reach this golfer's form",
          last_registration(db, ben.id)["mobile"] == "+1 843 555 0199")
    check("and theirs is their own", last_registration(db, pat.id)["name"] == "Pat")

    print("\na walk-up who registered with no account attached")
    register(None, "Nobody", "+1 843 555 0333", "nobody@t.test",
             filled_in=NOW, tees_off=NOW)
    check("is not handed to a signed-in golfer",
          last_registration(db, ben.id)["mobile"] == "+1 843 555 0199")

    print("\na registration that left a field blank")
    register(ben, "Ben", None, "ben@t.test", filled_in=NOW, tees_off=NOW)
    got = last_registration(db, ben.id)
    check("comes back as None rather than breaking the form", got["mobile"] is None)

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
