#!/usr/bin/env python3
"""Put the production flow back to raw clips, from a shell.

THE BUTTON IS THE USUAL WAY. "Start over" on the Production Flow page
does this, armed, with the counts in front of it. This exists for the
times the page cannot be reached -- a broken frontend build, a
half-finished deploy -- and for scripting.

Both call the same `reset_flow`, so they cannot drift. What it
deletes, what it refuses to touch and why is documented there.

IT LIVES UNDER backend/ BECAUSE THAT IS WHAT SHIPS. The Dockerfile
copies `backend/` and `pi-agent/` and nothing else, so a tool in the
repo root exists on the laptop and not in the image -- which shows up
as "No such file or directory" in the Render Shell, at the moment
somebody is trying to run it.

Usage, from the Render Shell (which starts in /app/backend):

    python tools/flow_reset.py            # counts only, changes nothing
    python tools/flow_reset.py --yes      # actually deletes
"""
import argparse
import sys
from pathlib import Path

# backend/ -- the import root for `app.*`. Already the working
# directory in the image; spelled out so the script also runs from
# anywhere else, including the repo root on a laptop.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal                           # noqa: E402
from app.models import AudioCut, VideoClip                      # noqa: E402
from app.routers.production_flow import reset_flow              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it nothing changes")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        n_cuts = db.query(AudioCut).count()
        n_clips = (db.query(VideoClip)
                   .filter(VideoClip.flow_run_at.isnot(None)).count())
        n_legacy = (db.query(VideoClip)
                    .filter(VideoClip.flow_run_at.is_(None)).count())

        print("the flow made:")
        print("  %4d audio cut(s)" % n_cuts)
        print("  %4d clip(s) produced through the flow" % n_clips)
        print("untouched:")
        print("  %4d clip(s) from before the flow" % n_legacy)
        print("  every recording -- no source video is deleted")

        if not args.yes:
            print("\nnothing was changed. re-run with --yes to delete.")
            return 0

        out = reset_flow(db)
        print("\ndeleted %d clip(s) and %d cut(s)%s."
              % (out["clips"], out["cuts"],
                 ", %d failed" % out["failed"] if out["failed"] else ""))

        left_clips = (db.query(VideoClip)
                      .filter(VideoClip.flow_run_at.isnot(None)).count())
        left_cuts = db.query(AudioCut).count()
        print("still there: %d flow clip(s), %d cut(s), %d legacy clip(s)"
              % (left_clips, left_cuts, out["legacy_after"]))
        if out["legacy_after"] != out["legacy_before"]:
            # Loud, because it would mean `flow_run_at` is not the
            # fence reset_flow is built on.
            print("WARNING: the legacy clip count CHANGED (%d -> %d). "
                  "That should be impossible -- stop and look."
                  % (out["legacy_before"], out["legacy_after"]))
            return 2
        return 0 if (left_clips == 0 and left_cuts == 0) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
