"""Which shots to show a golfer so they can point at their own.

Nothing matches a swing to a person automatically -- services/matcher.py
ships a stub embedder that hashes the clip's URL, so in production it
round-robins. Rather than guess, we show the golfer a short list and let
them tap. The only hard problem left is keeping that list short.

A TIME WINDOW IS THE WRONG TOOL. The tee time a golfer gives us is for
the FIRST hole; the camera is on the eighth. `Course.minutes_per_hole`
puts the expected arrival an hour and a half later, and a group stuck
behind a scramble blows through that by twenty minutes. A window wide
enough to be safe is wide enough to hold every group that played that
morning.

SWINGS DO NOT ARRIVE EVENLY. A foursome hits four shots inside a few
minutes and then the tee is empty until the next group walks up. So the
list is cut on the GAPS instead: sort the hole's clips by capture time
and split wherever the silence runs longer than `GAP_SECONDS`. Each run
is one group, which is three to six clips -- the right number to look
at, and right whether the group was early, late or held up.

The tee time is then used only to RANK those clusters, never to filter
them, because being wrong about the rank costs one tap and being wrong
about a filter costs the clip.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..models import ClipProcessingStatus, Course, Participant, VideoClip

# How long the tee has to stay quiet before the next swing counts as a
# different group. Four minutes is a starting point, not a measurement:
# every clip on the system today is a test session, where the gaps are
# whatever the operator was doing. Re-derive it from the real gap
# distribution once groups are actually playing through.
GAP_SECONDS = 240

# A golfer who registers and never plays should not be shown tomorrow's
# clips. Same horizon the matcher uses, for the same reason.
def _horizon() -> timedelta:
    return timedelta(minutes=settings.match_window_minutes)


# HOW FAR BACK BEFORE SIGN-UP A SHOT CAN STILL BE YOURS.
#
# The matcher refuses any clip captured before `created_at` -- "you
# cannot appear in a clip shot before you registered". That was a guard
# against misassigning a stranger's swing, and it made sense while a
# model was doing the assigning.
#
# It does not survive the camera being ON the hole. The sign is at the
# tee: a golfer plays No. 8, likes what the sign says, and scans it on
# the way to the green -- their shot is already recorded, and the floor
# would hide the one clip they just paid for. Under self-pick the guard
# is not guarding anything either, because the golfer is the one saying
# which swing is theirs.
#
# So the window opens before registration as well as after. Four hours
# covers signing up at the turn, or at the end of a round, without
# reaching into the previous day's play.
PRE_REGISTRATION_BACKSTOP = timedelta(hours=4)


def available_clips(db: Session, participant: Participant) -> list[VideoClip]:
    """Every unclaimed clip this golfer could plausibly be in.

    Unclaimed, at their course, and inside the round window -- which
    opens PRE_REGISTRATION_BACKSTOP before they signed up, because the
    sign is at the tee and plenty of golfers will scan it after they
    have already hit.
    """
    course_id = participant.tee_time.course_id if participant.tee_time else None
    if course_id is None:
        return []
    start = participant.created_at
    return (
        db.query(VideoClip)
        .filter(
            VideoClip.course_id == course_id,
            VideoClip.participant_id.is_(None),
            VideoClip.captured_at >= start - PRE_REGISTRATION_BACKSTOP,
            VideoClip.captured_at <= start + _horizon(),
            VideoClip.processing_status != ClipProcessingStatus.flagged.value,
        )
        .order_by(VideoClip.captured_at.asc())
        .all()
    )


def cluster_by_gap(clips: list[VideoClip], gap_seconds: int = GAP_SECONDS) -> list[list[VideoClip]]:
    """Split a time-ordered list wherever the tee went quiet."""
    groups: list[list[VideoClip]] = []
    for clip in clips:
        if groups and clip.captured_at is not None:
            prev = groups[-1][-1].captured_at
            if prev is not None and (clip.captured_at - prev).total_seconds() <= gap_seconds:
                groups[-1].append(clip)
                continue
        groups.append([clip])
    return groups


def expected_arrival(participant: Participant, course: Course | None,
                     hole_number: int | None) -> datetime | None:
    """When this golfer should reach the hole the camera is on.

    Their tee time is for the first hole, so the walk is
    `minutes_per_hole` times the holes in between. Approximate on
    purpose -- it only decides which group is shown FIRST.
    """
    tt = participant.tee_time
    if tt is None or tt.starts_at is None or not hole_number:
        return None
    per_hole = (course.minutes_per_hole if course else None) or 14
    return tt.starts_at + timedelta(minutes=per_hole * (int(hole_number) - 1))


def _distance(group: list[VideoClip], target: datetime | None) -> float:
    if target is None:
        return 0.0
    mid = group[len(group) // 2].captured_at
    if mid is None:
        return float("inf")
    return abs((mid - target).total_seconds())


def rank_groups(groups: list[list[VideoClip]], participant: Participant,
                course: Course | None) -> list[list[VideoClip]]:
    """Likeliest group first. Ties and a missing tee time keep clock order."""
    if not groups:
        return []
    hole = groups[0][0].hole_number if groups[0] else None
    target = expected_arrival(participant, course, hole)
    if target is None:
        return groups
    return sorted(groups, key=lambda g: _distance(g, target))


def candidates(db: Session, participant: Participant, offset: int = 0) -> dict:
    """The group to show, and whether there are others to step through.

    `offset` walks the ranking: 0 is the likeliest group, 1 the next
    likeliest, and so on. That is the "not your group?" button -- the
    golfer resolves in one tap what no amount of guessing at the tee
    time could.
    """
    course = db.get(Course, participant.tee_time.course_id) if participant.tee_time else None
    groups = rank_groups(cluster_by_gap(available_clips(db, participant)),
                         participant, course)
    if not groups:
        return {"clips": [], "group_index": 0, "group_count": 0,
                "has_more": False, "total_unclaimed": 0}
    idx = max(0, min(int(offset), len(groups) - 1))
    chosen = groups[idx]
    return {
        "clips": chosen,
        "group_index": idx,
        "group_count": len(groups),
        "has_more": len(groups) > 1,
        "total_unclaimed": sum(len(g) for g in groups),
    }


def preview_url(clip: VideoClip) -> str:
    """What to actually play in the grid.

    The 9:16 cut first: it is already framed on the golfer and the ball,
    and it is what a phone wants. Falling back to the wide renders rather
    than showing nothing, because a clip with no vertical yet is still
    perfectly recognisable to the person who hit it.
    """
    return clip.vertical_url or clip.tracer_url or clip.source_url or ""


def claim(db: Session, participant: Participant, clip_ids: list[int]) -> dict:
    """Assign these clips to this golfer, skipping any already spoken for.

    THE CHECK IS HERE, NOT IN THE LIST THE BROWSER WAS HOLDING. Everyone
    in a foursome can have the same four clips on screen at the same
    moment, and two of them can tap the same one. The second tap has to
    lose quietly rather than move a clip out of the first golfer's
    gallery -- so ownership is re-read inside the transaction and a clip
    that already has an owner is reported back, not reassigned.
    """
    course_id = participant.tee_time.course_id if participant.tee_time else None
    claimed: list[int] = []
    taken: list[int] = []
    if not clip_ids:
        return {"claimed": claimed, "already_taken": taken}
    for clip in db.query(VideoClip).filter(VideoClip.id.in_(clip_ids)).all():
        if clip.course_id != course_id:
            continue
        if clip.participant_id is not None:
            if clip.participant_id != participant.id:
                taken.append(clip.id)
            continue
        clip.participant_id = participant.id
        clip.processing_status = ClipProcessingStatus.assigned.value
        claimed.append(clip.id)
    db.commit()
    return {"claimed": claimed, "already_taken": taken}


def unclaim(db: Session, participant: Participant, clip_id: int) -> bool:
    """Put a clip back. False when it was never this golfer's to give.

    A wrong tap that cannot be undone is a clip nobody can ever claim,
    and the golfer who did hit it has no way to ask for it back.
    """
    clip = db.get(VideoClip, clip_id)
    if not clip or clip.participant_id != participant.id:
        return False
    clip.participant_id = None
    clip.processing_status = ClipProcessingStatus.unassigned.value
    db.commit()
    return True
