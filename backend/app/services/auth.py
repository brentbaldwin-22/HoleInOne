"""User authentication: bcrypt password hashing + JWT bearer tokens."""
from __future__ import annotations

from datetime import datetime, timedelta

import bcrypt
import jwt

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Participant

JWT_ALG = "HS256"


def last_registration(db: Session, user_id: int) -> dict | None:
    """What this golfer typed the last time they signed up for a round.

    THE ACCOUNT DOES NOT HOLD A PHONE NUMBER. User carries an email and a
    name and nothing else, so a returning golfer re-typed their mobile
    every round even while signed in. The registration rows do hold it,
    and the most recent one is the best guess at what they would type
    again.

    Their own entries rather than the account's fields, because the two
    can differ and the newer one is the one they chose: somebody who
    signed up as "Benny" at the club and holds an account reading "Ben
    Baldwin" meant Benny.

    Most recent by created_at, not by tee time -- a round booked for next
    week was filled in today, and today is what their details look like
    now.
    """
    row = (
        db.query(Participant)
        .filter(Participant.user_id == user_id)
        .order_by(Participant.created_at.desc())
        .first()
    )
    if row is None:
        return None
    return {"name": row.name, "mobile": row.mobile, "email": row.email}


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def issue_token(user_id: int) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "kind": "user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def issue_operator_token(course_id: int) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": f"course:{course_id}",
        "kind": "operator",
        "course_id": course_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def decode_token(token: str) -> int | None:
    """Returns user_id for legacy `user` tokens (and operator tokens are
    not user accounts so they return None here)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALG])
        if payload.get("kind", "user") != "user":
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def decode_operator_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALG])
        if payload.get("kind") != "operator":
            return None
        return int(payload["course_id"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
