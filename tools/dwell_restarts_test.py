#!/usr/bin/env python3
""""70 restart(s)" must mean THIS trigger, not the whole morning.

An event read:

    somebody stood in zone 2 for 2.2s · threshold 2s · 70 restart(s)

and the page's own comment says what that line is for: "a trigger that
needed several of them is one the detector nearly missed, which is
worth seeing next to a clip that starts late."

It never meant that. `Dwell.restarts` is zeroed in __init__ and never
again, so the number was every dwell the agent had ever abandoned since
it booted -- 70 people crossing a zone on a busy morning, not 70 failed
attempts at the golfer in front of it. Those two want opposite
responses (fix the detector / nothing is wrong), and the sentence on
the page is the one a reader takes away. It is the reading this
session's own diagnosis started down before the counter was checked.

So the rig sends both: this trigger's, and the lifetime total beside
it so a busy tee still reads as a busy tee.

    python3 tools/dwell_restarts_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.tee import Dwell  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# dwell 2s, grace 3s -- the rig's defaults.
def abandon(d, t):
    """Somebody appears, then leaves long enough to lose the clock.
    Returns the time afterwards."""
    d.update(t, True)                 # seen
    d.update(t + 4.0, False)          # gone past the grace
    return t + 4.0


print("\n-- a lifetime counter is not a per-trigger counter")
d = Dwell(2.0, 3.0)
t = 100.0
for _ in range(7):
    t = abandon(d, t) + 1.0
check("seven walkers abandoned seven dwells", d.restarts, 7)
check("  and none of them triggered", d.restarts_since_mark, 7)

# A golfer now stands still and triggers.
t += 1.0
d.update(t, True)
fired = d.update(t + 2.1, True)
check("a golfer who stands still triggers", fired, True)
check("  this trigger cost no restarts", d.restarts_since_mark, 7)
d.mark()
check("  and marking resets the per-trigger count", d.restarts_since_mark, 0)
check("  while the lifetime total is untouched", d.restarts, 7)


print("\n-- the next trigger reports only its own run-up")
t += 10.0
for _ in range(2):
    t = abandon(d, t) + 1.0
check("two more abandons", d.restarts_since_mark, 2)
check("  lifetime keeps counting", d.restarts, 9)
t += 1.0
d.update(t, True)
check("it triggers again", d.update(t + 2.1, True), True)
check("  and reports 2, not 9", d.restarts_since_mark, 2)
d.mark()
check("  then zero again", d.restarts_since_mark, 0)


print("\n-- the counter never goes negative")
# mark() on a fresh object, then nothing happens.
d2 = Dwell(2.0, 3.0)
d2.mark()
check("a marked, idle dwell reports 0", d2.restarts_since_mark, 0)


print("\n-- the rig sends both numbers")
src = (ROOT / "pi-agent" / "agent" / "tee.py").read_text(encoding="utf-8")
check("the detail carries this trigger's",
      'd["restarts"] = int(dwell.restarts_since_mark)' in src, True)
check("  and the lifetime total",
      'd["restarts_total"] = int(dwell.restarts)' in src, True)
check("  and it marks when a trigger fires", "dwell.mark()" in src, True)
# THE DETAIL IS BUILT BEFORE THE MARK. Marking first would make every
# event report 0 -- the counter would be correct and useless.
check("  building the detail happens before the mark",
      src.index("_dwell_detail_blob = self._dwell_detail")
      < src.index("dwell.mark()"), True)

# IN THE SHARED RENDERER. This read the old cameras page, which is
# where the trigger line used to be written; both cameras pages now
# render one component, so the assertion follows it there instead of
# going quiet on a page nothing routes to.
jsx = (ROOT / "frontend" / "src" / "components"
       / "TriggerLine.jsx").read_text(encoding="utf-8")
check("the page shows the lifetime figure separately",
      "since the agent started" in jsx, True)
# AND THIS TRIGGER'S FIGURE BESIDE IT, which is the number the whole
# commit was about: a lifetime count shown as this trigger's read
# "the detector nearly missed this one 70 times" when it meant "70
# people have crossed a zone since breakfast".
check("  and this trigger's own, which is the point",
      "restart(s)" in jsx, True)
check("  on the page that is actually routed",
      "<TriggerLine" in (ROOT / "frontend" / "src" / "pages"
                         / "AdminCameras.jsx").read_text(encoding="utf-8"),
      True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
