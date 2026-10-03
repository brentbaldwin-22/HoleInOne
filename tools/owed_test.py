#!/usr/bin/env python3
"""Does the 'clips owed' count mean what the card claims it means?

Standalone, like tools/poolhold_test.py — no pytest, no live backend.
Builds the real schema in SQLite, writes real CameraEvent rows, and
calls the real _owed_clips. Run from the repo root:

    python3 tools/owed_test.py

The claim under test is narrow and worth stating: the number is the
count of events this camera appears in, past the settle window, inside
the spool's own horizon, with its side's clip filename still null. Each
clause below is a case, because each one was a decision.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base, Camera, CameraEvent, Course
from app.routers.admin import _owed_clips

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def main() -> int:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    # MIRROR PRODUCTION, which sets expire_on_commit=False (see
    # app/database.py). It is not a detail here: with the default True,
    # reading c.id after a commit re-SELECTs every camera row, and the
    # statement count below would measure the test's own session
    # settings instead of the query this file exists to pin down.
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    # Count the statements this costs, so a future refactor that turns
    # the grouped query back into a per-camera loop fails here rather
    # than on Render at 7am. That loop is what took the server down.
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _tally(conn, cursor, stmt, params, context, executemany):
        statements.append(stmt)

    db.add(Course(id=1, name="Test Links"))
    cams = []
    for cid, role in ((1, "tee"), (2, "green"), (3, "tee"), (4, "tee")):
        c = Camera(
            id=cid, course_id=1, assigned_hole=8, assigned_role=role,
            auth_token=f"tok{cid}", name=f"cam{cid}",
        )
        cams.append(c)
        db.add(c)
    db.commit()

    now = datetime.utcnow()

    def ev(sid, tee_cam, green_cam, age, tee_clip=None, green_clip=None):
        db.add(CameraEvent(
            session_id=sid, tee_camera_id=tee_cam, green_camera_id=green_cam,
            course_id=1, hole_number=8, triggered_at=now - age,
            tee_clip_filename=tee_clip, green_clip_filename=green_clip,
        ))

    # Camera 1 (tee), paired with camera 2 (green).
    # — still inside the settle window: being made, not stuck.
    ev("fresh", 1, 2, timedelta(seconds=30))
    # — owed by both sides, 2h old.
    ev("owed-2h", 1, 2, timedelta(hours=2))
    # — owed by the tee only; the green already landed.
    ev("tee-only", 1, 2, timedelta(hours=1), green_clip="g.mp4")
    # — both landed: owed by nobody.
    ev("done", 1, 2, timedelta(hours=3), tee_clip="t.mp4", green_clip="g.mp4")
    # — past the spool's own horizon: lost, not owed.
    ev("ancient", 1, 2, timedelta(hours=30))
    # Camera 3: one recent-but-settled clip, to pin the level boundary.
    ev("cam3", 3, None, timedelta(minutes=10))
    # Camera 4: nothing at all.
    db.commit()

    statements.clear()
    owed = _owed_clips(db, cams)
    # Two grouped queries — the tee side and the green side — however
    # many cameras were passed in.
    check("query count", len(statements), 2)

    check("cam1 (tee) count", owed.get(1, {}).get("count"), 2)
    check("cam1 level (oldest 2h)", owed.get(1, {}).get("level"), "warn")
    check("cam1 summary", owed.get(1, {}).get("summary"),
          "2 clips owed, oldest 2h")

    check("cam2 (green) count", owed.get(2, {}).get("count"), 1)
    check("cam2 level (oldest 2h)", owed.get(2, {}).get("level"), "warn")

    check("cam3 count", owed.get(3, {}).get("count"), 1)
    check("cam3 level (oldest 10m)", owed.get(3, {}).get("level"), "ok")
    check("cam3 summary singular", owed.get(3, {}).get("summary"),
          "1 clip owed, oldest 10m")

    check("cam4 absent when it owes nothing", 4 in owed, False)

    # A stale backlog is the one that means something is wrong.
    ev("stale", 4, None, timedelta(hours=20))
    db.commit()
    check("cam4 level (oldest 20h)",
          _owed_clips(db, cams).get(4, {}).get("level"), "bad")

    # And the control: a camera list of nothing must not query at all.
    statements.clear()
    check("empty list short-circuits", _owed_clips(db, []), {})
    check("empty list costs no queries", len(statements), 0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
