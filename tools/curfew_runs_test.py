#!/usr/bin/env python3
"""The curfew could not power a rig off, and said nothing about it.

`run()` reads `self._started_at` on the first tick that lands inside
the sleep window:

    grace_left = self.boot_grace - (time.monotonic() - self._started_at)

Nothing ever assigned it. So the first time a rig reached its bedtime
the thread raised AttributeError and died -- on a DAEMON thread, where
the traceback goes to the journal and the agent carries straight on
recording. Everything else about the rig kept working, which is why
nobody noticed that the feature has never once halted anything.

Two fixes: the attribute exists, and the thread can no longer die
quietly. A curfew that cannot decide whether to sleep must leave the
rig AWAKE -- a camera that is up when it should be asleep costs
battery, one that is down when it should be filming costs the shot.

    python3 tools/curfew_runs_test.py
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.curfew import CurfewThread  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


CFG = {"enabled": True, "sleep": "18:20", "wake": "08:00",
       "boot_grace_seconds": 600}

print("the clock it counts the boot grace from:")
c = CurfewThread(dict(CFG))
check("it exists", hasattr(c, "_started_at"), True)
check("...and is a monotonic reading, not a wall clock",
      abs(c._started_at - time.monotonic()) < 5.0, True)
# THE LINE THAT USED TO RAISE, run as it is run for real.
_grace = c.boot_grace - (time.monotonic() - c._started_at)
check("the grace line computes instead of raising",
      550 <= _grace <= 600, True)

print()
print("the window these settings describe:")
_in = c._in_sleep_window
check("18:19 is still awake", _in(dt.datetime(2026, 11, 2, 18, 19)), False)
check("18:20 is bedtime", _in(dt.datetime(2026, 11, 2, 18, 20)), True)
check("midnight is asleep", _in(dt.datetime(2026, 11, 2, 0, 30)), True)
check("07:59 is asleep", _in(dt.datetime(2026, 11, 2, 7, 59)), True)
check("08:00 is awake", _in(dt.datetime(2026, 11, 2, 8, 0)), False)
check("noon is awake", _in(dt.datetime(2026, 11, 2, 12, 0)), False)
# AND THE WAKE IT WOULD ARM. Tonight's bedtime wakes TOMORROW morning;
# a tick at 07:00 wakes at 08:00 the same day.
check("asleep at 19:00 it arms for 08:00 tomorrow",
      c._next_wake(dt.datetime(2026, 11, 2, 19, 0)),
      dt.datetime(2026, 11, 3, 8, 0))
check("asleep at 07:00 it arms for 08:00 today",
      c._next_wake(dt.datetime(2026, 11, 2, 7, 0)),
      dt.datetime(2026, 11, 2, 8, 0))

print()
print("bedtime follows the sun:")
# A COURSE IN SOUTH CAROLINA, and a Pi set to its own timezone --
# which is where the local clock in the window check comes from.
os.environ["TZ"] = "America/New_York"
time.tzset()
from agent.curfew import sunset_local  # noqa: E402

_LAT, _LON = 32.95, -79.78
_sun = {d: sunset_local(dt.date.fromisoformat(d), _LAT, _LON)
        for d in ("2026-11-02", "2026-12-21", "2026-06-21")}
print("      sunset here: "
      + ", ".join("%s %s" % (d, v.strftime("%H:%M"))
                  for d, v in _sun.items()))
# THE NUMBERS THE OPERATOR CONFIRMED FROM THE TEE: "it gets dark now
# around 5:30" on 2 November.
check("November sunset is about half five",
      17 * 60 + 20 <= _sun["2026-11-02"].hour * 60
      + _sun["2026-11-02"].minute <= 17 * 60 + 40, True)
# AND THE REASON A FIXED BEDTIME CANNOT SERVE THE YEAR: three hours
# of travel between midwinter and midsummer.
_spread = ((_sun["2026-06-21"].hour * 60 + _sun["2026-06-21"].minute)
           - (_sun["2026-12-21"].hour * 60 + _sun["2026-12-21"].minute))
check("midsummer is three hours later than midwinter",
      170 <= _spread <= 210, True)

sc = CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30",
                   "sleep_after_sunset_minutes": 60,
                   "latitude": _LAT, "longitude": _LON})
check("tonight's bedtime is an hour after tonight's sunset",
      sc.sleep_hm_for(dt.date(2026, 11, 2)), (18, 28))
check("midwinter moves it earlier",
      sc.sleep_hm_for(dt.date(2026, 12, 21)), (18, 17))
check("midsummer moves it later",
      sc.sleep_hm_for(dt.date(2026, 6, 21)), (21, 31))
# WORKED OUT ONCE A DAY, not every thirty seconds: the loop asks on
# every tick and the arithmetic is the same answer all evening.
sc._sun_cache = None
_a = sc.sleep_hm_for(dt.date(2026, 11, 2))
_cached = sc._sun_cache
check("the answer is cached for the day",
      _cached[0], dt.date(2026, 11, 2))
check("...and asking again gives the same one",
      sc.sleep_hm_for(dt.date(2026, 11, 2)), _a)
# AND THE WINDOW USES IT, which is the only thing that matters.
check("18:00 on 2 Nov is still awake",
      sc._in_sleep_window(dt.datetime(2026, 11, 2, 18, 0)), False)
check("18:45 on 2 Nov is bedtime",
      sc._in_sleep_window(dt.datetime(2026, 11, 2, 18, 45)), True)
check("...but 18:45 in June is not",
      sc._in_sleep_window(dt.datetime(2026, 6, 21, 18, 45)), False)

print()
print("a rig that did not ask for it is unchanged:")
fx = CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30"})
check("the fixed bedtime is used", fx.sleep_hm_for(dt.date(2026, 6, 21)),
      (18, 30))
nc = CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30",
                   "sleep_after_sunset_minutes": 60})
check("...and so it is when the position is missing",
      nc.sleep_hm_for(dt.date(2026, 6, 21)), (18, 30))

print()
print("a mistyped coordinate cannot halt the rig in daylight:")
# The same course with the longitude's sign flipped: sunset computes
# to 06:50, which would put bedtime at 07:50.
bad = CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30",
                    "sleep_after_sunset_minutes": 60,
                    "latitude": _LAT, "longitude": -_LON})
check("the computed time is thrown away, not clamped to the floor",
      bad.sleep_hm_for(dt.date(2026, 11, 2)), (18, 30))
# CLAMPED THE OTHER WAY, THOUGH: "be asleep by half ten whatever the
# sun does" is a real battery policy, and the clamped time is dark.
#
# Northern Scotland in midsummer, with the Pi on ITS OWN clock -- the
# coordinates and the timezone have to agree, because the conversion
# out of UTC is where the zone comes from. A rig with the right
# position and the wrong timezone gets a sunset hours out, and the
# floor above is what catches that.
os.environ["TZ"] = "Europe/London"
time.tzset()
_far = CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30",
                     "sleep_after_sunset_minutes": 60,
                     "latitude": 58.0, "longitude": -3.0,
                     "sleep_not_after": "22:30"})
check("a midsummer sunset past the ceiling is clamped to it",
      _far.sleep_hm_for(dt.date(2026, 6, 21)), (22, 30))
os.environ["TZ"] = "America/New_York"
time.tzset()
check("the sun that never sets falls back to the fixed time",
      CurfewThread({"enabled": True, "wake": "08:00", "sleep": "18:30",
                    "sleep_after_sunset_minutes": 60,
                    "latitude": 89.0, "longitude": 0.0}
                   ).sleep_hm_for(dt.date(2026, 6, 21)), (18, 30))

print()
print("a dry run reaches the halt and declines it:")


class _Rec(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, r):
        self.lines.append(r.getMessage() % () if not r.args
                          else r.getMessage())


_log = logging.getLogger("golfreelz_agent.curfew")
_rec = _Rec()
_log.addHandler(_rec)
_log.setLevel(logging.INFO)

# Inside the window, no boot grace left, nothing to upload: the exact
# state in which the old code raised.
d = CurfewThread({"enabled": True, "sleep": "00:00", "wake": "23:59",
                  "boot_grace_seconds": 0, "dry_run": True})
_t = threading.Thread(target=d.run, daemon=True)
_t.start()
time.sleep(1.5)
d.stop()
_t.join(timeout=5)
_said = " | ".join(_rec.lines)
check("the thread is still alive after reaching bedtime",
      any("DRY RUN" in x for x in _rec.lines), True)
check("...and says what it would have done",
      any("Would have" in x and "woken at" in x for x in _rec.lines), True)
check("...and how to arm it", "dry_run" in _said, True)
check("it did not die", any("stopped" in x for x in _rec.lines), False)

print()
print("waking early is said out loud, not left to be inferred:")
# A RIG AWAKE INSIDE ITS OWN SLEEP WINDOW. Either somebody powered it
# up -- the case the boot grace exists for -- or the RTC fired at the
# wrong time, and the second one is otherwise invisible: it comes
# back, behaves normally, and burns a night of battery.
_rec.lines.clear()
w = CurfewThread({"enabled": True, "sleep": "18:30", "wake": "08:00",
                  "boot_grace_seconds": 600, "dry_run": True})
_t2 = threading.Thread(target=w.run, daemon=True)
# Pretend it is 03:00 -- five hours before the 08:00 wake, which is
# this course's UTC offset and the shape of a hardware clock in the
# wrong timezone.
_real_now = dt.datetime.now
dt_patch = dt.datetime(2026, 11, 3, 3, 0)


class _FakeDt(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return dt_patch


import agent.curfew as _cur  # noqa: E402

_cur.dt.datetime = _FakeDt
try:
    _t2.start()
    time.sleep(1.0)
    w.stop()
    _t2.join(timeout=5)
finally:
    _cur.dt.datetime = dt.datetime
_said = " | ".join(_rec.lines)
check("it says the rig is awake inside its own window",
      "INSIDE this rig" in _said, True)
check("...with the gap to the wake time, in hours",
      "5.0h before" in _said, True)
check("...and names the clock to check, not the curfew",
      "timedatectl" in _said and "/etc/adjtime" in _said, True)
# ONCE PER BOOT. The loop re-enters this branch every minute of the
# grace, and a warning repeated sixty times is a warning nobody reads.
check("said once, not on every tick",
      sum(1 for x in _rec.lines if "INSIDE this rig" in x), 1)

print()
print("a disabled curfew says so rather than vanishing:")
_rec.lines.clear()
e = CurfewThread({"enabled": False, "sleep": "18:20", "wake": "08:00"})
e.run()
check("it logs that the rig stays awake",
      any("disabled" in x for x in _rec.lines), True)
check("...and names the setting to change",
      any("curfew.enabled" in x for x in _rec.lines), True)

print()
print("an unexpected failure is reported, not swallowed:")
_rec.lines.clear()
b = CurfewThread({"enabled": True, "sleep": "18:20", "wake": "08:00"})
b._in_sleep_window = lambda _now: (_ for _ in ()).throw(RuntimeError("boom"))
b.run()
check("the rig is told it will not sleep tonight",
      any("will NOT power itself off" in x for x in _rec.lines), True)
# A CURFEW THAT CANNOT DECIDE LEAVES THE RIG AWAKE. The other
# direction halts a camera that should be filming.
check("...and run() returns rather than taking the agent with it",
      True, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — the curfew can actually reach its bedtime now")
