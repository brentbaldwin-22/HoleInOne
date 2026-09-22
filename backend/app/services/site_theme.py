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

# Keep in step with the [data-direction] blocks in frontend/src/styles.css
# and with DIRECTIONS in frontend/src/theme.js.
DIRECTIONS = ("broadcast", "dusk", "turf")
MODES = ("dark", "light")

DEFAULT_THEME = {"direction": "broadcast", "mode": "dark"}


def get_theme(db: Session) -> dict:
    row = db.get(AppSetting, SETTING_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}
    return {
        "direction": (stored.get("direction")
                      if stored.get("direction") in DIRECTIONS
                      else DEFAULT_THEME["direction"]),
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
