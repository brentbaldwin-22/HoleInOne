#!/usr/bin/env python3
"""The category nothing swept, and the one it must never touch.

`sweep_clips` only ever looked at VIDEO. Every frame the wizard pages
to, every Test Cut composite and every composite the rig's swing judge
was shown is a full-frame jpeg that lands in the clips directory and
stays there for ever -- arriving at the rate swings do, which went up
sharply the day the audio trigger started cutting.

So the disk filled with pictures, and neither the age sweep nor the
headroom sweep could rescue it: both correctly found no video old
enough. The first anybody saw of it was a skyline modal answering
"could not write detect-1251-frame-0.jpg to the clips directory".

BY PREFIX, NEVER BY SUFFIX, and that is the whole safety argument. A
thumbnail and a gallery still are also jpegs in that directory, the
gallery shows them for clips whose footage is long gone, and nothing
regenerates them. Everything swept here is DERIVED and rebuilt on
demand from a clip that is still present.

    python3 tools/image_sweep_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.retention import (  # noqa: E402
    IMAGE_RETENTION_HOURS,
    sweep_derived_images,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


OLD = time.time() - (IMAGE_RETENTION_HOURS + 6) * 3600
NEW = time.time() - 600


def put(d, name, when):
    p = d / name
    p.write_bytes(b"x" * 1000)
    os.utime(p, (when, when))
    return p


with tempfile.TemporaryDirectory() as _td:
    D = Path(_td)
    # Derived, old: all of these go.
    for n in ("detect-1251-frame-0.jpg", "detect-9-green-frame-120.jpg",
              "verify-1203-00-6a110b-clean.jpg", "righeat-7-abc123.jpg",
              "heatchk-evidence.png", "d3prod-55-aa-0-tee.jpg"):
        put(D, n, OLD)
    # Derived, but still warm: somebody may be looking at the report.
    put(D, "verify-1300-00-ffffff-clean.jpg", NEW)
    # NOT derived, however old: the gallery still shows these.
    for n in ("thumb-884.jpg", "clip-884-poster.png",
              "shot-of-the-week.jpg"):
        put(D, n, OLD)
    # Video is the other sweep's business, not this one's.
    put(D, "camera-event-1557-tee.mp4", OLD)
    # A keep-marker name that happens to be an image.
    put(D, "detect-12-frame-3_clean.jpg", OLD)

    print("a dry run reports and removes nothing:")
    r = sweep_derived_images(clips_dir=D, dry_run=True)
    check("six old derived images found", r["candidates"], 6)
    check("...and none deleted", r["deleted"], 0)
    check("...all still on disk", len(list(D.iterdir())), 12)

    print()
    print("for real:")
    r = sweep_derived_images(clips_dir=D, dry_run=False)
    check("six deleted", r["deleted"], 6)
    check("...and it says how much it freed", r["bytes"], 6000)
    left = sorted(p.name for p in D.iterdir())

    print()
    print("what it must never take:")
    check("the thumbnail stays", "thumb-884.jpg" in left, True)
    check("the poster stays", "clip-884-poster.png" in left, True)
    check("the shot of the week stays", "shot-of-the-week.jpg" in left, True)
    check("raw video is not its business",
          "camera-event-1557-tee.mp4" in left, True)
    check("a _clean keep-marker is honoured",
          "detect-12-frame-3_clean.jpg" in left, True)
    check("a warm composite stays",
          "verify-1300-00-ffffff-clean.jpg" in left, True)
    check("...and nothing else is left", len(left), 6)

    print()
    print("the off switch, and a directory that is not there:")
    check("hours=0 is disabled",
          "skipped" in sweep_derived_images(clips_dir=D, hours=0), True)
    check("a missing directory is reported, not raised",
          "skipped" in sweep_derived_images(
              clips_dir=D / "nope", dry_run=False), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
