#!/usr/bin/env python3
"""Can an hour of retention fill the SD card?

Until now the ring evicted on AGE alone. At the shipped 155s window
that is 77 MB and nothing can go wrong. The continuous-capture design
asks for 3600s, and then age-only eviction is unbounded in BYTES:

    4000 kbps   1.8 GB / hour
    8000 kbps   3.6 GB / hour      <- camera bitrate is operator-set
   16000 kbps   7.2 GB / hour

The 2 GB upload spool shares the same filesystem. A full card on a
golf course is a camera nobody can reach, so retention has to yield to
disk. These tests are about that yielding — and just as much about it
NOT happening when there is room, because a guard that evicts eagerly
destroys the recovery footage it was added to protect.

    python3 tools/ring_disk_test.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.copycap import SegmentRing  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def seg_name(when):
    from datetime import datetime
    return "seg-%s.ts" % datetime.fromtimestamp(when).strftime("%Y%m%d-%H%M%S")


def ring(d, **kw):
    r = SegmentRing("rtsp://x/y", d, **kw)
    return r


def seg(d, index, when, size):
    """A segment file named as the ring names them, aged and sized.

    THE AGE IS IN THE NAME, not the mtime: the ring parses
    seg-YYYYmmdd-HHMMSS.ts and ignores the filesystem's own stamps. An
    earlier version of this fixture wrote seg-00000.ts and touched the
    mtime, so the glob matched nothing, the ring saw an empty
    directory, and every assertion about eviction passed by measuring
    nothing at all.
    """
    from datetime import datetime
    name = "seg-%s.ts" % datetime.fromtimestamp(when).strftime("%Y%m%d-%H%M%S")
    p = Path(d) / name
    p.write_bytes(b"\0" * size)
    return p


def main() -> int:
    now = time.time()
    MB = 1024 * 1024

    # ---- the guard bites on a byte cap ---------------------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=10, min_free_mb=0)
        for i in range(20):
            seg(td, i, now - (20 - i), 1 * MB)      # 20 MB, all fresh
        dropped = r._sweep_for_space()
        left = len(list(Path(td).glob("*.ts")))
        check("a 10 MB cap over 20 MB of fresh segments evicts", dropped > 0,
              True)
        check("  down to about the cap", 9 <= left <= 12, True)
        names = sorted(q.name for q in Path(td).glob("*.ts"))
        check("  oldest first — the newest survives",
              names[-1], seg_name(now - 1))
        check("  and the oldest is the one gone",
              seg_name(now - 20) in names, False)

    # ---- CONTROL: room to spare, nothing is touched --------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=100, min_free_mb=0)
        for i in range(20):
            seg(td, i, now - (20 - i), 1 * MB)
        check("under the cap, nothing is evicted (control)",
              r._sweep_for_space(), 0)
        check("  every segment survives (control)",
              len(list(Path(td).glob("*.ts"))), 20)

    # CONTROL: both guards off is the old behaviour, untouched.
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=0, min_free_mb=0)
        for i in range(20):
            seg(td, i, now - (20 - i), 1 * MB)
        check("with no caps set the guard does nothing (control)",
              r._sweep_for_space(), 0)

    # ---- free space: evict until the floor is met ----------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, min_free_mb=1, window_max_mb=0)
        for i in range(10):
            seg(td, i, now - (10 - i), 1 * MB)
        r._free_bytes = lambda: 0.0        # disk is full
        dropped = r._sweep_for_space()
        check("a nearly full disk evicts until the floor is met",
              dropped, 1)
        check("  and stops there, not evicting the lot",
              len(list(Path(td).glob("*.ts"))), 9)

    # ---- unknowable free space must NOT evict --------------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, min_free_mb=99999, window_max_mb=0)
        for i in range(10):
            seg(td, i, now - (10 - i), 1 * MB)
        r._free_bytes = lambda: None          # statvfs failed
        check("a failed stat does not throw footage away", 
              r._sweep_for_space(), 0)
        check("  (control) and a real reading at the same floor does",
              (lambda rr: (setattr(rr, "_free_bytes", lambda: 0.0),
                           rr._sweep_for_space())[1])(r) > 0, True)

    # ---- a one-segment ring is left alone ------------------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=1, min_free_mb=0)
        seg(td, 0, now, 50 * MB)
        check("the segment being written is never evicted",
              r._sweep_for_space(), 0)

    # ---- age eviction still works, and still runs first ----------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=10, min_free_mb=0, window_max_mb=0)
        for i in range(5):
            seg(td, i, now - 100, 1 * MB)      # all long expired
        seg(td, 5, now, 1 * MB)
        r._sweep()
        check("age eviction still drops expired segments",
              len(list(Path(td).glob("*.ts"))), 1)

    # ---- status reports the guard --------------------------------
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600)
        st = r.status()
        check("status carries a free-space reading",
              isinstance(st.get("free_mb"), int), True)
        check("  and a count of space evictions", st["evicted_for_space"], 0)


    # ---- the space sweep must run FROM the age sweep -------------
    # Testing _sweep_for_space() directly says nothing about whether
    # anything ever calls it. _sweep() is what the ring's own thread
    # runs; deleting the call from it left every assertion above green.
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=5, min_free_mb=0)
        for i in range(20):
            seg(td, i, now - (20 - i), 1 * MB)
        r._sweep()
        left = len(list(Path(td).glob("*.ts")))
        check("the age sweep also enforces the disk guard",
              left < 20, True)
        check("  down to about the cap", left <= 8, True)

    # ---- even a brutal cap keeps the segment being written -------
    # The earlier "newest survives" case stopped evicting long before
    # it reached the newest, so it could not see segs vs segs[:-1].
    with tempfile.TemporaryDirectory() as td:
        r = ring(td, window_seconds=3600, window_max_mb=0.001,
                 min_free_mb=0)
        for i in range(5):
            seg(td, i, now - (5 - i), 1 * MB)
        r._sweep_for_space()
        left = sorted(q.name for q in Path(td).glob("*.ts"))
        check("a cap smaller than one segment still leaves exactly one",
              len(left), 1)
        check("  and it is the newest, which ffmpeg is writing into",
              left[0], seg_name(now - 1))

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
