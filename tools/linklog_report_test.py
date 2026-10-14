#!/usr/bin/env python3
"""Does the link-log reader say the right thing about a known file?

The CSV it reads is now the primary evidence for whether the tee rig's
connectivity got better or worse, so a reader that miscounts would send
us chasing the wrong hardware -- which this project has already done
more than once off a misread measurement.

Every assertion has a control beside it, because the ways this reader
could be wrong are all "it counted something that was not an outage":

  * '-' (the logger could not measure) counted as dead
  * partial loss counted as dead
  * the WAN ping counted instead of the gateway
  * a truncated or mangled row crashing the run

    python3 tools/linklog_report_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import linklog_report as rep  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


HEADER = "ts,uptime_s,temp_c,throttled,gw_loss_pct,gw_ms,net_loss_pct,net_ms"


def row(ts, up=1000, temp="45.0", thr="0x0", gwl="0", gwm="12.0",
        nl="0", nm="40.0"):
    return f"{ts},{up},{temp},{thr},{gwl},{gwm},{nl},{nm}"


# Two days. 13th: one dead minute in four. 14th: four dead in four --
# chosen so a reader that returns a constant, or that swaps the two
# buckets, cannot pass.
LINES = [
    HEADER,
    row("2026-10-13T09:00:00+00:00", up=100, temp="40.0"),
    row("2026-10-13T09:01:00+00:00", up=160, temp="41.0", gwl="100", gwm="-"),
    row("2026-10-13T09:02:00+00:00", up=220, temp="40.5"),
    row("2026-10-13T10:00:00+00:00", up=3820, temp="42.0"),
    row("2026-10-14T08:00:00+00:00", up=90, temp="50.0", gwl="100", gwm="-"),
    row("2026-10-14T08:01:00+00:00", up=150, temp="51.0", gwl="100", gwm="-"),
    row("2026-10-14T09:00:00+00:00", up=60, temp="52.0", gwl="100", gwm="-",
        thr="0x50005"),
    row("2026-10-14T09:01:00+00:00", up=120, temp="53.0", gwl="100", gwm="-"),
]
ROWS = rep.parse(LINES)

check("every data row parses, the header does not", len(ROWS), 8)
check(
    "dead minutes per day",
    {k: v for k, v in rep.bucket(ROWS, "_day").items()},
    {"2026-10-13": (1, 4), "2026-10-14": (4, 4)},
)
check(
    "hour buckets split the chosen day",
    rep.bucket([r for r in ROWS if r["_day"] == "2026-10-14"], "_hour"),
    {"08": (2, 2), "09": (2, 2)},
)
# uptime went 3820 -> 90 and 150 -> 60: two restarts. A reader that
# counted every row, or none, would miss this.
check("reboots found from uptime going backwards",
      [t for t, _, _ in rep.reboots(ROWS)],
      ["2026-10-14T08:00:00+00:00", "2026-10-14T09:00:00+00:00"])
check(
    "the one brownout is surfaced with its count",
    rep.throttle_counts(ROWS)["0x50005"][0],
    1,
)
check("...and the healthy value is counted separately",
      rep.throttle_counts(ROWS)["0x0"][0], 7)

# CONTROL: a reading the logger COULD NOT TAKE is not an outage. The
# logger writes 100 for a real failure, so '-' means the measurement
# never happened -- scoring it dead makes a broken logger look like a
# broken link.
check(
    "a '-' gateway reading is not counted dead",
    rep.is_dead(rep.parse([row("2026-10-14T11:00:00+00:00", gwl="-",
                                gwm="-")])[0]),
    False,
)
# CONTROL: partial loss is congestion, not a re-enumerating modem.
# Lumping them together is what kept "the link is bad" unfalsifiable.
check(
    "80% loss is NOT dead",
    rep.is_dead(rep.parse([row("2026-10-14T11:01:00+00:00",
                                gwl="80")])[0]),
    False,
)
check(
    "100% loss IS dead",
    rep.is_dead(rep.parse([row("2026-10-14T11:02:00+00:00",
                                gwl="100")])[0]),
    True,
)
# CONTROL: the GATEWAY is the modem. Total loss to 1.1.1.1 with the
# gateway answering is the carrier's problem, not a USB fault, and must
# not show up in this count.
check(
    "WAN loss with a live gateway is not a dead minute",
    rep.is_dead(rep.parse([row("2026-10-14T11:03:00+00:00", gwl="0",
                                nl="100", nm="-")])[0]),
    False,
)
# Temperature. The five dead minutes (41, 50, 51, 52, 53) average
# 49.4C; the three live ones (40, 40.5, 42) average 40.83C. The SIGN is
# what the report is for -- a cooking enclosure makes the dead minutes
# the hot ones -- but the means are pinned too, because a split that
# averaged the wrong set would still get the sign right here.
_hot, _cool = rep.temp_split(ROWS)
check("dead minutes are the hotter ones in this fixture",
      (round(_hot, 2), round(_cool, 2)), (49.4, 40.83))

# Junk must be skipped, not fatal: this file is appended to by cron
# while being read and trimmed by `tail`, so a short final line is
# ordinary.
JUNK = [
    HEADER,
    "2026-10-14T12:00:00+00:00,100,45.0,0x0,0",        # truncated
    "",                                                 # blank
    "garbage with no comma",
    "no-T-in-timestamp,100,45.0,0x0,0,1,0,1",
    "2026-10-14TXX:00:00+00:00,100,45.0,0x0,0,1,0,1",  # mangled hour
    row("2026-10-14T12:01:00+00:00"),                   # the one good row
]
check("junk rows are skipped, the good one survives",
      [r["ts"] for r in rep.parse(JUNK)], ["2026-10-14T12:01:00+00:00"])

# And the whole report runs over the junk file without raising -- the
# day it is needed is the day the file is ugly.
_out: list[str] = []
try:
    rep.report(rep.parse(JUNK), out=_out.append)
    _ran = True
except Exception as exc:  # noqa: BLE001
    _ran = f"raised {type(exc).__name__}: {exc}"
check("the full report survives a junk file", _ran, True)
# CONTROL: and it says something rather than silently printing nothing.
check("an empty file is reported, not crashed",
      rep.parse([HEADER]) == [] and (
          lambda o: (rep.report([], out=o.append), o)[1])([])[0],
      "no usable rows")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
