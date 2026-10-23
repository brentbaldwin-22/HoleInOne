"""THE SITE'S APPEARANCE, stored once and read by every page.

Only two things are stored: which colour direction the site wears and
whether it wears it dark or light. The palettes themselves live in the
stylesheet, because that is where colour belongs — what is kept here is
the CHOICE, so changing it is an admin click rather than a deploy.

Both values are validated against the lists below on the way in. A
stored direction the stylesheet has never heard of would leave the site
with no accent colour at all, so an unknown name is refused rather than
written.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AppSetting

SETTING_KEY = "site_theme"

# Keep in step with DIRECTIONS in frontend/src/theme.js.
#
# These name a LOCKUP and the six band colours that come with it, not a
# whole look: the type, the buttons and the linen ground are the same on
# all four. An unknown name is refused rather than stored, because the
# stylesheet has no [data-direction] block for it and the site would
# come up with the default ramp under a name nothing can explain.
#
# "linen" is the name this look carried while it was the only one; the
# same artwork is "sunset" now. It is accepted on the way IN and
# translated, so a theme stored under the old name — in this database
# or in a visitor's localStorage — keeps working.
DIRECTIONS = ("sunset", "fairway", "sky", "ember")
LEGACY_DIRECTIONS = {"linen": "sunset"}
# LIGHT ONLY. The frontend pins the mode regardless of what is stored,
# so a "dark" left in the database by an older admin would be ignored
# rather than obeyed — this keeps the two ends saying the same thing.
MODES = ("light",)

DEFAULT_THEME = {"direction": "sunset", "mode": "light"}


def _clean_direction(name):
    """A stored direction, translated and validated. Never raises."""
    if name in DIRECTIONS:
        return name
    return LEGACY_DIRECTIONS.get(name, DEFAULT_THEME["direction"])


def get_theme(db: Session) -> dict:
    row = db.get(AppSetting, SETTING_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}
    return {
        "direction": _clean_direction(stored.get("direction")),
        "mode": (stored.get("mode") if stored.get("mode") in MODES
                 else DEFAULT_THEME["mode"]),
    }


def set_theme(db: Session, payload: dict) -> dict:
    """Write whichever of the two keys were sent, leaving the other as is.

    Returns the theme as it now stands. Raises ValueError on a value
    outside the allowed lists.
    """
    current = get_theme(db)
    for key, allowed in (("direction", DIRECTIONS), ("mode", MODES)):
        if key in payload and payload[key] is not None:
            val = str(payload[key]).strip().lower()
            # Translate before validating, so a client still sending the
            # old name is corrected rather than rejected.
            if key == "direction":
                val = LEGACY_DIRECTIONS.get(val, val)
            if val not in allowed:
                raise ValueError(
                    f"{key} must be one of {', '.join(allowed)} — got {val!r}")
            current[key] = val

    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY, value=current)
        db.add(row)
    else:
        row.value = current
    db.commit()
    return current
