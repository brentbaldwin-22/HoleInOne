#!/usr/bin/env python3
"""Put the production flow back to raw clips.

Deletes what the FLOW made -- its audio cuts and the drafts produced
through it -- and nothing else. The recordings themselves are never
touched: the point is to get back to a stepper reading
"532 recordings / 0 cuts / 0 clips / 0 clips" and start again.

WHAT IT CANNOT REACH, BY CONSTRUCTION. There are about a thousand
clips in this database from every pipeline the console has ever had,
and some of them are in player galleries or have been shared. A clip
produced through the flow carries `flow_run_at`; nothing else does.
That is the same predicate the page itself filters on (`flow_only` in
`flow_clips`), so what this deletes is exactly what the page counts,
and a legacy clip cannot be caught by it even by accident.

THE CONSOLE'S OWN DELETE, not a second answer to "what does deleting
a clip mean". Four real foreign keys point at video_clips --
camera_events.produced_clip_id is stamped on every clip the moment it
is produced -- and a hand-rolled delete here would be the one that is
missing a table. `delete_flow_cut` is likewise reused for the cuts so
the files on disk go with the rows.

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

from app.database import SessionLocal                      # noqa: E402
from app.models import AudioCut, VideoClip                 # noqa: E402
from app.routers.admin import delete_clip                  # noqa: E402
from app.routers.production_flow import delete_flow_cut    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it nothing changes")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # ── IDS, NOT ROWS ───────────────────────────────────────
        #
        # Both deletes commit, and a commit expires every object in
        # the session. Holding ORM rows across the loop means each
        # `k.id` on the next pass is a fresh SELECT for a row that may
        # by then be gone -- an ObjectDeletedError in the middle of a
        # half-finished delete, against production data. A list of
        # integers cannot go stale.
        clip_ids = [r[0] for r in db.query(VideoClip.id)
                    .filter(VideoClip.flow_run_at.isnot(None)).all()]
        cut_ids = [r[0] for r in db.query(AudioCut.id).all()]
        legacy = (db.query(VideoClip)
                  .filter(VideoClip.flow_run_at.is_(None)).count())

        print("the flow made:")
        print("  %4d audio cut(s)" % len(cut_ids))
        print("  %4d clip(s) produced through the flow" % len(clip_ids))
        print("untouched:")
        print("  %4d clip(s) from before the flow" % legacy)
        print("  every recording -- no source video is deleted")

        if not args.yes:
            print("\nnothing was changed. re-run with --yes to delete.")
            return 0

        n_clips = n_cuts = 0
        for cid in clip_ids:
            try:
                # keep_swing: the swing still happened. The cut row is
                # about to go anyway, but failing halfway through must
                # not leave the recording's edit list mangled.
                delete_clip(cid, keep_swing=True, db=db)
                n_clips += 1
            except Exception as exc:                      # noqa: BLE001
                db.rollback()
                print("  ! clip %s: %s" % (cid, exc))
        for kid in cut_ids:
            try:
                delete_flow_cut(kid, db=db)
                n_cuts += 1
            except Exception as exc:                      # noqa: BLE001
                db.rollback()
                print("  ! cut %s: %s" % (kid, exc))

        left_clips = (db.query(VideoClip)
                      .filter(VideoClip.flow_run_at.isnot(None)).count())
        left_cuts = db.query(AudioCut).count()
        left_legacy = (db.query(VideoClip)
                       .filter(VideoClip.flow_run_at.is_(None)).count())
        print("\ndeleted %d clip(s) and %d cut(s)." % (n_clips, n_cuts))
        print("still there: %d flow clip(s), %d cut(s), %d legacy clip(s)"
              % (left_clips, left_cuts, left_legacy))
        if left_legacy != legacy:
            # Loud, because it would mean the predicate above is not
            # the fence this script claims it is.
            print("WARNING: the legacy clip count CHANGED (%d -> %d). "
                  "That should be impossible -- stop and look."
                  % (legacy, left_legacy))
            return 2
        return 0 if (left_clips == 0 and left_cuts == 0) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
