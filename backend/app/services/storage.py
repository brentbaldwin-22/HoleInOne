"""Persistent object storage for uploaded + produced clips.

Replit deployments have an EPHEMERAL filesystem — every redeploy wipes
`uploads/clips/`, so raw footage and produced videos vanish. This module
mirrors those files into **Replit Object Storage** (a GCS-backed bucket
that survives deploys), and rehydrates them back onto local disk on
demand, so a republish no longer deletes anything.

Design goals:
  * FAIL-SAFE. If the object-storage client can't initialise (bucket not
    added to the repl, package missing, running locally in dev/CI), every
    function becomes a no-op and the app behaves exactly as before —
    local disk only. Enabling persistence is purely additive.
  * NO write-path surgery. A background sweeper uploads any new local clip
    to the bucket, so we don't have to touch every place that writes a
    file. Serving + processing pull a file back from the bucket on demand
    when it's missing locally (post-redeploy).

The object key is normally just the filename (clips already have
unique, UUID/session-derived names). The ONE exception is a folder
listed in `MIRRORED_SUBDIRS`: those keep their folder in the key, so
`_flowcuts/flowcut-1406-00-tee.mp4` goes to the bucket under that
whole path and comes back to that same path on disk. See the comment
on `MIRRORED_SUBDIRS`.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("golfreelz.storage")

_client = None
_enabled = False

try:  # pragma: no cover - depends on the deploy environment
    from replit.object_storage import Client  # type: ignore

    _client = Client()
    # Probe: the package can import + Client() can construct even when NO
    # bucket is configured for the repl (e.g. dev). Without this check we'd
    # think storage is "on" and then fail — and log — on every single file,
    # flooding the console. A cheap exists() call raises "no default bucket"
    # when unconfigured, so we disable cleanly and stay local-disk only.
    _client.exists("__golfreelz_probe__")
    _enabled = True
    log.info("storage: Replit Object Storage enabled")
except Exception as exc:  # noqa: BLE001
    _client = None
    log.info(
        "storage: object storage unavailable (%s) — running local-disk only",
        exc,
    )


# ── FOLDERS UNDER THE CLIPS DIR THAT ARE MIRRORED TOO ───────────────
#
# The sweeper used to walk `clips_dir.glob("*")`, which is the TOP
# LEVEL ONLY -- so anything in a subfolder was never uploaded, and on
# this host the disk is wiped by every deploy. The flow's audio cuts
# live in `_flowcuts/` and so they died on every deploy: the AudioCut
# row survived, named a file that was no longer there, and the page
# said "the file is not on disk any more" over a swing that had been
# cut the day before.
#
# A LIST, NOT "EVERY SUBFOLDER", because not every subfolder wants
# keeping. `_testcuts/` is scratch that the next Test Cut wipes, and
# mirroring it would push a few megabytes per press into a bucket
# nothing ever reads it back from.
#
# The key keeps the folder, so two folders can hold the same filename
# and the file comes back where it was written.
MIRRORED_SUBDIRS = ("_flowcuts",)


def key_for(name: str) -> str:
    """The bucket key for a name relative to the clips dir.

    `"a.mp4"` -> `"a.mp4"`; `"_flowcuts/a.mp4"` -> `"_flowcuts/a.mp4"`;
    anything else keeps only its filename, which is what every caller
    outside the mirrored folders has always passed.
    """
    parts = Path(str(name or "")).parts
    if len(parts) >= 2 and parts[-2] in MIRRORED_SUBDIRS:
        return "%s/%s" % (parts[-2], parts[-1])
    return Path(str(name or "")).name


def _is_not_found(exc: Exception) -> bool:
    """True when an exception means 'object not in bucket' rather than a real
    failure. Tolerant across replit-object-storage versions: match the error
    class name or message text."""
    if "notfound" in type(exc).__name__.lower():
        return True
    msg = str(exc).lower()
    return (
        "could not be found" in msg
        or "not found" in msg
        or "does not exist" in msg
        or "no such object" in msg
    )


def enabled() -> bool:
    return _enabled


def upload(name: str, path: Path) -> bool:
    """Mirror a local file into the bucket under key `name`. Best-effort."""
    if not _enabled or not path.exists():
        return False
    try:
        # Prefer the filename API; fall back to bytes for older clients.
        if hasattr(_client, "upload_from_filename"):
            _client.upload_from_filename(name, str(path))
        else:
            _client.upload_from_bytes(name, path.read_bytes())
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("storage: upload failed for %s: %s", name, exc)
        return False


def exists(name: str) -> bool:
    if not _enabled:
        return False
    try:
        return bool(_client.exists(name))
    except Exception:  # noqa: BLE001
        return False


def download(name: str, path: Path) -> bool:
    """Pull key `name` from the bucket onto local disk at `path`. Best-effort."""
    if not _enabled:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".dl")
        if hasattr(_client, "download_to_filename"):
            _client.download_to_filename(name, str(tmp))
        else:
            tmp.write_bytes(_client.download_as_bytes(name))
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001
        # A plain "not found" is expected, not an error: the object was never
        # persisted (e.g. clips wiped in a republish before the bucket
        # existed). Log those at debug so they don't flood the console; keep
        # genuine failures (permissions, network) at warning.
        if _is_not_found(exc):
            log.debug("storage: %s not in bucket (never persisted)", name)
        else:
            log.warning("storage: download failed for %s: %s", name, exc)
        return False


def list_names() -> set[str]:
    if not _enabled:
        return set()
    try:
        out: set[str] = set()
        for obj in _client.list():
            # Objects expose their key as `.name`; be defensive about shape.
            n = getattr(obj, "name", None) or (obj if isinstance(obj, str) else None)
            if n:
                out.add(n)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("storage: list failed: %s", exc)
        return set()


def ensure_local(clips_dir: Path, name: str) -> bool:
    """Guarantee `clips_dir/name` exists locally, pulling it from the bucket
    if the ephemeral disk lost it (e.g. after a redeploy). Returns True if the
    file is present locally afterward.

    A NAME IN A MIRRORED FOLDER KEEPS ITS FOLDER, both as the key and
    as the path on disk. Flattening it -- which is what this did to
    every name -- put `_flowcuts/a.mp4` back as `clips/a.mp4`, which
    is not where the row that named it says it is, so the download
    "succeeded" and the file was still missing.
    """
    if not name:
        return False
    key = key_for(name)
    local = clips_dir / key
    if local.exists():
        return True
    return download(key, local)


def remove(name: str) -> bool:
    """Delete the bucket's copy. Best-effort; missing is success.

    THE COUNTERPART OF THE SWEEPER, and the reason retention needs it:
    once a file is mirrored, deleting it from local disk alone leaves
    the bucket holding it for ever -- and worse, the next `ensure_local`
    pulls the deleted file straight back. A sweep that cannot forget
    is not a sweep.
    """
    if not _enabled or not name:
        return False
    key = key_for(name)
    try:
        if hasattr(_client, "delete"):
            _client.delete(key)
        else:  # pragma: no cover - older clients
            _client.delete_object(key)
        return True
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return True
        log.warning("storage: delete failed for %s: %s", key, exc)
        return False


# Files the sweeper should never push to the bucket (local-only artefacts).
_SKIP_SUFFIXES = (".dl", ".reencode.mp4", ".h264-ok")


def _sweepable(clips_dir: Path):
    """Every file the sweeper should consider, as (key, path) pairs.

    The top level, plus one level down into each mirrored folder. Not
    `rglob`: a sweep that walks everything would also pick up
    `_testcuts/`, the per-press scratch the next Test Cut deletes.
    """
    try:
        tops = list(clips_dir.glob("*"))
    except OSError:
        return
    for f in tops:
        try:
            if f.is_file():
                yield f.name, f
        except OSError:
            continue
    for sub_name in MIRRORED_SUBDIRS:
        try:
            for f in (clips_dir / sub_name).glob("*"):
                if f.is_file():
                    yield "%s/%s" % (sub_name, f.name), f
        except OSError:
            continue


def start_sweeper(clips_dir: Path, interval_sec: float = 30.0) -> None:
    """Background mirror: every `interval_sec`, upload any clip on local disk
    that isn't in the bucket yet. Avoids having to hook every write site.

    Skips files modified in the last ~20 s so we never upload one mid-write.
    Idempotent to start (guards against double-launch). No-op when storage is
    disabled."""
    if not _enabled:
        return
    if getattr(start_sweeper, "_started", False):
        return
    start_sweeper._started = True  # type: ignore[attr-defined]

    def _run() -> None:
        # Seed with what's already in the bucket so we don't re-upload.
        known = list_names()
        log.info("storage: sweeper started (%d object(s) already in bucket)", len(known))
        while True:
            try:
                now = time.time()
                for key, f in _sweepable(clips_dir):
                    if key in known:
                        continue
                    if f.name.endswith(_SKIP_SUFFIXES):
                        continue
                    # Skip very-recent files — may still be being written.
                    try:
                        if now - f.stat().st_mtime < 20:
                            continue
                    except OSError:
                        continue
                    if upload(key, f):
                        known.add(key)
                        log.info("storage: mirrored %s to bucket", key)
            except Exception as exc:  # noqa: BLE001
                log.warning("storage: sweeper pass failed: %s", exc)
            time.sleep(interval_sec)

    threading.Thread(target=_run, name="storage-sweeper", daemon=True).start()
