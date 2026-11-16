#!/usr/bin/env python3
"""A golfer behind a zone is playing from it; one in front is not.

The zones are drawn on the GROUND and the trigger tested the FEET,
which is what makes a zone mean "this patch of turf". But a golfer
addressing a ball stands a pace behind it, and on a camera looking
down the hole "behind" is LOWER in the picture -- so a zone drawn
accurately on the tee surface can miss the player using it.

Counting the whole body fixes that in ONE DIRECTION ONLY, and the
asymmetry is not a rule anybody had to write: people extend UP from
their feet. Somebody in front of a zone (nearer the hole, higher in
frame) has their feet above its top edge, so no part of them can
overlap it. Somebody behind it reaches up into it.

    python3 tools/body_in_zone_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import tee_roi  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-60s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# One tee strip drawn on the ground, as an operator draws it.
ROI = {"boxes": [{"x": 400, "y": 540, "w": 300, "h": 30, "label": "Back"}],
       "frame_w": 1920, "frame_h": 1080}
TALL = 90.0          # a person's on-screen height at this distance


def inz(x, y, h=TALL, frac=1.0):
    return tee_roi.contains_body((x, y), ROI, h, frac)


print("\n-- feet in the zone, as before")
check("standing on it", inz(550, 555), True)
check("  control, the point test agrees",
      tee_roi.contains((550, 555), ROI), True)


print("\n-- BEHIND it (lower in frame, nearer the camera) now counts")
check("a pace behind", inz(550, 600), True)
check("  further back, still within a body height", inz(550, 650), True)
check("  but not a long way back", inz(550, 700), False)
# CONTROL: none of these would have counted before.
check("control, the feet-only test caught none of them",
      [tee_roi.contains((550, y), ROI) for y in (600, 650, 700)],
      [False, False, False])


print("\n-- IN FRONT of it (higher in frame, toward the hole) never does")
check("just in front", inz(550, 535), False)
check("  well in front", inz(550, 430), False)
check("  right at the top edge is still on it", inz(550, 540), True)
# THE WHOLE SAFETY ARGUMENT, stated as a test: being in front cannot
# be rescued by being tall, because a tall person in front is tall
# UPWARD, away from the zone.
check("a very tall person in front is still out", inz(550, 500, h=600.0),
      False)


print("\n-- sideways is unchanged: zones are not widened")
check("beside the zone, level with it", inz(300, 555), False)
check("  and beside it from behind", inz(300, 600), False)


print("\n-- the fraction bounds how far back it reaches")
check("frac 0 is the old test exactly", inz(550, 600, frac=0.0), False)
check("  and still catches feet inside", inz(550, 555, frac=0.0), True)
check("frac 0.5 reaches half as far", inz(550, 620, frac=0.5), False)
check("  while frac 1.0 reaches it", inz(550, 620, frac=1.0), True)


print("\n-- the zone it is FILED under is the one that let it through")
# Otherwise a trigger shows up attributed to no zone at all.
check("a golfer behind the zone is attributed to it",
      tee_roi.lowest_at_body((550, 600), ROI, TALL, 1.0), 0)
check("  while the point version loses them",
      tee_roi.lowest_at((550, 600), ROI), None)
check("  and someone in front is attributed to nothing",
      tee_roi.lowest_at_body((550, 430), ROI, TALL, 1.0), None)


print("\n-- it never raises on what a detector really returns")
for name, args in (("no zones", ((550, 600), {}, TALL, 1.0)),
                   ("junk point", ((None, None), ROI, TALL, 1.0)),
                   ("no height", ((550, 600), ROI, 0.0, 1.0)),
                   ("negative height", ((550, 600), ROI, -5.0, 1.0))):
    tee_roi.contains_body(*args)
    tee_roi.lowest_at_body(*args)
    print("  %-60s ok" % ("%s answers without raising" % name))

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
