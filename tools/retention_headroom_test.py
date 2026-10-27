#!/usr/bin/env python3
"""Deleting by free space, when deleting by age is not enough.

AN AGE HORIZON CANNOT KEEP A DISK ALIVE. 20 GB of raw footage
accumulates here in about two weeks, so a 30-day horizon has nothing
old enough to delete while the disk fills underneath it -- and the disk
hit 100% twice in four days with the age sweeper running faithfully
every six hours and correctly finding no candidates.

This sweep is the brake. Everything worth testing about it is a REFUSAL,
because the thing it does cannot be undone: there is no object-storage
mirror behind the deployment, so a file it removes is gone.

    python3 tools/retention_headroom_test.py
"""
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services import retention  # noqa: E402

FAILED = []
GB = 1_000_000_000


def check(label, got, want):
    ok = got == want
    print("  %-56s %-18s %s" % (label, repr(got)[:18],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


KB = 1_000


def make_dir(tmp, files):
    """files: (name, size_kb, age_hours)"""
    d = Path(tmp)
    d.mkdir(parents=True, exist_ok=True)
    for name, mb, age_h in files:
        p = d / name
        p.write_bytes(b"\0" * int(mb * KB))
        t = time.time() - age_h * 3600
        import os
        os.utime(p, (t, t))
    return d


def sweep(d, *, free_gb, protected=("keep.mp4",), dry_run=True,
          min_free_gb=0.003, min_age_hours=12.0):
    """Run the sweep against a fake disk with `free_gb` free."""
    usage = mock.Mock(free=free_gb * GB, total=20 * GB,
                      used=(20 - free_gb) * GB)
    with mock.patch.object(retention, "protected_names",
                           lambda db: set(protected)), \
         mock.patch("shutil.disk_usage", return_value=usage):
        return retention.sweep_to_headroom(
            None, min_free_gb=min_free_gb, min_age_hours=min_age_hours,
            dry_run=dry_run, clips_dir=d)


import tempfile  # noqa: E402

TMP = Path(tempfile.mkdtemp())

# ---- the refusals ------------------------------------------------------
print("it refuses in every case where it cannot be sure:")
d = make_dir(TMP / "a", [("old.mp4", 500, 999)])
r = sweep(d, free_gb=0.009)
check("a disk already above the floor is left alone",
      "already at or above" in (r.get("skipped") or ""), True)
check("...and nothing is listed as deletable", r["candidates"], 0)

r = sweep(d, free_gb=0.0001, protected=())
check("AN EMPTY PROTECTED SET REFUSES THE WHOLE SWEEP",
      "protected set is empty" in (r.get("skipped") or ""), True)
check("...deleting nothing", r["candidates"], 0)

r = sweep(d, free_gb=0.0001, min_free_gb=0)
check("a zero floor is the off switch",
      "disabled" in (r.get("skipped") or ""), True)

r = sweep(Path(TMP / "nope"), free_gb=0.0001)
check("a missing directory is reported, not created",
      "no such directory" in (r.get("skipped") or ""), True)

# ---- what it will and will not touch -----------------------------------
print()
print("on a full disk it takes the oldest raw footage and nothing else:")
d = make_dir(TMP / "b", [
    ("oldest.mp4", 1200, 400),       # oldest raw -> first to go
    ("middle.mp4", 1200, 300),
    ("newest.mp4", 1200, 200),
    ("spare1.mp4", 1200, 150),       # eligible, but not needed
    ("spare2.mp4", 1200, 100),       # ...nor this one
    ("keep.mp4", 1200, 500),         # protected by the database
    ("fresh.mp4", 1200, 2),          # TOO YOUNG: may be uploading now
    ("shot_composite.mp4", 900, 999),   # a deliverable, never swept
    ("notes.txt", 100, 999),         # not video
])
r = sweep(d, free_gb=0.0005, dry_run=True)      # needs 2.5 MB
# A DRY RUN REPORTS `candidates`, exactly as the age sweep does, and
# leaves `deleted` at zero. Two dry runs printing 0 and 264 for the
# same kind of answer is how an operator deletes the wrong thing.
check("takes enough to clear the floor", r["candidates"], 3)
check("...and a dry run deleted nothing, and says so", r["deleted"], 0)
check("...AND NO MORE, with two eligible files left untouched",
      sorted(x["name"] for x in r["sample"]),
      ["middle.mp4", "newest.mp4", "oldest.mp4"])
check("...oldest first",
      [x["name"] for x in r["sample"]][:2], ["oldest.mp4", "middle.mp4"])
check("...and stops once there is room",
      round(r["bytes"] / KB), 3600)
check("the protected clip is counted, not taken", r["protected_hits"], 1)
check("footage from the last 12 hours is spared", r["too_young"], 1)
names = [x["name"] for x in r["sample"]]
check("a composite deliverable is never a candidate",
      "shot_composite.mp4" in names, False)
check("a non-video file is never a candidate", "notes.txt" in names, False)
check("the protected clip is never a candidate", "keep.mp4" in names, False)

# A DRY RUN MUST NOT DELETE. The report says "deleted" either way --
# that is the endpoint's contract -- so the only honest check is the disk.
check("...and a dry run left every file where it was",
      len(list(d.iterdir())), 9)

# ---- and it says so when it cannot get there ---------------------------
print()
print("when the disk is too small for the policy, it says so:")
d = make_dir(TMP / "c", [("old.mp4", 200, 400), ("keep.mp4", 5000, 500)])
r = sweep(d, free_gb=0.0001, dry_run=True)       # needs 2.9 MB, can free 0.2
# Rounded to three places, not two: at two, a real shortfall of a few
# megabytes prints as "0.00 GB short", which is the one thing this
# field exists not to say.
check("it reports how far short it fell", r["short_by_gb"], 0.003)
check("...having taken what it could", r["candidates"], 1)

# ---- the real thing ----------------------------------------------------
print()
print("a live run actually removes them:")
d = make_dir(TMP / "d", [("a.mp4", 1500, 400), ("b.mp4", 1500, 300),
                         ("keep.mp4", 100, 500)])
r = sweep(d, free_gb=0.0005, dry_run=False)
check("deleted", r["deleted"], 2)
check("the files are gone", sorted(p.name for p in d.iterdir()), ["keep.mp4"])

import shutil as _sh  # noqa: E402
_sh.rmtree(TMP, ignore_errors=True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
