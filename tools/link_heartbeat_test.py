#!/usr/bin/env python3
"""Does a modem reading on the heartbeat actually reach the card?

The unit tests either side of this one are solid and prove nothing
about the join: link_status can be perfect while the heartbeat drops
the field on the floor, and the card can render beautifully from a
payload the backend never fills in. The whole feature is a relay --
agent to heartbeat to column to payload to card -- and every seam in it
is a place where a rename or a missed Form() makes the pill silently
never appear.

Which would be discovered the next time the tee went down, by an
operator who then has to SSH in anyway. So the relay is tested whole,
over the real routes, against a real database.

    python3 tools/link_heartbeat_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_DB = Path(tempfile.mkdtemp()) / "link_hb.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401
from app.main import app  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def seed() -> str:
    """One course, one camera. Returns the camera's auth token."""
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        course = models.Course(name="Rivertowne Country Club")
        db.add(course)
        db.flush()
        cam = models.Camera(
            course_id=course.id, name="GR Cam 1", auth_token="tok-tee-1",
            assigned_role="tee", assigned_hole=8, kind="ip",
        )
        db.add(cam)
        db.commit()
        return cam.auth_token
    finally:
        db.close()


def main() -> int:
    token = seed()
    client = TestClient(app)
    admin = {"X-Admin-Password": os.environ["ADMIN_PASSWORD"]}

    def card():
        r = client.get("/api/admin/cameras", headers=admin)
        assert r.status_code == 200, r.text
        return r.json()[0]

    # ---- BEFORE: the key exists and is empty ------------------------
    # Not absent. The card tests the value, so a missing key and a null
    # one behave the same there -- but an absent key means the payload
    # was never wired and the pill can never appear.
    before = card()
    check("the payload carries a link slot from the start",
          "link" in before, True)
    check("and it is empty before any rig has reported",
          before["link"], None)

    # ---- THE HEARTBEAT ----------------------------------------------
    link = {"gateway": "192.168.5.1", "ok": True,
            "rsrp": -112, "rsrq": -17, "sinr": -2, "rssi": -98,
            "band": "B12", "carrier": "Telus", "usage": "0.4 GB",
            "usb_resets": 0, "uptime_seconds": 5400.0}
    r = client.post(f"/api/cameras/{token}/heartbeat",
                    data={"firmware_version": "tee-0.1.0+abc1234",
                          "link_info": json.dumps(link)})
    check("the heartbeat is accepted", r.status_code, 200)

    # ---- AFTER: it is on the card, interpreted ----------------------
    after = card()["link"]
    check("the reading reached the card", after is not None, True)
    if after is None:
        # Everything below reads fields off it. Stop here rather than
        # bury the one finding that matters under a TypeError.
        print("\nthe relay is broken at the heartbeat — nothing below ran")
        return 1
    check("and was interpreted, not just echoed", after["level"], "bad")
    check("with the numbers intact", (after["rsrp"], after["sinr"]),
          (-112.0, -2.0))
    check("and the band and carrier", (after["band"], after["carrier"]),
          ("B12", "Telus"))
    check("and the verdict an operator acts on",
          "antenna" in after["verdict"], True)
    check("and when it was read", bool(after["updated_at"]), True)

    # ---- A SECOND HEARTBEAT REPLACES IT -----------------------------
    # THE CONTROL. Everything above would pass on an endpoint that
    # stored the first reading and ignored every one after it -- which
    # is a card that is confidently wrong for as long as you look at it.
    good = dict(link, rsrp=-76, sinr=21, rsrq=-8)
    r = client.post(f"/api/cameras/{token}/heartbeat",
                    data={"link_info": json.dumps(good)})
    check("the second heartbeat is accepted", r.status_code, 200)
    after2 = card()["link"]
    check("a recovered link is reported as recovered", after2["level"], "ok")
    check("and the stale number is gone", after2["rsrp"], -76.0)

    # ---- A RIG THAT SENDS NOTHING IS NOT BROKEN ---------------------
    # Agents older than this change send no link_info at all, over a
    # link too poor to carry an update. That must leave the last good
    # reading alone rather than blanking the card.
    r = client.post(f"/api/cameras/{token}/heartbeat",
                    data={"firmware_version": "tee-0.1.0+old"})
    check("a heartbeat with no link block still works", r.status_code, 200)
    check("and leaves the last reading standing",
          card()["link"]["rsrp"], -76.0)

    # ---- GARBAGE DOES NOT 500 THE HEARTBEAT -------------------------
    # The heartbeat is the keepalive. Losing it to a malformed
    # diagnostic would mean this feature could take the camera offline.
    r = client.post(f"/api/cameras/{token}/heartbeat",
                    data={"link_info": "{not json"})
    check("an unparseable link block does not break the keepalive",
          r.status_code, 200)
    check("and still leaves the last good reading",
          card()["link"]["rsrp"], -76.0)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
