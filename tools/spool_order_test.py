#!/usr/bin/env python3
"""Does the spool finish the OLDEST clip, or nibble at all of them?

Nine tee clips, each 10-25% uploaded, each stalled over an hour, not
one of them finished. `_retry_one_spooled` carries a comment headed
"DO NOT ROTATE" explaining precisely why that must not happen — and it
happened anyway, through two doors that comment did not cover:

1. THE SHRINK RESET THE CLIP'S AGE. The ladder re-encodes a clip that
   has failed enough times, `compress_for_upload` replaces the file,
   and the replacement carries a brand-new mtime. The spool ordered by
   mtime, so the clip the sweep had just made sendable sorted to the
   BACK of the queue. Eviction used mtime too, so a re-encode also reset
   the 24-hour clock.

2. EVERY FRESH CLIP GOT AN ATTEMPT AHEAD OF THE BACKLOG. The backoff
   gate covered a link that had failed, but `_note_result` deliberately
   keeps the quiet window short for a link that is merely slow — which
   is the state the tee lives in. So each new swing spent the link
   painting a sliver onto itself, and the clip at the head of the queue
   never got a clear run.

Both are ordering bugs, so both are invisible to a test that uploads
one clip and checks it arrived.

    python3 tools/spool_order_test.py
"""
from __future__ import annotations

import json
import subprocess
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
    """A backend that is always unreachable, and remembers who knocked."""

    def __init__(self):
        self.attempts: list[str] = []

    def _knock(self, *a, **k):
        for v in list(a) + list(k.values()):
            if isinstance(v, (str, Path)) and str(v).endswith(".mp4"):
                self.attempts.append(Path(v).name)
                break
        raise RuntimeError("link down")

    upload_event = _knock
    upload_event_chunked = _knock


def spool_a_clip(spool: Path, name: str, joined_at, mtime=None) -> Path:
    """A clip in the spool, with its join time and its mtime set apart."""
    p = spool / f"{name}.mp4"
    p.write_bytes(b"\0" * 4096)
    meta = {"session_id": name, "recording_started_at": joined_at,
            "tries": 0, "kbps": 2500, "compressed": True}
    if joined_at is not None:
        meta["spooled_at"] = joined_at
    p.with_suffix(".json").write_text(json.dumps(meta))
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))
    return p


def uploader(spool: Path, client=None):
    return common.BackgroundUploader(
        client or FakeClient(), compress_kbps=0, spool_dir=spool,
        settle_seconds=0.0, backoff_base=0.0,
        fresh_timeout=1, idle_timeout=1, patient_timeout=1,
    )


def main() -> int:  # noqa: C901
    tmp = Path(tempfile.mkdtemp())
    now = time.time()

    # ---- 1. ORDER SURVIVES A RE-ENCODE ------------------------------
    spool = tmp / "s1"
    spool.mkdir()
    # `old` joined an hour ago and was re-encoded a moment ago, so its
    # mtime is NEWER than the clip that joined after it. This is exactly
    # the state the shrink ladder leaves behind, and under the old
    # mtime ordering it sent the sweep to `later` instead.
    spool_a_clip(spool, "old", joined_at=now - 3600, mtime=now)
    spool_a_clip(spool, "later", joined_at=now - 60, mtime=now - 60)
    up = uploader(spool)
    check("the oldest clip is still next after it was re-encoded",
          up._next_spooled()[0].name, "old.mp4")
    check("and the queue order is by join time, not mtime",
          [p.name for _j, p, _m in up._spool_entries()],
          ["old.mp4", "later.mp4"])

    # THE CONTROL. Without the fix the mtime IS the join time, so an
    # assertion that only ever saw agreeing clocks would pass on the
    # bug. Here they disagree the other way: `old` really is older by
    # both clocks, and must still come first.
    spool2 = tmp / "s1b"
    spool2.mkdir()
    spool_a_clip(spool2, "old", joined_at=now - 3600, mtime=now - 3600)
    spool_a_clip(spool2, "later", joined_at=now - 60, mtime=now - 60)
    check("agreeing clocks order the same way (control)",
          [p.name for _j, p, _m in uploader(spool2)._spool_entries()],
          ["old.mp4", "later.mp4"])

    # ---- 2. AN OLDER AGENT'S SPOOL STILL SORTS ----------------------
    # Clips spooled before this change have no `spooled_at`. They must
    # fall back to mtime rather than all collapsing to one value.
    spool3 = tmp / "s2"
    spool3.mkdir()
    spool_a_clip(spool3, "legacy-old", joined_at=None, mtime=now - 7200)
    spool_a_clip(spool3, "legacy-new", joined_at=None, mtime=now - 10)
    check("a spool written by an older agent still sorts oldest-first",
          [p.name for _j, p, _m in uploader(spool3)._spool_entries()],
          ["legacy-old.mp4", "legacy-new.mp4"])

    # ---- 3. EVICTION AGES BY JOIN TIME ------------------------------
    # A clip spooled 30 hours ago is past the cap even if the shrink
    # ladder rewrote it a minute ago. Under mtime it looked one minute
    # old and could outlive the cap indefinitely, while genuinely older
    # footage was dropped to make room for it.
    spool4 = tmp / "s3"
    spool4.mkdir()
    spool_a_clip(spool4, "stale", joined_at=now - 30 * 3600, mtime=now)
    spool_a_clip(spool4, "fresh", joined_at=now - 60, mtime=now - 60)
    up4 = uploader(spool4)
    up4.spool_max_age = 24 * 3600
    up4._prune_spool()
    check("a 30h-old clip is evicted despite a one-minute-old mtime",
          sorted(p.name for p in spool4.glob("*.mp4")), ["fresh.mp4"])
    check("and its sidecar goes with it",
          sorted(p.name for p in spool4.glob("*.json")), ["fresh.json"])

    # THE CONTROL. Nothing is evicted when nothing is actually stale,
    # or "evicts the stale one" would pass by evicting everything.
    spool5 = tmp / "s4"
    spool5.mkdir()
    spool_a_clip(spool5, "a", joined_at=now - 3600, mtime=now)
    spool_a_clip(spool5, "b", joined_at=now - 60, mtime=now - 60)
    up5 = uploader(spool5)
    up5.spool_max_age = 24 * 3600
    up5._prune_spool()
    check("a spool inside its bounds is left alone (control)",
          sorted(p.name for p in spool5.glob("*.mp4")), ["a.mp4", "b.mp4"])

    # ---- 4. compress_for_upload KEEPS THE FILE'S AGE ----------------
    # The fallback above is only honest if a re-encode stops bumping
    # mtime. This runs the real ffmpeg, because the bug was one line of
    # Path.replace() semantics and a stub would have reproduced neither.
    clip = tmp / "real.mp4"
    gen = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=False, capture_output=True,
    )
    if gen.returncode != 0 or not clip.exists():
        print("SKIP  compress_for_upload mtime: ffmpeg could not make a clip")
    else:
        import os
        was = now - 12345
        os.utime(clip, (was, was))
        before = clip.stat().st_mtime
        ok = common.compress_for_upload(clip, target_kbps=200)
        check("the re-encode ran at all (control)", ok, True)
        check("and left the clip's age alone",
              round(clip.stat().st_mtime, 3), round(before, 3))

    # ---- 5. A FRESH CLIP JOINS THE BACK OF A BACKLOG ----------------
    spool6 = tmp / "s5"
    spool6.mkdir()
    spool_a_clip(spool6, "waiting", joined_at=now - 3600, mtime=now - 3600)
    client = FakeClient()
    up6 = uploader(spool6, client)
    check("the gate sees the backlog", up6._park_fresh()[1], 1)
    check("and no quiet window is doing the work (control)",
          up6._park_fresh()[0], 0)
    up6.start()
    fresh = tmp / "brand-new.mp4"
    fresh.write_bytes(b"\0" * 4096)
    up6.enqueue("sess-new", fresh, time.time(), real_fps=None)
    # Wait for the SWEEP, not just for the park: the fresh clip lands in
    # the spool within a tick, and stopping there would prove only that
    # it was parked -- never that the link then went to the older clip.
    deadline = time.time() + 20
    while time.time() < deadline and "waiting.mp4" not in client.attempts:
        time.sleep(0.1)
    up6.stop(drain_timeout=0.0)
    check("the fresh clip was parked, not sent",
          (spool6 / "brand-new.mp4").exists(), True)
    check("nothing was attempted on it ahead of the backlog",
          "brand-new.mp4" in client.attempts, False)
    check("the link spent its time on the clip already waiting",
          "waiting.mp4" in client.attempts, True)

    # ---- 6. THE CONTROL: AN EMPTY SPOOL STILL GETS AN ATTEMPT -------
    # Without this, "never attempts a fresh clip" would pass on an
    # uploader that had simply stopped uploading.
    spool7 = tmp / "s6"
    spool7.mkdir()
    client7 = FakeClient()
    up7 = uploader(spool7, client7)
    check("an empty spool is no reason to park", up7._park_fresh(), (0, 0))
    up7.start()
    solo = tmp / "solo.mp4"
    solo.write_bytes(b"\0" * 4096)
    up7.enqueue("sess-solo", solo, time.time(), real_fps=None)
    deadline = time.time() + 15
    while time.time() < deadline and "solo.mp4" not in client7.attempts:
        time.sleep(0.1)
    up7.stop(drain_timeout=0.0)
    check("a clip with nothing ahead of it is attempted immediately",
          "solo.mp4" in client7.attempts, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
