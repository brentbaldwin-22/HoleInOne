"""The order a golfer's highlight reel plays in.

Closest to the pin, because that is the one number here that is measured
rather than inferred. What is worth pinning: a holed shot leads, the
order really is ascending distance and not whatever the database handed
back, ten is the cap, and a profile with nothing measured still gets a
reel instead of an empty card.
"""
import os, sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import highlights as H

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


class Clip:
    def __init__(self, cid, dist=None, cup=False, at=0):
        self.id = cid
        self.distance_from_pin_feet = dist
        self.ball_in_cup = cup
        self.captured_at = datetime(2026, 11, 7) + timedelta(minutes=at)


class Course:
    name = "Rivertowne Country Club"


def pairs(*clips):
    return [(c, Course()) for c in clips]


def main():
    co = Course()

    print("closest to the pin, nearest first")
    got = H.rank(pairs(Clip(1, 40), Clip(2, 4), Clip(3, 18)))
    check("ascending distance, not database order",
          [c.id for (c, _, _) in got] == [2, 3, 1], f"{[c.id for (c, _, _) in got]}")
    check("and each says how close", got[0][2] == "4 ft from the pin", got[0][2])

    print("\na holed shot leads")
    got = H.rank(pairs(Clip(1, 2), Clip(2, None, cup=True), Clip(3, 9)))
    check("above even a two-footer", got[0][0].id == 2, f"{got[0][0].id}")
    check("tagged as an ace", got[0][2] == "ACE", got[0][2])
    check("the rest still ascend", [c.id for (c, _, _) in got[1:]] == [1, 3])

    print("\ntwo aces and a field")
    got = H.rank(pairs(Clip(1, 5), Clip(2, None, cup=True), Clip(3, None, cup=True)))
    check("both lead", {got[0][0].id, got[1][0].id} == {2, 3})

    print("\nthe cap")
    many = pairs(*[Clip(i, dist=i) for i in range(1, 26)])
    got = H.rank(many)
    check("ten, not twenty-five", len(got) == H.REEL_SIZE, f"{len(got)}")
    check("and it is the ten CLOSEST", [c.id for (c, _, _) in got] == list(range(1, 11)),
          f"{[c.id for (c, _, _) in got]}")

    print("\nunmeasured shots do not dilute measured ones")
    got = H.rank(pairs(Clip(1, None, at=50), Clip(2, None, at=60), Clip(3, 30)))
    check("the one measured shot is the reel", [c.id for (c, _, _) in got] == [3],
          f"{[c.id for (c, _, _) in got]}")
    check("rather than being buried under newer unmeasured ones", len(got) == 1)

    print("\nnothing measured at all")
    got = H.rank(pairs(Clip(1, at=10), Clip(2, at=30), Clip(3, at=20)))
    check("still a reel, newest first", [c.id for (c, _, _) in got] == [2, 3, 1],
          f"{[c.id for (c, _, _) in got]}")
    check("with no tag, because nothing was measured", got[0][2] is None)

    print("\na golfer with no clips")
    check("returns nothing, and does not raise", H.rank([]) == [])

    print("\nzero feet that was not holed out")
    got = H.rank(pairs(Clip(1, 0), Clip(2, 3)))
    check("still sorts first on distance", got[0][0].id == 1)
    check("and reads as 0 ft, not as an ace", got[0][2] == "0 ft from the pin", got[0][2])

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
