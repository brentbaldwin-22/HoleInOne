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
# if its VideoClip row was deleted. Keeping a stray one costs nothing;
# losing a real one costs a clip we cannot rebuild.
#
# THIS IS A BACKSTOP, NOT THE PROTECTION. A single-camera produce names
# its deliverable after the segment with no marker at all -- see the
# source_url assignment in routers/admin.py, which serves that exact
# file -- so plenty of produced clips look identical to raw footage from
# the filename alone. Only the database knows. That is why an empty
# protected set aborts the sweep below rather than falling back to age.
_KEEP_MARKERS = ("_composite", "_vertical", "_clean", "_ai_tracer", "_traced")

# Only video is swept. Thumbnails average 0.13 MB and the gallery still
# shows them for clips whose raw footage is long gone.
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".ts", ".m4v", ".webm", ".avi"}

SWEEP_INTERVAL_SEC = 6 * 3600

# DERIVED IMAGES, WHICH NOTHING SWEPT AT ALL.
#
# "Only video is swept. Thumbnails average 0.13 MB" was true when the
# only images here were thumbnails. It is not any more: every frame the
# wizard pages to, every Test Cut composite and every composite the rig
# sends to the swing judge lands in this directory as a full-frame jpeg
# and stays for ever. They arrive at the rate swings do, and the rate
# of swings went UP sharply when the audio trigger started cutting.
#
# The disk hit 100% on 2026-10-24 and 2026-10-27 with the age sweeper
# running and correctly finding no video old enough; it hit it again on
# 2026-10-29, and the first thing anybody saw was a skyline modal
# answering "could not write detect-1251-frame-0.jpg".
#
# BY EXPLICIT PREFIX, never by suffix. A thumbnail and a gallery still
# are also jpegs in this directory, the gallery shows them for clips
# whose footage is long gone, and nothing regenerates them. Everything
# named below is DERIVED: it is rebuilt on demand from a clip that is
# still here, so the worst a delete costs is the second it takes to
# make again.
_DERIVED_IMAGE_PREFIXES = (
    "detect-",      # frames the wizard and the skyline modal page to
    "verify-",      # Test Cut's swing-judge composites
    "righeat-",     # the composites the rig's own judge was shown
    "heatchk",      # swing_heat_check's evidence images
    "d3prod-",      # produce's working files
)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
# Hours. Long enough that a report somebody is reading still has its
# pictures, short enough that a day of golf cannot fill the disk.
IMAGE_RETENTION_HOURS = 48


def _is_sweepable_image(path: Path) -> bool:
    """A derived image old enough to be worth nothing to anybody."""
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        return False
    if any(marker in path.name for marker in _KEEP_MARKERS):
        return False
    return any(path.name.startswith(p) for p in _DERIVED_IMAGE_PREFIXES)


def sweep_derived_images(
    *,
    hours: float | None = None,
    dry_run: bool = True,
    clips_dir: Path | None = None,
) -> dict:
    """Delete derived images older than `hours`. Never raises.

    No database at all, unlike its siblings: nothing in the schema
    points at one of these for longer than the page that made it is
    open, and the two that ARE linked from a record -- the rig's judge
    composite and Test Cut's -- are evidence rather than content. A
    card that loses its thumbnail after two days says the picture has
    aged out; a disk at 100% stops the rig recording.
    """
    import time as _time

    horizon = IMAGE_RETENTION_HOURS if hours is None else float(hours)
    directory = clips_dir or CLIPS_DIR
    report: dict = {
        "dry_run": dry_run, "hours": horizon,
        "clips_dir": str(directory),
        "candidates": 0, "deleted": 0, "bytes": 0, "errors": 0,
        "sample": [],
    }
    if horizon <= 0:
        report["skipped"] = "image sweep disabled (hours=0)"
        return report
    if not directory.is_dir():
        report["skipped"] = f"no such directory: {directory}"
        return report

    cutoff = _time.time() - horizon * 3600.0
    for path in directory.iterdir():
        if not _is_sweepable_image(path):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime > cutoff:
            continue
        report["candidates"] += 1
        report["bytes"] += st.st_size
        if len(report["sample"]) < 10:
            report["sample"].append(path.name)
        if dry_run:
            continue
        try:
            path.unlink()
            report["deleted"] += 1
        except OSError as exc:
            report["errors"] += 1
            log.warning("retention: could not delete %s: %s", path.name, exc)
    log.info(
        "retention: derived images older than %.0fh -- %d candidate(s), "
        "%.2f GB, %d deleted, %d error(s)%s",
        horizon, report["candidates"], report["bytes"] / 1e9,
        report["deleted"], report["errors"],
        " (dry run)" if dry_run else "",
    )
    return report


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
        "protected_hits": 0,
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

    # REFUSE TO SWEEP ON AN EMPTY PROTECTED SET. Produced clips are
    # indistinguishable from raw footage by filename, so if the query
    # came back empty -- a dead connection, a migration mid-flight, an
    # empty database on a fresh environment -- then deleting by age
    # alone would delete the product. An empty set means "I could not
    # find out", not "nothing is worth keeping".
    if not keep:
        report["skipped"] = (
            "protected set is empty; refusing to sweep by age alone"
        )
        log.warning("retention: %s", report["skipped"])
        return report

    cutoff = (datetime.utcnow() - timedelta(days=horizon_days)).timestamp()

    for path in directory.iterdir():
        try:
            if not _is_sweepable(path):
                continue
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            if path.name in keep:
                report["protected_hits"] += 1
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


def sweep_to_headroom(
    db: Session,
    *,
    min_free_gb: float | None = None,
    min_age_hours: float | None = None,
    dry_run: bool = True,
    clips_dir: Path | None = None,
) -> dict:
    """Delete the OLDEST unprotected raw footage until the disk has room.

    AN AGE HORIZON CANNOT KEEP A DISK ALIVE. Measured here: 20 GB of raw
    footage accumulates in about two weeks, so a 30-day horizon has
    nothing old enough to delete while the disk fills underneath it.
    The disk hit 100% on 2026-10-24 and again on 2026-10-27, both times
    with the age sweeper running every six hours and correctly finding
    no candidates. Age says what is worth keeping; only free space says
    what is affordable.

    So this runs after the age sweep and only when the disk is already
    under the floor. It takes the oldest first, which is the same
    preference the horizon expresses, and stops the moment there is
    room -- it is a brake, not a policy.

    THE SAME TWO REFUSALS AS THE AGE SWEEP, for the same reasons: an
    empty protected set means "I could not find out", not "nothing is
    worth keeping", and a dry run is the default because nothing can be
    rehydrated afterwards. Plus one more of its own: footage younger
    than `min_age_hours` is never touched. A disk at 100% is precisely
    when clips are being written, and an emergency sweep that deletes
    the upload currently landing turns an outage into a lost round.
    """
    import shutil

    floor_gb = (settings.clip_retention_min_free_gb
                if min_free_gb is None else min_free_gb)
    min_age = (settings.clip_retention_min_age_hours
               if min_age_hours is None else min_age_hours)
    directory = clips_dir or CLIPS_DIR

    report: dict = {
        "dry_run": dry_run,
        "min_free_gb": floor_gb,
        "min_age_hours": min_age,
        "clips_dir": str(directory),
        "candidates": 0,
        "deleted": 0,
        "bytes": 0,
        "errors": 0,
        "too_young": 0,
        "protected_hits": 0,
        "sample": [],
    }
    if floor_gb <= 0:
        report["skipped"] = "headroom sweep disabled (min_free_gb=0)"
        return report
    if not directory.is_dir():
        report["skipped"] = f"no such directory: {directory}"
        return report

    try:
        free_gb = shutil.disk_usage(directory).free / 1e9
    except OSError as exc:
        report["skipped"] = f"could not read disk usage: {exc}"
        return report
    report["free_gb_before"] = round(free_gb, 2)
    if free_gb >= floor_gb:
        report["skipped"] = (
            f"{free_gb:.2f} GB free is already at or above the "
            f"{floor_gb:.2f} GB floor"
        )
        return report

    keep = protected_names(db)
    report["protected_by_db"] = len(keep)
    if not keep:
        report["skipped"] = (
            "protected set is empty; refusing to sweep by size alone"
        )
        log.warning("retention: %s", report["skipped"])
        return report

    young_cutoff = time.time() - min_age * 3600.0
    candidates = []
    for path in directory.iterdir():
        try:
            if not _is_sweepable(path):
                continue
            if path.name in keep:
                report["protected_hits"] += 1
                continue
            stat = path.stat()
            if stat.st_mtime >= young_cutoff:
                report["too_young"] += 1
                continue
        except OSError:
            continue
        candidates.append((stat.st_mtime, stat.st_size, path))

    candidates.sort(key=lambda c: c[0])      # oldest first
    need_bytes = (floor_gb - free_gb) * 1e9
    freed = 0
    for mtime, size, path in candidates:
        if freed >= need_bytes:
            break
        if len(report["sample"]) < 25:
            report["sample"].append({
                "name": path.name,
                "mb": round(size / 1e6, 2),
                "modified": datetime.utcfromtimestamp(mtime).strftime(
                    "%Y-%m-%d"),
            })
        freed += size
        report["bytes"] += size
        report["candidates"] += 1
        if dry_run:
            continue
        try:
            path.unlink()
            report["deleted"] += 1
        except OSError as exc:
            report["errors"] += 1
            report["bytes"] -= size
            freed -= size
            log.warning("retention: could not delete %s: %s", path.name, exc)

    report["gb"] = round(report["bytes"] / 1e9, 2)
    report["short_by_gb"] = round(max(0.0, (need_bytes - freed) / 1e9), 3)
    if report["short_by_gb"] > 0:
        # SAY SO RATHER THAN LOOK SUCCESSFUL. Everything left is either
        # protected, too young, or a deliverable -- which means the
        # disk is too small for the retention policy, and that is a
        # decision for a person.
        log.warning(
            "retention: headroom sweep could not reach the %.2f GB floor "
            "-- %.2f GB short after %d deletion(s). What remains is "
            "protected, too young, or not raw footage.",
            floor_gb, report["short_by_gb"], report["deleted"])
    log.info(
        "retention: headroom %s -- %.2f GB from %d file(s), %d error(s)",
        "dry run" if dry_run else "sweep",
        report["gb"], report["deleted"], report["errors"])
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
            "retention: sweeper started (%d day horizon, %.1f GB floor, "
            "%.0fh interval)",
            settings.clip_retention_days,
            settings.clip_retention_min_free_gb, interval_sec / 3600,
        )
        while True:
            time.sleep(interval_sec)
            db = SessionLocal()
            try:
                # DERIVED IMAGES FIRST, because they are the only
                # thing here that costs nothing to lose -- every one is
                # rebuilt on demand from a clip that is still present.
                # Taking them before any footage means the two sweeps
                # below usually find the disk already fine, and a round
                # of golf is never deleted to make room for a cache.
                sweep_derived_images(dry_run=False)
                sweep_clips(db, dry_run=False)
                # AGE FIRST, THEN SIZE. The horizon expresses what is
                # worth keeping; the floor expresses what is
                # affordable. Running the horizon first means the
                # headroom sweep usually finds the disk already fine
                # and does nothing, which is the intended shape: a
                # brake, not a policy.
                sweep_to_headroom(db, dry_run=False)
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
