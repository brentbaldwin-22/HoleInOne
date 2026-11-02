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
