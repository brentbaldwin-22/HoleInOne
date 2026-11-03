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
import agent.curfew as _cur  # noqa: E402


def _at(when):
    """A datetime class whose .now() is frozen at `when`.

    Every test of the halt path uses one. The alternative is a
    fixture that behaves differently depending on the hour the suite
    runs at, which this file has already been caught by once.
    """

    class _Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    return _Frozen



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
# THE LINE THAT USED TO RAISE, run as it is run for real.
_grace = c.boot_grace - (time.monotonic() - c._started_at)
check("the grace line computes instead of raising",
      isinstance(_grace, float), True)
# FROM BOOT, NOT FROM THE AGENT'S START, and this is the assertion
# that matters: measured from the agent's start, `update.sh` -- which
# stops and starts the service -- resets the grace, so an update run
# inside the sleep window halts the Pi ten minutes later. It did,
# under its owner, at 07:10 on a window that ended at 08:00.
with open("/proc/uptime") as _fh:
    _up = float(_fh.read().split()[0])
check("it is the boot instant, not this moment",
      abs(c._started_at - (time.monotonic() - _up)) < 2.0, True)
time.sleep(1.1)
c2 = CurfewThread(dict(CFG))
check("restarting the agent does not reset it",
      abs(c2._started_at - c._started_at) < 0.5, True)
check("...so a long-running rig is already past its grace",
      (c.boot_grace - (time.monotonic() - c._started_at)) < 0
      if _up > c.boot_grace else True, True)

print()
print("every attribute the loop reads actually exists:")
# THREE TIMES NOW. `_started_at` was read in run() and never assigned,
# which killed the thread the first time a rig reached bedtime. Then
# `_early_warned` went the same way when the sunset code was stripped
# back out. The failure is invisible by construction -- a daemon
# thread that raises is simply gone -- so it is worth testing the
# CLASS of bug rather than each instance of it.
import ast as _ast  # noqa: E402

_mod = _ast.parse((ROOT / "pi-agent" / "agent"
                   / "curfew.py").read_text(encoding="utf-8"))
_cls = next(n for n in _mod.body
            if isinstance(n, _ast.ClassDef) and n.name == "CurfewThread")
_reads = set()
for _fn in _cls.body:
    if not isinstance(_fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
        continue
    if _fn.name == "__init__":
        continue
    for _n in _ast.walk(_fn):
        if (isinstance(_n, _ast.Attribute)
                and isinstance(_n.value, _ast.Name)
                and _n.value.id == "self"
                and isinstance(_n.ctx, _ast.Load)):
            _reads.add(_n.attr)
_live = CurfewThread(dict(CFG))
_missing = sorted(a for a in _reads
                  if not hasattr(_live, a) and not a.startswith("__"))
print("      %d attribute(s) read outside __init__" % len(_reads))
check("none of them is missing from a fresh thread", _missing, [])

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
#
# ON A FROZEN CLOCK, because this fixture was "sleep 00:00 wake 23:59"
# -- always inside the window, whenever the suite runs -- and the
# minimum-sleep guard later made that depend on the hour after all:
# run it at 23:53 and the wake is six minutes away, so the curfew
# rightly declines to halt and the test failed on the time of day. A
# test of the halt path must not care what o'clock it is.
d = CurfewThread({"enabled": True, "sleep": "18:30", "wake": "08:00",
                  "boot_grace_seconds": 0, "dry_run": True})
_cur.dt.datetime = _at(dt.datetime(2026, 11, 3, 19, 0))
_t = threading.Thread(target=d.run, daemon=True)
try:
    _t.start()
    time.sleep(1.5)
    d.stop()
    _t.join(timeout=5)
finally:
    _cur.dt.datetime = dt.datetime
_said = " | ".join(_rec.lines)
check("the thread is still alive after reaching bedtime",
      any("Would have halted" in x for x in _rec.lines), True)
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
                  "boot_grace_seconds": 0, "dry_run": True})
_t2 = threading.Thread(target=w.run, daemon=True)
# Pretend it is 03:00 -- five hours before the 08:00 wake, which is
# this course's UTC offset and the shape of a hardware clock in the
# wrong timezone.
_cur.dt.datetime = _at(dt.datetime(2026, 11, 3, 3, 0))
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
print("it will not halt a rig that is nearly due to wake:")
# THE OTHER HALF OF WHAT POWERED A RIG OFF UNDER ITS OWNER. An agent
# restarted at 07:00, fifty minutes before an 08:00 wake, inside the
# window: the old code armed the RTC and pulled the power. A halt
# costs a boot cycle and stakes the morning on an alarm firing; over
# the last of a window it saves a few watt-minutes.
_rec.lines.clear()
m = CurfewThread({"enabled": True, "sleep": "18:30", "wake": "08:00",
                  "boot_grace_seconds": 0, "dry_run": True,
                  "min_sleep_minutes": 60})
_cur.dt.datetime = _at(dt.datetime(2026, 11, 3, 7, 10))
_t3 = threading.Thread(target=m.run, daemon=True)
try:
    _t3.start()
    time.sleep(1.0)
    m.stop()
    _t3.join(timeout=5)
finally:
    _cur.dt.datetime = dt.datetime
_said3 = " | ".join(_rec.lines)
check("50 minutes before the wake it stays up",
      "not worth a halt" in _said3, True)
check("...and never reaches the halt",
      "Would have halted" in _said3, False)
check("...saying how long is left", "50 min until" in _said3, True)

# BUT A REAL NIGHT STILL HAPPENS. The guard must not become "never
# sleep": at bedtime proper there are thirteen hours to go.
_rec.lines.clear()
m2 = CurfewThread({"enabled": True, "sleep": "18:30", "wake": "08:00",
                   "boot_grace_seconds": 0, "dry_run": True,
                   "min_sleep_minutes": 60})
_cur.dt.datetime = _at(dt.datetime(2026, 11, 3, 18, 35))
_t4 = threading.Thread(target=m2.run, daemon=True)
try:
    _t4.start()
    time.sleep(1.0)
    m2.stop()
    _t4.join(timeout=5)
finally:
    _cur.dt.datetime = dt.datetime
check("at bedtime proper it goes through with it",
      any("Would have halted" in x for x in _rec.lines), True)

print()
print("it will not power off a rig somebody is working on:")
_rec.lines.clear()
h = CurfewThread({"enabled": True, "sleep": "18:30", "wake": "08:00",
                  "boot_grace_seconds": 0, "dry_run": True})
_cur._who_is_logged_in = lambda: ["pi"]
_cur.dt.datetime = _at(dt.datetime(2026, 11, 3, 18, 35))
_t5 = threading.Thread(target=h.run, daemon=True)
try:
    _t5.start()
    time.sleep(1.0)
    h.stop()
    _t5.join(timeout=5)
finally:
    _cur.dt.datetime = dt.datetime
    _cur._who_is_logged_in = lambda: []
_said5 = " | ".join(_rec.lines)
check("an open session holds the halt off", "is logged in" in _said5, True)
check("...and it does not halt",
      "Would have halted" in _said5, False)
check("...naming who", "pi" in _said5, True)
# NOT FOREVER, THOUGH. A forgotten SSH session must not flatten the
# battery, so it is bounded by the same deferral the uploads get.
check("the hold is bounded, not indefinite",
      "up to" in _said5 and "min" in _said5, True)

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
