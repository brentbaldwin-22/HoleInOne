#!/usr/bin/env python3
"""A person the zones did not claim leaves a record.

"The golfer was not in a zone" and "the detector never saw him"
produced byte-identical evidence: the capture loop recorded a sighting
when the feet were inside a zone, and NOTHING at all when they were
not -- no log, no sighting, nothing on the event. So the first question
asked about every missed swing could not be answered, and the tempting
response (make the zone bigger) is exactly wrong when the cause is the
second one: it makes the trigger worse at the only job a zone has.

`nearest_miss` measures how far out and WHICH WAY, so a zone that wants
moving says so in pixels instead of being guessed at.

    python3 tools/near_miss_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import tee_roi  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# Two tee strips, as an operator draws them: ground rectangles.
ROI = {"boxes": [
    {"x": 400, "y": 540, "w": 260, "h": 30, "label": "Zone 3"},
    {"x": 1190, "y": 560, "w": 230, "h": 30, "label": "Middle"},
], "frame_w": 1920, "frame_h": 1080}


print("\n-- a point inside a zone is not a miss")
check("dead centre of Zone 3", tee_roi.nearest_miss((530, 555), ROI), None)
check("  on the boundary", tee_roi.nearest_miss((400, 540), ROI), None)
# CONTROL: the same point really is inside, by the function that decides.
check("control, contains() agrees", tee_roi.contains((530, 555), ROI), True)


print("\n-- a golfer standing just in front of the zone")
# 40px BELOW the box (y grows downward) and within its x span.
miss = tee_roi.nearest_miss((530, 610), ROI)
check("it is a miss", miss is not None, True)
check("  against Zone 3 (index 0)", miss[0], 0)
check("  no sideways error", miss[1], 0.0)
check("  40px below, sign positive", miss[2], 40.0)
check("  distance 40px", miss[3], 40.0)

print("\n-- ...and just behind it")
miss = tee_roi.nearest_miss((530, 520), ROI)
check("20px above, sign negative", miss[2], -20.0)

print("\n-- sideways is signed the way the point lies")
left = tee_roi.nearest_miss((360, 555), ROI)
check("left of Zone 3 is negative", left[1], -40.0)
right = tee_roi.nearest_miss((700, 555), ROI)
check("right of Zone 3 is positive", right[1], 40.0)


print("\n-- it picks the CLOSEST zone, not the first drawn")
# Nearer Middle than Zone 3, so it must not report Zone 3 just because
# that box comes first in the list.
miss = tee_roi.nearest_miss((1150, 575), ROI)
check("reports Middle (index 1)", miss[0], 1)
check("  40px to its left", miss[1], -40.0)


print("\n-- it never raises on the inputs a Pi actually sees")
check("no zones at all", tee_roi.nearest_miss((500, 500), {}), None)
check("empty box list", tee_roi.nearest_miss((500, 500), {"boxes": []}), None)
check("a junk point", tee_roi.nearest_miss(None, ROI), None)
check("a short point", tee_roi.nearest_miss((5,), ROI), None)
check("a non-numeric point", tee_roi.nearest_miss(("a", "b"), ROI), None)


print("\n-- the capture loop actually calls it")
src = (ROOT / "pi-agent" / "agent" / "tee.py").read_text(encoding="utf-8")
check("there is an else-branch for a person in no zone",
      "self._note_near_miss(now, stance, _roi_now)" in src, True)
check("  it is rate-limited", "_NEAR_MISS_EVERY" in src, True)
check("  and it summarises the CLOSEST approach",
      'st["best"] is None or px < st["best"][3]' in src, True)
# The two copies of tee_roi must not drift; tee_roi_sync_test owns that
# rule, this just refuses to pass while the backend lacks the function.
be = (ROOT / "backend" / "app" / "services"
      / "tee_roi.py").read_text(encoding="utf-8")
check("the backend copy has it too", "def nearest_miss" in be, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
