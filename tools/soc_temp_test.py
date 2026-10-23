#!/usr/bin/env python3
"""Does the card show a Pi getting hot BEFORE it throttles?

The tee is slow by day and fine by evening, in three different
locations, while the green beside it is fine throughout. So it is
something about that rig, not the place or the tower -- and two
theories fit: it saturates its own uplink (its view is ~6.5 MB a clip
against the green's ~1 MB, and golfers play in the day), or it cooks,
because it runs the detector, the copy ring and the live stream in a
sealed box in the sun while the green mostly waits.

`throttled` already said whether a limit was HIT. That is useless for
telling these apart, because by the time a flag trips the afternoon is
over. The temperature says how close it is the rest of the time, which
is what makes the trend readable -- and readable from a phone, which
matters when the rig in question cannot hold an SSH session at noon.

    python3 tools/soc_temp_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.cameras import throttled_status  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def blob(**over):
    base = {"value": 0, "raw": "0x0", "flags": []}
    base.update(over)
    return base


def main() -> int:
    # ---- the ordinary case: cool, clean, and SAYS SO ----------------
    s = throttled_status(blob(temp_c=48.2))
    check("a cool rig still reports its temperature", s["temp_c"], 48.2)
    check("and is not alarming about it", s["level"], "ok")
    check("and the summary stays the plain one", s["summary"], "power clean")

    # ---- THE PI 5's OWN LIMITS: caps at 80, hard-throttles at 85 ----
    s = throttled_status(blob(temp_c=81.0))
    check("80C+ is a warning before anything has tripped",
          s["level"], "warn")
    check("and names the figure", "81" in s["summary"], True)
    s = throttled_status(blob(temp_c=86.5))
    check("85C+ is bad", s["level"], "bad")
    check("and says why", "hot" in s["summary"], True)

    # THE CONTROL. Without this, "warm is a warning" would pass on a
    # function that warned about everything.
    check("79C is still ok — the threshold is a threshold",
          throttled_status(blob(temp_c=79.0))["level"], "ok")

    # ---- POWER STILL OUTRANKS HEAT ----------------------------------
    # A brownout is the finding; a temperature is context. If a hot rig
    # masked an under-voltage the card would send someone to the wrong
    # half of the runbook.
    s = throttled_status(blob(value=1, raw="0x1", temp_c=86.0))
    check("under-voltage now wins over hot", s["summary"],
          "under-voltage right now")
    check("and is still bad", s["level"], "bad")
    s = throttled_status(blob(value=1 << 16, raw="0x50000", temp_c=50.0))
    check("a cool rig that browned out still says so",
          s["summary"], "browned out since boot")

    # ---- AN AGENT TOO OLD TO REPORT IT ------------------------------
    # Every rig updates on its own schedule over a link that sometimes
    # cannot carry an update, so this runs for weeks. It must read as
    # "not reported", never as 0°C and never as a warning.
    s = throttled_status(blob())
    check("no temperature is None, not zero", s["temp_c"], None)
    check("and does not warn", s["level"], "ok")
    check("and the summary is unchanged from before this existed",
          s["summary"], "power clean")

    # ---- junk from the wire -----------------------------------------
    check("a non-numeric temperature is dropped, not crashed",
          throttled_status(blob(temp_c="warm"))["temp_c"], None)
    check("and a string number is still read",
          throttled_status(blob(temp_c="48.2'C"))["temp_c"], 48.2)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
