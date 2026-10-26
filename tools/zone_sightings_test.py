#!/usr/bin/env python3
"""Sightings remember WHICH tee, and old readers still work.

`_person_seen_at` used to be a list of floats: when somebody was seen
in any zone. The audio trigger needs more than that -- a crack can
only have come from the tee its player is standing on, and this camera
watches a back tee and a forward tee -- so it now holds (time, zone).

Two ways that breaks things, both tested here:

  * TWO LOOPS WRITE THE LIST. The decode engine and the copy engine
    both append to it and one reader drains it. A float in one and a
    tuple in the other is a crash that only shows up on whichever
    engine a given rig happens to run, which is the worst kind.
  * A RIG MID-UPGRADE can hold a list that started one shape and
    finished the other, so readers take both.

And the distinction that has already caused one bug here: None is "no
detector ran, do not gate", [] is "it ran and saw nobody, do gate".
The zone argument has the same trap -- None means "not asking about
geography", [] would name no zones and reject everything.

    python3 tools/zone_sightings_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent import tee_roi  # noqa: E402

FAILED = []
ROI = {"boxes": [
    {"x": 100, "y": 600, "w": 200, "h": 100, "label": "back"},
    {"x": 500, "y": 600, "w": 200, "h": 100, "label": "forward"},
], "frame": {"w": 1920, "h": 1080}}


def check(label, got, want):
    ok = got == want
    print("  %-54s %-14s %s" % (label, repr(got),
                               "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


print("which zone, by index:")
check("on the back tee", tee_roi.which((150, 650), ROI), 0)
check("on the forward tee", tee_roi.which((550, 650), ROI), 1)
check("on the path between them", tee_roi.which((400, 650), ROI), None)
check("contains() still agrees", tee_roi.contains((150, 650), ROI), True)
check("...and for a miss", tee_roi.contains((400, 650), ROI), False)
check("no zones configured", tee_roi.which((150, 650), None), None)

print()
print("seen_times accepts either shape:")
check("new (time, zone) pairs",
      tee_roi.seen_times([(10.0, 0), (11.0, 1)]), [10.0, 11.0])
check("old bare floats", tee_roi.seen_times([10.0, 11.0]), [10.0, 11.0])
check("a list that changed shape mid-run",
      tee_roi.seen_times([10.0, (11.0, 1)]), [10.0, 11.0])
check("empty", tee_roi.seen_times([]), [])
check("None", tee_roi.seen_times(None), [])

print()
print("seen_in_zones — the geography question:")
S = [(10.0, 0), (30.0, 1)]
check("back tee, 0.5s before the crack",
      tee_roi.seen_in_zones(S, [0], 10.5, 1.0), True)
check("...but the forward tee was not occupied then",
      tee_roi.seen_in_zones(S, [1], 10.5, 1.0), False)
check("forward tee at its own moment",
      tee_roi.seen_in_zones(S, [1], 30.2, 1.0), True)
check("nobody anywhere near 20s",
      tee_roi.seen_in_zones(S, [0, 1], 20.0, 1.0), False)
check("tolerance is honoured", tee_roi.seen_in_zones(S, [0], 12.0, 3.0), True)
check("...and bounded", tee_roi.seen_in_zones(S, [0], 12.0, 1.0), False)

print()
print("None is not []:")
check("None = not asking about geography",
      tee_roi.seen_in_zones(S, None, 10.5, 1.0), True)
check("[] names no zone and can never be true",
      tee_roi.seen_in_zones(S, [], 10.5, 1.0), False)
check("old bare floats have no zone, so a zone query is False",
      tee_roi.seen_in_zones([10.0], [0], 10.5, 1.0), False)
check("...but None still accepts them",
      tee_roi.seen_in_zones([10.0], None, 10.5, 1.0), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
