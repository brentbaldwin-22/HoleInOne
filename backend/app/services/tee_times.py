"""The moment a golfer says they are playing.

Registration used to make them pick from a list of tee times, which the
mock provider generated on the spot every ten minutes from 7am. The
course does not hand us its tee sheet, so those slots described nothing:
a golfer picked the one nearest the truth and the row recorded a booking
that did not exist. Now they give us the day and the time directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Course, TeeTime

# Not a business rule -- a guard. A mistyped or mis-parsed year puts a
# round in 1970 or 2126, where it never appears on any day's sheet and
# nothing explains why.
MAX_AHEAD = timedelta(days=365)
MAX_BEHIND = timedelta(days=365)


def parse_tee_time(iso: str) -> datetime:
    """An ISO 8601 instant as NAIVE UTC. Raises ValueError on anything else.

    Naive UTC because that is what every other datetime in this app is,
    and two things already depend on it: admin.py's candidate search
    compares `TeeTime.starts_at` against `clip.captured_at`, and
    frontend/src/time.js renders tz-less values by tagging them UTC.
    Store the golfer's wall clock instead and every tee time is wrong by
    the viewer's offset -- which still looks like a plausible time, which
    is what makes it worth a test.
    """
    raw = (iso or "").strip()
    if not raw:
        raise ValueError("a tee time is required")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"could not read the tee time {raw!r}")
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.utcnow()
    if not (now - MAX_BEHIND <= when <= now + MAX_AHEAD):
        raise ValueError("that tee time is not within the next year")
    return when


def tee_time_for(db: Session, course: Course, iso: str) -> TeeTime:
    """A row of this course's sheet for this golfer, at this instant.

    ONE ROW PER GOLFER, NOT ONE SHARED ROW PER MINUTE. The obvious
    alternative -- find an existing row at the same instant and join it
    -- hands `max_players` a veto over a sign-up it has no business
    refusing: two strangers who both pick 2:24 and the second is told the
    tee time is full. Nothing joins on this row's identity either. The
    matcher's candidate window is anchored to `Participant.created_at`
    and to the course (services/matcher.py), so `starts_at` is a record
    of what the golfer told us rather than a key.
    """
    tt = TeeTime(
        course_id=course.id,
        starts_at=parse_tee_time(iso),
        max_players=1,
    )
    db.add(tt)
    db.flush()
    return tt
