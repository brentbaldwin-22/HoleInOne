#!/usr/bin/env python3
"""Does a stream-copied clip reach the backend un-re-encoded?

This exists because it already failed once in production. precompressed
=True guarded _send, but a clip spooled WITHOUT an attempt — what
happens while a capture is running or the link is settling — took a
different route that compressed it anyway. The engine extracted 207 MB
of 1920x1080 and the server received 12.1 MB of 1280x720.

It also pins the tuple shape of the uploader's queue. Adding a field to
it broke the shutdown drain's unpack, which runs only while stopping
with clips still queued: the one path whose entire job is not to lose
footage. This code ships to the tee camera, which cannot currently be
reached to fix, so "read it again carefully" is not good enough.

    python3 tools/uploader_precompressed_test.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import common  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class FakeClient:
    """A backend that is always unreachable, so every clip spools."""
    def __init__(self):
        self.attempts = 0

    def upload_event(self, *a, **k):
        self.attempts += 1
        raise RuntimeError("link down")

    def upload_event_chunked(self, *a, **k):
        self.attempts += 1
        raise RuntimeError("link down")


def main() -> int:
    calls: list[dict] = []
    real = common.compress_for_upload

    def spy(path, target_kbps=2500, scale_height=None, timeout=240,
            force_input_fps=None):
        calls.append({"path": Path(path).name, "kbps": target_kbps,
                      "scale_height": scale_height})
        return False       # leave the file alone; we only care that it was asked

    common.compress_for_upload = spy
    try:
        tmp = Path(tempfile.mkdtemp())
        spool = tmp / "spool"
        spool.mkdir()

        # settle_seconds MUST BE NON-ZERO. _quiet is max(backoff,
        # settling), and with settle_seconds=0 a "capture is running"
        # still leaves _quiet at 0 — so the clip takes the ordinary
        # _send path and never touches the branch under test. The first
        # version of this file got that wrong and passed against the
        # bug it was written to catch.
        up = common.BackgroundUploader(
            FakeClient(), compress_kbps=2500, scale_height=720,
            spool_dir=spool, settle_seconds=60.0, backoff_base=0.1,
            fresh_timeout=1, idle_timeout=1, patient_timeout=1,
        )

        # ---- THE PRODUCTION BUG: spooled without an attempt ----------
        # A capture is running, so the worker parks the clip instead of
        # sending it. That route compressed it.
        up.capture_started()
        check("a running capture really does make the worker go quiet",
              up._settling() > 0, True)
        copied = tmp / "copy-abc.mp4"
        copied.write_bytes(b"\0" * 2048)
        up.start()
        up.enqueue("sess-copy", copied, time.time(), real_fps=None,
                   precompressed=True)
        deadline = time.time() + 10
        while time.time() < deadline and not list(spool.glob("*.mp4")):
            time.sleep(0.1)
        check("a precompressed clip spooled during a capture is untouched",
              [c for c in calls if c["path"] == "copy-abc.mp4"], [])
        check("it reached the spool", len(list(spool.glob("*.mp4"))), 1)

        import json
        meta = json.loads(next(spool.glob("*.json")).read_text())
        check("the spool records that it is NOT this uploader's encode",
              meta.get("compressed"), False)

        # ---- and an ordinary clip on the SAME path still gets one ----
        # Same quiet window, same branch: the only difference is the
        # flag, which is what makes this the control.
        calls.clear()
        plain = tmp / "decode-xyz.mp4"
        plain.write_bytes(b"\0" * 2048)
        up.enqueue("sess-plain", plain, time.time(), real_fps=29.9)
        deadline = time.time() + 10
        while time.time() < deadline and not calls:
            time.sleep(0.1)
        check("a decode-path clip is still compressed",
              [c["path"] for c in calls], ["decode-xyz.mp4"])
        check("at the configured scale", calls[0]["scale_height"], 720)

        up.stop(drain_timeout=0.0)

        # ---- the shutdown drain must not throw on the queue shape ----
        # A SECOND, UNSTARTED UPLOADER. stop(drain_timeout=N) waits for
        # the queue to EMPTY before draining, so with a worker running
        # there is nothing left for the drain loop to unpack and the
        # path under test never executes — which is how the first
        # version of this check passed with a four-field unpack still
        # in place. No worker, so the clips are still there.
        up2 = common.BackgroundUploader(
            FakeClient(), compress_kbps=2500, spool_dir=spool,
        )
        for i in range(3):
            p = tmp / f"late-{i}.mp4"
            p.write_bytes(b"\0" * 512)
            up2.enqueue(f"late-{i}", p, time.time(),
                        precompressed=bool(i % 2))
        check("the clips really are still queued", up2._q.qsize(), 3)
        before = len(list(spool.glob("*.mp4")))
        try:
            up2.stop(drain_timeout=0.0)
            drained, err = True, None
        except Exception as exc:  # noqa: BLE001
            drained, err = False, repr(exc)
        check("stopping with clips queued does not throw", drained, True)
        if err:
            print(f"       {err}")
        check("and every queued clip was spooled rather than dropped",
              len(list(spool.glob("*.mp4"))) - before, 3)

        print()
        if FAIL:
            print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
            return 1
        print("all good")
        return 0
    finally:
        common.compress_for_upload = real


if __name__ == "__main__":
    raise SystemExit(main())
