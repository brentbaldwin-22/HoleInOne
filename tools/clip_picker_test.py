"""Keeping the pick-your-shot list short, and honest about ties.

The list has to be short enough to scan and still contain the golfer's
own swing. A window around the tee time cannot do both -- the tee time
is for the first hole, the camera is on the eighth, and pace of play
moves the arrival by twenty minutes either way -- so the list is cut on
the GAPS between groups instead, and the tee time only ranks them.

Everything here uses a morning shaped like real play: groups of four
going out every ten minutes.
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
from app.services import clip_picker

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def main():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()

    course = Course(name="Rivertowne Country Club", par3_holes=[8],
                    minutes_per_hole=14)
    db.add(course); db.flush()

    DAY = datetime(2026, 11, 2, 12, 0)          # 8am local, naive UTC
    def clip(at, hole=8, owner=None):
        c = VideoClip(course_id=course.id, hole_number=hole, camera_type="tee",
                      captured_at=at, source_url=f"s{at.isoformat()}.mp4",
                      participant_id=owner,
                      processing_status=(ClipProcessingStatus.assigned.value if owner
                                         else ClipProcessingStatus.unassigned.value))
        db.add(c); db.flush(); return c

    # Four groups reaching hole 8 ten minutes apart, four swings each
    # about forty seconds apart.
    groups = []
    for g in range(4):
        base = DAY + timedelta(minutes=10 * g)
        groups.append([clip(base + timedelta(seconds=40 * i)) for i in range(4)])

    def register(minutes_before_hole8, name="Ben"):
        # Tee off on hole 1; hole 8 is 7 * minutes_per_hole later.
        arrive = DAY + timedelta(minutes=minutes_before_hole8)
        tee1 = arrive - timedelta(minutes=14 * 7)
        tt = TeeTime(course_id=course.id, starts_at=tee1, max_players=1)
        db.add(tt); db.flush()
        p = Participant(tee_time_id=tt.id, name=name, email=f"{name}@t.test",
                        group_size=1, created_at=tee1 - timedelta(minutes=5))
        db.add(p); db.flush(); return p

    print("the gaps, not a window")
    p = register(0)
    all_clips = clip_picker.available_clips(db, p)
    cl = clip_picker.cluster_by_gap(all_clips)
    check("sixteen swings fall into four groups", len(cl) == 4,
          f"{[len(g) for g in cl]}")
    check("each group is the foursome that hit it",
          all(len(g) == 4 for g in cl), f"{[len(g) for g in cl]}")

    print("\nthe list the golfer actually sees")
    got = clip_picker.candidates(db, p)
    check("shows one group, not the morning", len(got["clips"]) == 4,
          f'{len(got["clips"])} of {got["total_unclaimed"]} unclaimed')
    check("and it is the group nearest their expected arrival",
          {c.id for c in got["clips"]} == {c.id for c in groups[0]})
    check("says there are others to step through", got["has_more"] is True)
    check("group_count is the whole morning", got["group_count"] == 4)

    print("\nslow play moves the right group, and the tap still finds it")
    # Teed off expecting hole 8 at 8:00 but actually got there at 8:30.
    late = register(0, name="Slow")
    got0 = clip_picker.candidates(db, late, offset=0)
    got2 = clip_picker.candidates(db, late, offset=2)
    check("offset 0 is the predicted group",
          {c.id for c in got0["clips"]} == {c.id for c in groups[0]})
    check("offset 2 reaches the group they were really in",
          {c.id for c in got2["clips"]} == {c.id for c in groups[2]})
    check("offset past the end clamps instead of erroring",
          len(clip_picker.candidates(db, late, offset=99)["clips"]) == 4)

    print("\nconstraints that cost nothing")
    # THE SIGN IS AT THE TEE. A golfer who plays No. 8 and scans on the
    # way to the green registered AFTER their shot was recorded, so a
    # hard floor at created_at would hide the only clip they paid for.
    just_before = clip(DAY - timedelta(hours=2))
    check("a shot hit shortly before they signed up is still offered",
          just_before.id in {c.id for c in clip_picker.available_clips(db, p)})
    long_before = clip(DAY - timedelta(hours=9))
    check("but not one from before they could plausibly have been there",
          long_before.id not in {c.id for c in clip_picker.available_clips(db, p)})
    far = clip(DAY + timedelta(hours=20))
    check("nor one outside the match horizon",
          far.id not in {c.id for c in clip_picker.available_clips(db, p)})
    flagged = clip(DAY + timedelta(minutes=1))
    flagged.processing_status = ClipProcessingStatus.flagged.value
    db.flush()
    check("nor one somebody flagged",
          flagged.id not in {c.id for c in clip_picker.available_clips(db, p)})

    print("\nclaiming")
    mine = [c.id for c in groups[0][:2]]
    out = clip_picker.claim(db, p, mine)
    check("takes the two I tapped", sorted(out["claimed"]) == sorted(mine), str(out))
    check("they leave everyone else's list",
          not ({*mine} & {c.id for c in clip_picker.available_clips(db, register(0, "Other"))}))
    check("and land in my gallery",
          db.query(VideoClip).filter(VideoClip.participant_id == p.id).count() == 2)

    other = register(0, name="Partner")
    race = clip_picker.claim(db, other, mine)
    check("a second tap on the same clip loses cleanly",
          race["claimed"] == [] and sorted(race["already_taken"]) == sorted(mine), str(race))
    check("and does not move it out of the first golfer's gallery",
          db.query(VideoClip).filter(VideoClip.participant_id == p.id).count() == 2)

    print("\nundo")
    check("a wrong tap can be given back", clip_picker.unclaim(db, p, mine[0]) is True)
    check("it is offered again",
          mine[0] in {c.id for c in clip_picker.available_clips(db, other)})
    check("but only by the golfer holding it",
          clip_picker.unclaim(db, other, mine[1]) is False)

    print("\nwhat gets played in the grid")
    c = groups[1][0]
    check("the wide render when there is nothing better",
          clip_picker.preview_url(c) == c.source_url)
    c.tracer_url = "t.mp4"; db.flush()
    check("the tracer over the raw source", clip_picker.preview_url(c) == "t.mp4")
    c.vertical_url = "v.mp4"; db.flush()
    check("but the 9:16 above both", clip_picker.preview_url(c) == "v.mp4")

    print("\nnothing to pick is not an error")
    empty = Course(name="Empty", par3_holes=[8]); db.add(empty); db.flush()
    tt = TeeTime(course_id=empty.id, starts_at=DAY, max_players=1); db.add(tt); db.flush()
    lonely = Participant(tee_time_id=tt.id, name="Nobody", group_size=1,
                         created_at=DAY); db.add(lonely); db.flush()
    got = clip_picker.candidates(db, lonely)
    check("empty course returns an empty list, not a crash",
          got["clips"] == [] and got["group_count"] == 0 and got["has_more"] is False)

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    sys.exit(main())
