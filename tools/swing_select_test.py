#!/usr/bin/env python3
"""Does swing selection ever LOSE a swing?

That is the only question that matters here. Selection saves
bandwidth, and bandwidth is worth nothing next to footage: a missed
shot is a refund. So every assertion below is about the fallback —
when anything at all goes wrong, the whole session must still go up,
exactly as it did before this feature existed.

The paths that must all end in "upload whole":
  * no tracker (feature off, or a green)
  * too few motion samples
  * no swing found
  * windows cover so much of the session that cutting saves nothing
  * ffmpeg refuses a cut
  * a PARTIAL cut set — the subtle one. Sending the cuts that worked
    would silently drop the swings whose cuts did not.

Runs the real ffmpeg for the cuts, because a stub cannot show that a
sub-cut is still playable, still first-generation, and the right
length.

    python3 tools/swing_select_test.py
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import copyengine  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class FakeUploader:
    def __init__(self):
        self.sent = []

    def enqueue(self, sid, path, ts, real_fps=None, precompressed=False):
        self.sent.append({"sid": sid, "path": Path(path),
                          "bytes": Path(path).stat().st_size,
                          "precompressed": precompressed})


class FakeTracker:
    """Replays a canned motion signal, as the real one would have."""

    def __init__(self, times, motion):
        self._t, self._m = times, motion

    def span(self, t0, t1):
        return list(self._t), list(self._m)


CLIP_START = 1000.0          # res["start"], wall-clock, as in production


class Runner:
    def __init__(self, tracker, people=None):
        self.motion = tracker
        # Person sightings are WALL CLOCK, the same scale as
        # res["start"] — the engine subtracts one from the other. An
        # earlier version of this fixture used 0.0 here against a
        # clip_start of 1000.0, so every sighting landed 1000s before
        # every swing and the gate rejected the lot.
        self._person_seen_at = (
            people if people is not None
            else [CLIP_START + x for x in (0, 10, 20, 30, 40, 50, 60)])


def signal(duration, events, rate=20.0, base=1.0):
    n = int(duration * rate)
    times = [i / rate for i in range(n)]
    vals = [base] * n
    for centre, peak, width in events:
        sd = max(1e-6, width / 2.355)
        for i in range(n):
            dt = times[i] - centre
            vals[i] += (peak - base) * math.exp(-(dt * dt) / (2 * sd * sd))
    return times, vals


def make_src(path, seconds):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=size=320x180:rate=30", "-t", str(seconds),
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)


def dur(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True, check=False).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return -1.0


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("SKIP: ffmpeg/ffprobe not installed")
        return 0

    eng = copyengine.CopyEngine.__new__(copyengine.CopyEngine)

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = d / "copy-abc123-ffffff.mp4"
        make_src(src, 60)
        res = {"start": CLIP_START, "seconds": 60.0}

        def fresh():
            """Each phase starts from an intact session file."""
            for stale in d.glob("*-sw*.mp4"):
                stale.unlink()
            make_src(src, 60)


        # ---- the happy path -------------------------------------
        t, m = signal(60, [(12.0, 8.0, 0.5), (40.0, 8.0, 0.5)])
        up = FakeUploader()
        whole_bytes = src.stat().st_size      # the engine deletes src on
        sent = eng._enqueue_swings(Runner(FakeTracker(t, m)), up, "sessA",
                                   src, res, 1005.0)   # success
        check("two swings are sent as two clips", sent, 2)
        check("  the uploader got exactly those", len(up.sent), 2)
        check("  each is smaller than the session",
              all(x["bytes"] < whole_bytes for x in up.sent)
              if up.sent else False, True)
        check("  each is still marked first-generation",
              all(x["precompressed"] for x in up.sent) if up.sent else False,
              True)
        lens = [round(dur(x["path"])) for x in up.sent]
        check("  and each is roughly the 13.5s window",
              all(8 <= L <= 16 for L in lens), True)
        check("  the session file is removed once its cuts are away",
              src.exists(), False)

        # ---- every fallback must upload whole -------------------
        fresh()
        up = FakeUploader()
        check("no tracker -> upload whole",
              eng._enqueue_swings(Runner(None), up, "s", src, res, 0.0), 0)
        check("  and nothing was enqueued behind our back", len(up.sent), 0)
        check("  and the session file survives", src.exists(), True)

        fresh()
        t2, m2 = signal(60, [(30.0, 8.0, 0.5)], rate=0.1)
        check("too few samples -> upload whole",
              eng._enqueue_swings(Runner(FakeTracker(t2, m2)), up, "s",
                                  src, res, 0.0), 0)

        fresh()
        t3, m3 = signal(60, [])
        check("no swing found -> upload whole",
              eng._enqueue_swings(Runner(FakeTracker(t3, m3)), up, "s",
                                  src, res, 0.0), 0)

        fresh()
        t4, m4 = signal(60, [(30.0, 8.0, 0.5)])
        check("nobody in the ROI -> upload whole (cloud, not golf)",
              eng._enqueue_swings(Runner(FakeTracker(t4, m4), people=[]),
                                  up, "s", src, res, 0.0), 0)

        # five swings across 60s: the windows tile it, cutting is moot
        fresh()
        t5, m5 = signal(60, [(8.0, 8.0, 0.4), (20.0, 8.0, 0.4),
                             (31.0, 8.0, 0.4), (42.0, 8.0, 0.4),
                             (53.0, 8.0, 0.4)])
        check("windows covering most of the session -> upload whole",
              eng._enqueue_swings(Runner(FakeTracker(t5, m5)), up, "s",
                                  src, res, 0.0), 0)
        check("  still nothing enqueued on any fallback", len(up.sent), 0)
        check("  and the session file is still there for the caller",
              src.exists(), True)

        # ---- ffmpeg refusing ------------------------------------
        fresh()
        missing = d / "not-here.mp4"
        up = FakeUploader()
        check("a source ffmpeg cannot read -> upload whole",
              eng._enqueue_swings(Runner(FakeTracker(t, m)), up, "s",
                                  missing, res, 0.0), 0)
        check("  and no half-made cut was sent", len(up.sent), 0)

        # ---- PARTIAL cuts: the subtle one -----------------------
        fresh()
        real_subcut = copyengine._subcut
        calls = {"n": 0}

        def flaky(s_, dst, a, b):
            calls["n"] += 1
            return real_subcut(s_, dst, a, b) if calls["n"] == 1 else False

        copyengine._subcut = flaky
        try:
            up = FakeUploader()
            sent = eng._enqueue_swings(Runner(FakeTracker(t, m)), up, "sP",
                                       src, res, 0.0)
        finally:
            copyengine._subcut = real_subcut
        check("one cut of two failing -> upload whole, not the half set",
              sent, 0)
        check("  the successful cut is NOT left dangling on the uploader",
              len(up.sent), 0)
        check("  and its file is cleaned up",
              list(d.glob("*-sw*.mp4")), [])
        check("  the session file is intact for the caller", src.exists(),
              True)

        # ---- selection must never break a recording ------------
        # Everything above tests paths that DECLINE. This tests the
        # path that THROWS: a bug anywhere in selection must cost the
        # bandwidth saving and nothing else. Without the catch-all the
        # exception reaches record_and_upload after the clip is
        # already cut and the partner already signalled, and the swing
        # is lost to a feature whose entire job is to be optional.
        class ExplodingTracker:
            def span(self, a, b):
                raise RuntimeError("selection is broken")

        fresh()
        up = FakeUploader()
        check("a tracker that raises -> upload whole, no exception",
              eng._enqueue_swings(Runner(ExplodingTracker()), up, "sX",
                                  src, res, 0.0), 0)
        check("  nothing enqueued", len(up.sent), 0)
        check("  session file intact", src.exists(), True)

        class BadSpan:
            def span(self, a, b):
                return ["not", "numbers"], [None, None]

        fresh()
        check("garbage from the tracker -> upload whole",
              eng._enqueue_swings(Runner(BadSpan()), up, "sY", src, res,
                                  0.0), 0)

        # ---- CONTROL: the real subcut works --------------------
        fresh()
        out = d / "cut.mp4"
        check("control: a plain sub-cut succeeds",
              copyengine._subcut(src, out, 10.0, 20.0), True)
        check("  and is about the right length", 8 <= dur(out) <= 12, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
