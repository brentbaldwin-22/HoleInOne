#!/usr/bin/env python3
"""Can the card tell weak signal from a data cap from a dying modem?

All three present as "uploads are slow", and until now separating them
needed an SSH session on the one link that will not hold one. The
runbook's own causal chain is why the numbers alone are not enough:

    weak signal (LOCATION)
          v  modem transmits at higher power
    higher current draw (MODEM)
          v  5V rail sags under sustained TX
    USB bus resets, modem re-enumerates (PI)

A location problem PRESENTS as a power problem, so a card that prints
RSRP and stops has handed the operator the same ambiguity in nicer
type. What has to survive here is the VERDICT: the sentence that names
which of the three this is and what to do about it.

    python3 tools/link_status_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.cameras import link_status  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def blob(**over):
    """A heartbeat's link block, as the agent sends it."""
    base = {"gateway": "192.168.5.1", "ok": True,
            "rsrp": -78, "rsrq": -9, "sinr": 22, "rssi": -60,
            "band": "B4", "carrier": "Telus", "usage": "1.2 GB",
            "usb_resets": 0, "uptime_seconds": 7200.0}
    base.update(over)
    return base


def main() -> int:
    # ---- a good mount, so slow uploads are NOT the location ---------
    s = link_status(blob())
    check("a strong signal reads ok", s["level"], "ok")
    check("and the verdict rules the mount OUT, which is the whole job",
          "not the mount" in s["verdict"], True)

    # ---- a bad mount -------------------------------------------------
    s = link_status(blob(rsrp=-115, sinr=-3))
    check("a weak signal reads bad", s["level"], "bad")
    check("and the verdict sends you to the antenna, not to another modem",
          "antenna" in s["verdict"] and "not another modem" in s["verdict"],
          True)

    # ---- the middle, which is where this link actually lives ---------
    s = link_status(blob(rsrp=-97, sinr=6))
    check("a marginal signal reads warn", s["level"], "warn")
    check("and says why it heartbeats but will not upload",
          "not for sustained upload" in s["verdict"], True)

    # ---- WORST WINS. A clean RSRP with the noise floor on top of it
    # is the classic congested-cell reading, and averaging the two
    # would report it as fine.
    check("good RSRP with bad SINR is not reported as good",
          link_status(blob(rsrp=-75, sinr=-5))["level"], "bad")
    check("and bad RSRP with good SINR is not either",
          link_status(blob(rsrp=-112, sinr=25))["level"], "bad")

    # ---- THE RUNBOOK'S BANDWIDTH-OR-HARDWARE TEST -------------------
    # Zero is a finding, not a blank: it is what says "stop here and do
    # not swap anything".
    s = link_status(blob(usb_resets=0))
    check("zero resets is stated, not omitted",
          "not re-enumerating" in s["verdict"], True)
    s = link_status(blob(usb_resets=14))
    check("a reset count is reported", s["usb_resets"], 14)
    check("and names the power chain as next",
          "power chain" in s["verdict"], True)
    check("and a resetting modem is never still 'ok' overall",
          s["level"], "warn")
    # THE CONTROL, or "resets escalate the level" would pass on a
    # function that returned warn for everything.
    check("a clean rig with no resets stays ok (control)",
          link_status(blob(usb_resets=0))["level"], "ok")
    # ...and a genuinely bad signal is not DOWNgraded to warn by them.
    check("resets do not soften a bad signal",
          link_status(blob(rsrp=-120, sinr=-8, usb_resets=9))["level"], "bad")

    # ---- FIRMWARE WE HAVE NOT SEEN ----------------------------------
    # The LM1200's JSON shape is unconfirmed on our unit. Values arrive
    # as strings with units attached at least as often as as numbers,
    # and a parser that only accepts floats reports "no signal" from a
    # modem that is answering perfectly well.
    s = link_status(blob(rsrp="-78 dBm", sinr="22.0"))
    check("a string reading with its unit still parses", s["rsrp"], -78.0)
    check("and so does a bare decimal string", s["sinr"], 22.0)
    check("and it reaches the same verdict as the numeric form",
          s["level"], "ok")

    # ---- THE MODEM IS THERE AND WILL NOT TALK -----------------------
    # Distinct from "no modem fitted", because one is a site visit and
    # the other is nothing at all.
    s = link_status({"gateway": "192.168.5.1", "ok": False,
                     "error": "model.json returned 404"})
    check("a modem that answered unreadably is not silence",
          s["level"], "unknown")
    check("and the card is told where to look", "192.168.5.1" in s["verdict"],
          True)
    check("and what it said", "404" in s["verdict"], True)

    # ---- NOTHING AT ALL ---------------------------------------------
    # A gateway with `ok: False` is the unreadable-modem case above, not
    # this one -- the agent deliberately sends NO link block at all when
    # it could not find a modem, so "nothing fitted" arrives as an empty
    # reading rather than as an unreachable address.
    check("a rig with no modem and no counters reports nothing",
          link_status({}), None)
    check("and a non-dict is not a reading", link_status("nope"), None)
    check("and neither is None", link_status(None), None)

    # ---- counters without a modem still earn a row ------------------
    # A rig on Ethernet has no signal to report but its USB resets and
    # uptime are still the answer to "did this thing reboot?".
    s = link_status({"usb_resets": 0, "uptime_seconds": 3600})
    check("uptime alone is still worth a card row", s["level"], "unknown")
    check("and carries the reset finding",
          "not re-enumerating" in s["verdict"], True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
