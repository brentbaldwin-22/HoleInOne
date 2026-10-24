"""WHAT FILLS THE DISK, AND WHAT IS SAFE TO LOSE.

The clips directory had no retention at all until the 20 GB disk hit
100% on 2026-10-24 and every ffmpeg write began failing with "No space
left on device" -- which surfaces as a 500 on Produce and a vertical
that never renders, several layers away from the actual cause.

The shape of the problem, measured on that disk:

    plain mp4 (raw tee/green)   3068 files   20.04 GB
    thumbnails                  5935 files    0.77 GB
    composite                     90 files    0.07 GB
    vertical                      53 files    0.03 GB

Everything we actually ship is a rounding error. The disk is raw camera
footage, about 3% of which was ever produced into a clip; the rest is
false triggers and practice swings. So the rule is: deliverables live
for ever, raw footage lives for `clip_retention_days`.

DELETION HERE IS PERMANENT. storage.enabled() is False on Render -- the
mirror is Replit Object Storage and there is no bucket behind this
deployment -- so nothing can be rehydrated afterwards. That is why the
protected set is built from the database rather than guessed from
filenames, why `sweep_clips` defaults to a dry run, and why the sweeper
logs what it removed instead of doing it silently.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings

log = logging.getLogger("golfreelz.retention")

CLIPS_DIR = Path(__file__).resolve().parents[2] / settings.upload_dir / "clips"

# Produced artefacts, recognised by name so that an orphan survives even
# if its VideoClip row was deleted. All of them together are a tenth of
# a gigabyte, so keeping a stray one costs nothing and losing a real one
# costs a clip we can never rebuild.
_KEEP_MARKERS = ("_composite", "_vertical", "_clean")

# Only video is swept. Thumbnails average 0.13 MB and the gallery still
# shows them for clips whose raw footage is long gone.
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".ts", ".m4v", ".webm", ".avi"}

SWEEP_INTERVAL_SEC = 6 * 3600


def _basename(url: str | None) -> str | None:
    """Filename out of a stored URL. The columns hold a mix of absolute
    URLs, /uploads/clips/ paths and bare names depending on how old the
    row is, so normalise rather than assume."""
    if not url:
        return None
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or None


def protected_names(db: Session) -> set[str]:
    """Every filename the database still points at.

    Built from the DB on each sweep rather than cached: a clip produced
    two minutes ago must be protected on the sweep that follows it.
    """
    from ..models import LongVideoUpload, VideoClip

    names: set[str] = set()
    produced_uploads: set[int] = set()

    for src, tee, vert, tracer, thumb, lvu in db.query(
        VideoClip.source_url,
        VideoClip.tee_clip_url,
        VideoClip.vertical_url,
        VideoClip.tracer_url,
        VideoClip.thumbnail_url,
        VideoClip.long_upload_id,
    ):
        for url in (src, tee, vert, tracer, thumb):
            name = _basename(url)
            if name:
                names.add(name)
        if lvu is not None:
            produced_uploads.add(lvu)

    # The raw halves of an upload that actually produced a clip. This is
    # a safety margin ON TOP of the age rule, not part of it: it is the
    # difference between "re-produce this shot with new framing" and
    # "that footage is gone", and it costs on the order of a gigabyte.
    # Turn it off with clip_retention_keep_produced=0 if space gets
    # tight enough that even produced footage has to age out.
    if settings.clip_retention_keep_produced and produced_uploads:
        ids = list(produced_uploads)
        for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
            for tee_name, green_name in db.query(
                LongVideoUpload.tee_filename, LongVideoUpload.green_filename
            ).filter(LongVideoUpload.id.in_(chunk)):
                for name in (tee_name, green_name):
                    if name:
                        names.add(_basename(name) or name)

    return names


def _is_sweepable(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in _VIDEO_SUFFIXES:
        return False
    return not any(marker in path.name for marker in _KEEP_MARKERS)


def sweep_clips(
    db: Session,
    *,
    days: int | None = None,
    dry_run: bool = True,
    clips_dir: Path | None = None,
) -> dict:
    """Delete raw footage older than `days` that nothing points at.

    Returns a report whether or not it deleted anything, so the admin
    endpoint can show the operator exactly what a real run would remove
    before they authorise one.
    """
    horizon_days = settings.clip_retention_days if days is None else days
    directory = clips_dir or CLIPS_DIR

    report: dict = {
        "dry_run": dry_run,
        "days": horizon_days,
        "clips_dir": str(directory),
        "candidates": 0,
        "deleted": 0,
        "bytes": 0,
        "errors": 0,
        "protected_by_db": 0,
        "sample": [],
    }

    if horizon_days <= 0:
        report["skipped"] = "retention disabled (clip_retention_days=0)"
        return report
    if not directory.is_dir():
        report["skipped"] = f"no such directory: {directory}"
        return report

    keep = protected_names(db)
    report["protected_by_db"] = len(keep)
    cutoff = (datetime.utcnow() - timedelta(days=horizon_days)).timestamp()

    for path in directory.iterdir():
        try:
            if not _is_sweepable(path):
                continue
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            if path.name in keep:
                continue
        except OSError:
            # Raced with another writer, or vanished between iterdir and
            # stat. Nothing to do and nothing worth logging per-file.
            continue

        report["candidates"] += 1
        report["bytes"] += stat.st_size
        if len(report["sample"]) < 25:
            report["sample"].append(
                {
                    "name": path.name,
                    "mb": round(stat.st_size / 1e6, 2),
                    "modified": datetime.utcfromtimestamp(
                        stat.st_mtime
                    ).strftime("%Y-%m-%d"),
                }
            )

        if dry_run:
            continue
        try:
            path.unlink()
            report["deleted"] += 1
        except OSError as exc:
            report["errors"] += 1
            log.warning("retention: could not delete %s: %s", path.name, exc)

    report["gb"] = round(report["bytes"] / 1e9, 2)
    log.info(
        "retention: %s -- %d candidate(s), %.2f GB, %d deleted, %d error(s)",
        "dry run" if dry_run else "sweep",
        report["candidates"],
        report["gb"],
        report["deleted"],
        report["errors"],
    )
    return report


def start_retention_sweeper(interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
    """Run sweep_clips on a loop. Idempotent to start.

    No-op when clip_retention_days is 0 -- that is the off switch, and
    it should not leave a thread spinning. The first sweep waits one
    interval: startup is the worst moment to compete for disk IO with
    migrations, and nothing is so urgent it cannot wait six hours.
    """
    if not settings.clip_retention_days:
        log.info("retention: disabled (clip_retention_days=0)")
        return
    if getattr(start_retention_sweeper, "_started", False):
        return
    start_retention_sweeper._started = True  # type: ignore[attr-defined]

    def _loop() -> None:
        from ..database import SessionLocal

        log.info(
            "retention: sweeper started (%d day horizon, %.0fh interval)",
            settings.clip_retention_days, interval_sec / 3600,
        )
        while True:
            time.sleep(interval_sec)
            db = SessionLocal()
            try:
                sweep_clips(db, dry_run=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("retention: sweep failed: %s", exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

    threading.Thread(
        target=_loop, daemon=True, name="retention-sweeper"
    ).start()
