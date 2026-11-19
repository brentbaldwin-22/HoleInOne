#!/usr/bin/env python3
"""The trigger-zone editor draws on a still, at full brightness.

TWO THINGS MADE THE PICTURE HARD TO AIM AT.

It required the LIVE frame. The reasoning was sound -- "is the back
tee inside this box RIGHT NOW", after a zoom nudge, with today's
markers -- but it made the editor depend on the Pi streaming at that
moment. No live frame, no picture: the panel sat on "Waiting for live
frame from Pi..." with nothing to draw against at all.

And it laid 45% black over everything a zone did not cover, so that
ignored ground read as ignored. A tee at dusk, under a tree, or on the
far side of the hole is already the darkest part of the frame, and the
markers and turf edges the operator is aiming at were exactly what
went under the wash.

    python3 tools/zone_editor_still_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ = (ROOT / "frontend" / "src" / "components"
      / "TriggerZones.jsx").read_text()
OLD = (ROOT / "frontend" / "src" / "pages"
       / "AdminCamerasOld.jsx").read_text()
NEW = (ROOT / "frontend" / "src" / "pages"
       / "AdminCameras.jsx").read_text()

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("the whole picture is at full brightness:")
check("no wash is drawn over the unselected area",
      "rgba(0,0,0,0.45)" in TZ, False)
check("  and the mask that cut it is gone too",
      "mask={`url(#zm-" in TZ, False)
# THE ZONES STILL HAVE TO READ AS ZONES. Removing the wash must not
# take the only thing that says which ground is covered.
check("the zones keep their own colour and outline",
      "COLOURS[i % COLOURS.length]" in TZ, True)
check("  and their labels", "<text" in TZ, True)

print()
print("the editors draw on the last still, not the stream:")
check("the old page uses the camera still while editing",
      "api.cameraStillUrl(cam.id)}?t=${stillBust}" in OLD, True)
check("  for the zone editor and the outline editor alike",
      "(zoningCamId === cam.id || outliningCamId === cam.id)" in OLD, True)
check("  with a cache-bust, so a fresh capture is not a stale copy",
      "setStillBust(Date.now())" in OLD, True)
# AND THE LIVE PATH IS NOT BROKEN -- watching a camera still streams.
check("the live frame still renders when not editing",
      "&& liveFrameSrc && (" in OLD, True)
check("  and 'waiting for live frame' no longer blocks the editor",
      "&& !liveFrameSrc && (" in OLD, True)
# The newer page was already on a still; this must not have regressed it.
check("the newer cameras page was already on the still",
      "api.cameraStillUrl(cam.id)" in NEW, True)

print()
print("and the reasoning is written down where it changed:")
check("the component says why the still, and what it gives up",
      "DRAWN ON THE LAST STILL" in TZ and "latest lens move" in TZ, True)
check("  and why nothing is dimmed",
      "NOTHING OUTSIDE THE ZONES IS DIMMED" in TZ, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — a still, at full brightness, with the zones marked on it")
