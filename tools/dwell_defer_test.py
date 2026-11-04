#!/usr/bin/env python3
"""Does deferring a dwell clip's cut produce the same clip?

WHAT MOVED AND WHY. `record_and_upload` is two phases. The first
watches for the end of the swing, and has to run on the detection loop
because the stop policy polls the person detector that loop owns. The
second is ffmpeg and the link -- waiting for the last segment to close
(up to 8s), the extract (9.25s on a 126s clip, measured on the tee rig
4 Nov), the audio mux, and the hand-off to the uploader -- and paying
it on the detection loop stalls the microphone, which is the lag
tools/cut_queue_test.py exists about.

So phase two became `finish_clip`, and `record_and_upload` hands it to
a `defer` callback when one is given. Three things have to hold:

  1. The CLIP IS THE SAME. Same span, same bytes, same session handed
     to the uploader, whether the tail ran inline or on the worker.
  2. The REASON IS THE SAME, because the caller's dwell state machine
     runs off it -- splits, the runaway cooldown, whether the ring
     buffer is cleared and whether the dwell is re-armed.
  3. PHASE ONE RETURNS BEFORE PHASE TWO RUNS. That is the entire
     point; if it still waited, nothing would have been fixed.

AND THE ONE DIVERGENCE, pinned on purpose: `copy_extract_failed` can
only be known after the cut, so a deferred caller is told `no_person`
or `length_cap` instead. That changes nothing it acts on -- a person
standing in the ROI is standing there whether or not ffmpeg managed
the cut -- but it is a difference and it should fail loudly if it ever
becomes a silent one.

    python3 tools/dwell_defer_test.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.copyengine import CopyEngine  # noqa: E402
from agent.cutqueue import CutQueue  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class FakeRing:
    """A ring that always holds everything and cuts instantly."""

    def __init__(self, ok=True):
        self.ok = ok
        self.cuts = []

    def span(self):
        return (0.0, 1e12)

    def extract(self, t0, t1, out):
        self.cuts.append((round(t0, 3), round(t1, 3)))
        if not self.ok:
            return {"ok": False, "why": "the ring is unwell"}
        Path(out).write_bytes(b"\0" * 2048)
        return {"ok": True, "start": t0, "end": t1, "seconds": t1 - t0,
                "bytes": 2048, "took": 0.01, "segments": int(t1 - t0) or 1}


class FakeUploader:
    def __init__(self):
        self.sent = []
        self.ended = 0

    def capture_ended(self):
        self.ended += 1

    def enqueue(self, sid, path, start, **kw):
        self.sent.append((sid, round(float(start), 3)))


def rig(tmp, *, ring_ok=True):
    eng = object.__new__(CopyEngine)
    eng.ring = FakeRing(ok=ring_ok)
    eng.clips_dir = tmp
    # keep_audio True means "the camera's audio rides in the segments",
    # so _audio_recorder_for returns None and no recorder is involved.
    # That is the case this test wants: the clip, not the mux.
    eng.keep_audio = True
    eng._held = None
    eng._held_lock = threading.Lock()
    eng._extracted = 0
    eng._failed = 0
    eng._last = {}
    runner = types.SimpleNamespace(
        uploader=FakeUploader(), buffer_seconds=5.0, max_clip_seconds=120.0,
        stopping=threading.Event(), cfg={}, _audio_trigger=None,
        motion=None,
    )
    return eng, runner


def ends_after(seconds):
    """A stop policy that says no_person once `seconds` have passed."""
    t0 = time.time()

    def should_stop(now):
        return "no_person" if now - t0 >= seconds else None
    should_stop.stats = {"fake": True}
    return should_stop


def main() -> int:
    tmp = Path(tempfile.mkdtemp())

    # ---- 1. inline: today's behaviour, the control -------------------
    # BOTH RUNS ARE PINNED TO THE SAME t_trigger via not_before, so the
    # spans are comparable at all. Without it the two runs start a few
    # hundred milliseconds apart and every absolute timestamp differs
    # for a reason that has nothing to do with deferring.
    PINNED = time.time()
    PREROLL = 5.0               # runner.buffer_seconds
    BACK = round(PINNED - PREROLL, 3)

    eng, runner = rig(tmp)
    why = eng.record_and_upload(
        runner, "a-session", ends_after(0.3),
        on_recording_ended=lambda: None, not_before=PINNED,
    )
    inline_cut = eng.ring.cuts[0]
    check("inline, the reason is the stop policy's", why, "no_person")
    check("inline, one cut was made", len(eng.ring.cuts), 1)
    check("inline, it reaches back a full pre-roll", inline_cut[0], BACK)
    check("inline, the clip went to the uploader",
          runner.uploader.sent, [("a-session", BACK)])
    check("inline, the uploader was told the capture ended",
          runner.uploader.ended, 1)
    # And no silent-clip warning is even possible here: keep_audio means
    # the camera's own track is in the segments, so there is no recorder.
    check("a clip whose audio is in the segments has no recorder",
          eng._audio_recorder_for(runner), None)

    # ---- 2. deferred: the same clip, off the caller's thread ---------
    eng2, runner2 = rig(tmp)
    jobs: list = []
    t1 = time.time()
    why2 = eng2.record_and_upload(
        runner2, "a-session", ends_after(0.3),
        on_recording_ended=lambda: None, not_before=PINNED,
        defer=jobs.append,
    )
    phase_one_took = time.time() - t1
    check("deferred, the caller gets the same reason", why2, "no_person")
    check("deferred, nothing has been cut yet", eng2.ring.cuts, [])
    check("  nor handed to the uploader", runner2.uploader.sent, [])
    check("  the tail is waiting as one job", len(jobs), 1)

    # 3. phase one returned without paying for phase two
    check("phase one returns as soon as the swing ends", phase_one_took < 1.0,
          True)

    # THE CLIP MUST NOT GROW WHILE IT WAITS. `t_end` is captured in
    # phase one, when the swing ended; if the tail re-read the clock
    # the clip would stretch to cover however long the queue was, and a
    # dwell tail behind a burst would arrive as two minutes of empty
    # tee box. So sit on the job for a beat before running it.
    _ended_at = jobs[0]["t_end"]
    time.sleep(0.6)
    eng2.finish_clip(jobs[0])
    deferred_cut = eng2.ring.cuts[0]
    check("the deferred tail reaches back from the same trigger",
          deferred_cut[0], inline_cut[0])
    check("  and still ends where the swing ended, not where the queue did",
          deferred_cut[1], round(_ended_at, 3))
    check("  so waiting in the queue did not lengthen the clip",
          deferred_cut[1] - deferred_cut[0] < PREROLL + 1.0, True)
    check("  and hands the identical session and start to the uploader",
          runner2.uploader.sent, list(runner.uploader.sent))
    check("  and tells the uploader the capture ended, once",
          runner2.uploader.ended, 1)
    check("  and counts one clip extracted", eng2._extracted, 1)

    # ---- 4. through the real queue, at the front --------------------
    # A dwell tail has ~25s of slack against a 153s ring; an audio cut
    # has over two minutes. So it must not queue behind a burst.
    eng3, runner3 = rig(tmp)
    order: list = []
    q = CutQueue(work=lambda job: order.append(job["kind"]),
                 flush=None, idle_sec=0.05)
    # Deliberately NOT started: inspect the ordering the queue chose
    # rather than race a worker for it.
    q.submit({"kind": "audio-1"})
    q.submit({"kind": "audio-2"})
    q.submit({"kind": "dwell"}, front=True)
    drained = []
    while q.depth():
        with q._wake:
            drained.append(q._q.popleft()["kind"])
    check("a dwell tail jumps the queue ahead of waiting audio cuts",
          drained, ["dwell", "audio-1", "audio-2"])

    # ---- 5. the recorder is what decides whether we may defer -------
    # Deferring holds this clip's audio recorder open across the queue
    # wait, and the detection loop may open the next clip's meanwhile.
    # Two RTSP sessions to one camera is the thing that must not happen;
    # a RingAudioRecorder is not a session.
    class Ringish:
        def extract_span(self, a, b, out):
            return None

    class Rtspish:
        lead_seconds = 0.9

        def stop(self):
            return None

    check("no recorder at all: safe to defer",
          eng.clip_tail_is_deferrable(None), True)
    check("a ring recorder: safe to defer",
          eng.clip_tail_is_deferrable(Ringish()), True)
    check("a real RTSP recorder: NOT safe, stays inline",
          eng.clip_tail_is_deferrable(Rtspish()), False)

    # ---- 6. the documented divergence, held to its documentation ----
    # Inline, a failed extract is reported as copy_extract_failed.
    # Deferred, the caller was already told no_person -- and the dwell
    # state machine treats both the same way, which is why this is
    # allowed to differ at all.
    eng4, runner4 = rig(tmp, ring_ok=False)
    why4 = eng4.record_and_upload(
        runner4, "doomed", ends_after(0.2), on_recording_ended=lambda: None,
        not_before=PINNED,
    )
    check("inline, a failed cut says so", why4, "copy_extract_failed")

    eng5, runner5 = rig(tmp, ring_ok=False)
    jobs5: list = []
    why5 = eng5.record_and_upload(
        runner5, "doomed", ends_after(0.2), on_recording_ended=lambda: None,
        not_before=PINNED, defer=jobs5.append,
    )
    check("deferred, the caller hears the stop reason instead",
          why5, "no_person")
    check("  and the tail still reports the failure to its own caller",
          eng5.finish_clip(jobs5[0]), "copy_extract_failed")
    check("  having uploaded nothing", runner5.uploader.sent, [])
    check("  and counted the failure", eng5._failed, 1)

    # ---- 6b. the sightings are read on the caller's thread ----------
    # `_person_seen_at` is a deque the detection loop appends to, and
    # iterating a deque while it is appended to raises "deque mutated
    # during iteration". The swing gate used to read it on that same
    # thread. Read from the worker it would race -- and fail SILENTLY,
    # because _enqueue_swings catches everything and returns 0, which
    # means "upload the session whole": 60 MB of a 126s clip over a
    # cellular modem, the exact bill the gate exists to avoid.
    eng6, runner6 = rig(tmp)
    live = __import__("collections").deque([(1.0, 0), (2.0, 0)], maxlen=4000)
    runner6._person_seen_at = live
    jobs6: list = []
    eng6.record_and_upload(
        runner6, "snap", ends_after(0.2), on_recording_ended=lambda: None,
        not_before=PINNED, defer=jobs6.append,
    )
    check("the job carries a snapshot, not the live deque",
          isinstance(jobs6[0]["sightings"], list), True)
    check("  with the times it held at hand-off",
          jobs6[0]["sightings"], [1.0, 2.0])
    live.append((3.0, 0))          # the loop keeps going meanwhile
    check("  and the snapshot does not follow the deque",
          jobs6[0]["sightings"], [1.0, 2.0])

    # None and [] MUST STAY DIFFERENT. None is "no detector ran" -> do
    # not gate, or a green camera rejects every swing. [] is "it ran
    # and saw nobody" -> gate. A default of None would merge them.
    eng7, runner7 = rig(tmp)
    jobs7: list = []
    eng7.record_and_upload(
        runner7, "nodet", ends_after(0.2), on_recording_ended=lambda: None,
        not_before=PINNED, defer=jobs7.append,
    )
    check("no detector at all stays None, not an empty list",
          jobs7[0]["sightings"], None)

    # ---- 7. the wiring ----------------------------------------------
    tee = (ROOT / "pi-agent" / "agent" / "tee.py").read_text()
    check("the dwell call site hands the engine a defer",
          "defer=_defer," in tee, True)
    check("  only when there is a worker to defer to",
          "if getattr(self, \"_cuts\", None)\n                                      else None)"
          in tee, True)
    check("the worker knows both kinds of job",
          'job.get("kind") == "dwell"' in tee, True)
    # A tail queued as the agent goes down would be abandoned by
    # CutQueue.stop. The golfer has already hit and the footage is on
    # disk, so it finishes inline instead -- as it did before the queue.
    check("  and a tail is finished inline once we are stopping",
          "not self.stopping.is_set()" in tee, True)
    check("  and the dwell tail goes to the front",
          "front=True" in tee, True)

    # PHASE ONE MUST STILL OWN /event-stop. The green is sitting in its
    # own loop waiting to be told the swing ended, and until it is told
    # it records to its length cap. Deferring that would put the queue's
    # depth between the golfer finishing and the green stopping.
    eng_src = (ROOT / "pi-agent" / "agent" / "copyengine.py").read_text()
    _tail = eng_src[eng_src.index("    def finish_clip"):]
    check("the partner is released in phase one, not by the worker",
          "on_recording_ended" in _tail, False)

    # THE FALSE SILENT-CLIP WARNING, found writing this test.
    # `_audio_recorder_for` returns None on purpose when the camera's
    # audio is already in the segments. That None used to fall straight
    # into `audio.start(...)`, raise AttributeError, and be reported as
    # "audio capture did not start - the clip will be silent" on every
    # single clip, on exactly the rigs whose clips are NOT silent.
    check("a missing recorder is guarded, not caught as a failure",
          "        audio = self._audio_recorder_for(runner)\n"
          "        if audio is not None:" in eng_src, True)

    for q2 in tmp.rglob("*"):
        if q2.is_file():
            q2.unlink(missing_ok=True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
