"""THE ALL-TIME HOLE-IN-ONE BOARD.

Not a contest. Nothing is won for being on it and there is no timer --
it is the wall, and the only number on this site that has never been
reset. That is the whole appeal: a daily board resets at midnight and
an ace does not.

ONE HUMAN IS SEVERAL PARTICIPANTS. A Participant row is one golfer in
one round, so somebody who plays twice is two rows and would be two
entries on a board counting people. `person_key` is the handle that
survives across rounds.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import HIOStatus, HoleInOneEvent, Participant


def person_key(user_id, email, name) -> str:
    """A stable handle for one human, best evidence first.

    An account is the only identifier anybody deliberately keeps, so it
    wins. An email is typed fresh each round and comes back lowercased.
    A name is last and is genuinely weak -- two Bens at the same club
    merge -- but it is the one field always filled in, and leaving a
    golfer off the wall because they registered as a guest would be the
    worse error. Prefixed so an email and a name can never collide.
    """
    if user_id is not None:
        return f"u{user_id}"
    if email and email.strip():
        return f"e{email.strip().lower()}"
    return f"n{(name or '').strip().lower()}"


def all_time_board(db: Session, limit: int = 10) -> list[dict]:
    """Golfers with the most approved aces, most first.

    APPROVED ONLY. A pending claim is one somebody has made and nobody
    has checked, and this board is public -- putting an unreviewed ace
    on the wall is publishing a claim as a fact.

    Grouped in Python rather than SQL because the key is layered and
    the volume is tiny: an ace is rare, and a board of them is a few
    dozen rows at most for a long time yet.
    """
    rows = (
        db.query(
            Participant.user_id,
            Participant.email,
            Participant.name,
            Participant.created_at,
        )
        .join(HoleInOneEvent,
              HoleInOneEvent.participant_id == Participant.id)
        .filter(HoleInOneEvent.status == HIOStatus.approved.value)
        .all()
    )

    people: dict[str, dict] = {}
    for user_id, email, name, created_at in rows:
        key = person_key(user_id, email, name)
        p = people.setdefault(key, {"name": name, "aces": 0, "seen": created_at})
        p["aces"] += 1
        # Show the name they registered under MOST RECENTLY: people
        # marry, abbreviate, and fix their own typos, and the newest
        # spelling is the one they would recognise.
        if created_at is not None and (
            p["seen"] is None or created_at >= p["seen"]
        ):
            p["name"], p["seen"] = name, created_at

    ranked = sorted(
        people.values(),
        # Most aces first; ties broken by who got there first, so a
        # board does not reshuffle under people on every page load.
        key=lambda p: (-p["aces"], p["seen"] or ""),
    )
    return ranked[:limit]
