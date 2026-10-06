#!/usr/bin/env python3
"""Does the stored theme survive four lockups and a rename?

The look's only key was "linen" while there was only one of it. The
same artwork is "sunset" now, and the old name is sitting in this
database and in every visitor's localStorage. Falling back to the
default on an unknown name would have hidden that — the default IS
sunset, so it would have looked correct and been correct by luck, until
the default changed.

    python3 tools/site_theme_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine                     # noqa: E402
from sqlalchemy.orm import sessionmaker                  # noqa: E402

from app.models import AppSetting, Base                  # noqa: E402
from app.services import site_theme                      # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def main() -> int:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    # ---- the four on offer ------------------------------------------
    check("four lockups are offered", len(site_theme.DIRECTIONS), 4)
    for key in ("sunset", "fairway", "sky", "ember"):
        site_theme.set_theme(db, {"direction": key})
        check(f"{key} round-trips", site_theme.get_theme(db)["direction"], key)

    # ---- the rename, from both directions ---------------------------
    # Written straight into the row, as an older build left it.
    db.merge(AppSetting(key=site_theme.SETTING_KEY,
                        value={"direction": "linen", "mode": "light"}))
    db.commit()
    # THE DEFAULT IS MOVED FOR THIS CHECK, and that is the whole point.
    # The default is sunset, so "linen -> sunset" is also what a plain
    # fall-back-to-default would produce: the first version of this
    # check passed with the translation deleted. Pointing the default
    # somewhere else is what makes the two tell apart.
    _real = site_theme.DEFAULT_THEME["direction"]
    site_theme.DEFAULT_THEME["direction"] = "ember"
    try:
        check("a theme stored as 'linen' reads as sunset, not the default",
              site_theme.get_theme(db)["direction"], "sunset")
        # And a client still sending the old name is corrected, not refused.
        check("'linen' is accepted on the way in",
              site_theme.set_theme(db, {"direction": "linen"})["direction"],
              "sunset")
    finally:
        site_theme.DEFAULT_THEME["direction"] = _real

    # ---- a name the stylesheet has no block for ---------------------
    # It must be REFUSED on write, not quietly stored: there would be no
    # [data-direction] block, so the site would come up on the default
    # ramp under a name nothing can explain.
    try:
        site_theme.set_theme(db, {"direction": "chartreuse"})
        check("an unknown direction is refused", "accepted", "ValueError")
    except ValueError as exc:
        check("an unknown direction is refused", "ValueError", "ValueError")
        check("and the message names the real choices",
              all(d in str(exc) for d in site_theme.DIRECTIONS), True)
    check("and the stored theme is untouched by the attempt",
          site_theme.get_theme(db)["direction"], "sunset")

    # ---- a row that predates the setting entirely -------------------
    db.query(AppSetting).delete()
    db.commit()
    check("no row at all gives the default",
          site_theme.get_theme(db)["direction"],
          site_theme.DEFAULT_THEME["direction"])

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
