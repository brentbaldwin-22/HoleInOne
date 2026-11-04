#!/usr/bin/env python3
"""Does moving the cut off the detection thread keep every swing?

WHAT THIS GUARDS. The sub-cut, the swing judge, the pick-best contest
and the ship used to run on the thread that reads the microphone --
about thirteen seconds a strike, almost all of it ffmpeg plus the
/verify-swing round trip. On the tee rig, 4 Nov, a burst of ten
strikes put the ears 143 seconds behind the course, and the segment
ring only keeps about 153:

    copy: impact at 1793805858.7 needs 1793805854.2..1793805867.7
          and the ring holds 1793805855.0..1793806008.0 — not cutting

That is a confirmed swing, golfer in the zone, lost by eight tenths of
a second of retention because nothing looked at it for two minutes.

So the work moved to agent/cutqueue.CutQueue. Four things have to stay
true for that to be an improvement rather than a new way to lose a
swing, and each is a separate section below:

  1. SUBMITTING IS FAST. The whole point. A submit must return in
     microseconds no matter how slow the worker is.
  2. ORDER IS KEPT. The pick-best contest compares candidates in the
     order they were heard, and the refractory is sequential.
  3. NOTHING IS SILENTLY DROPPED. A shed job is loud, and a stop
     ships what is held rather than stranding it on the card.
  4. THE WINDOW STILL CLOSES. It used to be polled from the detection
     loop every tick; if the worker does not poll it on an idle tick
     too, a held swing is never shipped at all -- which is worse than
     never holding it, because the clip exists and nobody is waiting.

    python3 tools/cut_queue_test.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.cutqueue import CutQueue  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def waitfor(pred, timeout=5.0):
    """Poll until pred() or the timeout. True if it came true."""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def main() -> int:
    # ---- 1. submitting does not wait on the work ---------------------
    # The regression this whole change exists to prevent: if submit()
    # blocks, the ears are behind again and nothing above matters.
    gate = threading.Event()
    seen: list = []

    def slow(job):
        gate.wait(5.0)
        seen.append(job["at"])

    q = CutQueue(work=slow, flush=None, idle_sec=0.05)
    q.start()
    t0 = time.time()
    for at in range(5):
        q.submit({"at": float(at)})
    submit_cost = time.time() - t0
    check_true("five submits cost under a tenth of a second while the "
               "worker is wedged", submit_cost < 0.1)
    print(f"      (took {submit_cost * 1000:.1f} ms)")
    check("nothing has been cut yet — it is on the other thread", seen, [])
    gate.set()
    check_true("all five are cut once the worker is let go",
               waitfor(lambda: len(seen) == 5))

    # ---- 2. in the order they were heard -----------------------------
    # The contest compares a challenger against the held leader, so a
    # reordering would hand the window to the wrong swing.
    check("and in the order they were heard", seen, [0.0, 1.0, 2.0, 3.0, 4.0])
    q.stop(timeout=2.0)

    # ---- 3a. a shed job is counted and loud --------------------------
    # Dropping the OLDEST is deliberate: it is the closest to having
    # its footage swept out of the ring, so it is the least likely to
    # still be cuttable. Dropping the newest would throw away the one
    # job we know is still in the ring.
    gate2 = threading.Event()
    done2: list = []

    def slow2(job):
        gate2.wait(5.0)
        done2.append(job["at"])

    q2 = CutQueue(work=slow2, flush=None, max_depth=3, idle_sec=0.05)
    q2.start()
    # Let the worker pick one up and wedge on it, so the queue itself
    # is what fills.
    q2.submit({"at": 0.0})
    waitfor(lambda: q2.depth() == 0)
    for at in (1.0, 2.0, 3.0, 4.0, 5.0):
        q2.submit({"at": at})
    check("the queue is capped at its depth", q2.depth(), 3)
    check("the overflow is counted as shed, not forgotten", q2.shed, 2)
    gate2.set()
    waitfor(lambda: len(done2) == 4)
    check("the oldest were the ones dropped, the newest survived",
          done2, [0.0, 3.0, 4.0, 5.0])
    q2.stop(timeout=2.0)

    # ---- 3b. stopping ships what is held -----------------------------
    # A swing held mid-contest when the agent goes down must not be
    # stranded: the clip is on the card and the backend never hears
    # about it.
    flushed: list = []
    q3 = CutQueue(work=lambda job: None,
                  flush=lambda: flushed.append(time.time()),
                  idle_sec=0.05)
    q3.start()
    waitfor(lambda: len(flushed) >= 1)
    n_before = len(flushed)
    q3.stop(timeout=2.0)
    check_true("the window is flushed once more on the way out",
               len(flushed) > n_before)

    # ---- 3c. a stop does not start a thirteen-second cut ------------
    # Queued jobs are abandoned on purpose -- the ring is being stopped
    # underneath them -- but the one in hand is never interrupted.
    gate4 = threading.Event()
    started4: list = []
    done4: list = []

    def slow4(job):
        started4.append(job["at"])
        gate4.wait(5.0)
        done4.append(job["at"])

    q4 = CutQueue(work=slow4, flush=None, idle_sec=0.05)
    q4.start()
    q4.submit({"at": 10.0})
    waitfor(lambda: started4 == [10.0])
    q4.submit({"at": 11.0})
    q4.submit({"at": 12.0})
    stopper = threading.Thread(target=q4.stop, kwargs={"timeout": 3.0})
    stopper.start()
    waitfor(lambda: q4.depth() == 0)
    check("a stop abandons what is still queued", q4.depth(), 0)
    gate4.set()
    stopper.join(timeout=4.0)
    check("the job in hand was finished, not interrupted", done4, [10.0])
    check("  and the abandoned ones were never started", started4, [10.0])

    # ---- 4. the window closes with nothing being heard ---------------
    # It used to be polled from the detection loop on every tick. If
    # the worker only flushed after a job, a swing held during a quiet
    # spell would sit there for ever.
    ticks: list = []
    q5 = CutQueue(work=lambda job: None,
                  flush=lambda: ticks.append(1), idle_sec=0.05)
    q5.start()
    check_true("the held window is polled on idle ticks, with no jobs at all",
               waitfor(lambda: len(ticks) >= 3, timeout=2.0))
    q5.stop(timeout=2.0)

    # ---- 5. a failing cut does not take the worker down --------------
    # A sub-cut can fail for a dozen reasons. If the exception escaped,
    # every swing after it would be stranded too.
    survived: list = []

    def boom(job):
        if job["at"] == 0.0:
            raise RuntimeError("ffmpeg fell over")
        survived.append(job["at"])

    q6 = CutQueue(work=boom, flush=None, idle_sec=0.05)
    q6.start()
    q6.submit({"at": 0.0})
    q6.submit({"at": 1.0})
    check_true("a cut that raises does not stop the ones behind it",
               waitfor(lambda: survived == [1.0]))
    check("and the failure is not counted as done", q6.done, 1)
    q6.stop(timeout=2.0)

    # ---- 6. a flush that raises does not take it down either --------
    alive: list = []

    def bad_flush():
        raise RuntimeError("the backend is down")

    q7 = CutQueue(work=lambda job: alive.append(job["at"]),
                  flush=bad_flush, idle_sec=0.05)
    q7.start()
    q7.submit({"at": 7.0})
    check_true("a flush that raises leaves the worker running",
               waitfor(lambda: alive == [7.0]))
    q7.submit({"at": 8.0})
    check_true("  and still cutting", waitfor(lambda: alive == [7.0, 8.0]))
    q7.stop(timeout=2.0)

    # ---- 7. the wiring, so the move cannot be quietly undone --------
    # Every check above passes against a CutQueue nobody calls. These
    # pin the two sites that have to stop doing the work inline.
    tee = (ROOT / "pi-agent" / "agent" / "tee.py").read_text()
    eng = (ROOT / "pi-agent" / "agent" / "copyengine.py").read_text()

    # NOT a bare "is this string absent": `_flush_cut_window` calls it
    # on purpose, and an assertion that forbade the string outright
    # would have to be deleted to make the change work at all, which
    # is the opposite of a guard. So: it may be called from exactly one
    # place, and that place is the worker's flush.
    _window = tee[tee.index("def _flush_cut_window"):]
    _window = _window[:_window.index("\n    def ", 1)] if "\n    def " in \
        _window[1:] else _window
    check("the window is flushed from exactly one place",
          tee.count("flush_pick_best(self)"), 1)
    check("  and that place is the worker, not the detection loop",
          "self._copy.flush_pick_best(self)" in _window, True)
    check("the audio trigger's own callback only submits",
          "_q.submit(_job)" in tee, True)
    check("  and the thirteen seconds live on the worker",
          "def _run_cut_job" in tee, True)
    check("the worker drives the window",
          "def _flush_cut_window" in tee, True)
    check("the queue is started beside the engine it cuts from",
          "self._cuts.start()" in tee, True)
    # THE SHUTDOWN ORDER IS LOAD-BEARING. The final flush ships a held
    # swing, and shipping hands a clip to the uploader and reads the
    # ring -- so both have to outlive the worker or that last swing is
    # cut and then dropped on the floor.
    check("  stopped before the uploader it feeds",
          tee.index("self._cuts.stop()")
          < tee.index("self.uploader.stop(drain_timeout"), True)
    check("  and before the ring it reads",
          tee.index("self._cuts.stop()") < tee.index("self._copy.stop()"),
          True)

    # A submit racing a stop must not append to a queue nobody will
    # look at again: the flag is checked inside the critical section
    # that `stop` clears the queue in.
    cq = (ROOT / "pi-agent" / "agent" / "cutqueue.py").read_text()
    _sub = cq[cq.index("    def submit"):]
    _sub = _sub[:_sub.index("\n    def ", 1)]
    check("submit checks the stop flag under the lock",
          _sub.index("with self._wake:")
          < _sub.index("if self._stopping.is_set():"), True)
    check("switching the ears off ships rather than politely declining",
          "self._copy.ship_held_now(self)" in tee, True)

    # `_held` is now reached from the worker AND the watch-status
    # thread. Shipping it twice registers one swing as two events.
    check("the held candidate is locked", "self._held_lock" in eng, True)
    check("  and it is swapped out under the lock before shipping",
          "cand, self._held = self._held, None" in eng, True)
    check("  with the ship itself outside it",
          "return self._ship_one(runner, cand)" in eng, True)

    # ---- 8. the queued call is the call that used to be made --------
    # The refactor split one function into a describe half and a do
    # half, with a dict in between. A mistyped key there would send a
    # swing to the wrong span of the ring, which looks like a bad cut
    # rather than a bad refactor. So: drive a real TeeAgent's
    # `_cut_at_impact` both ways and demand the same arguments.
    #
    # The decision below is the real one from the tee rig's journal at
    # 10:27:29 on 4 Nov -- the strike that became clip 1317:
    #
    #   audio trigger: cutting 1793805902.3..1793805915.8s
    #                  around 1793805906.774 — x12.9, person in zone 0
    from agent import tee as teemod  # noqa: E402  (needs cv2; late on purpose)

    calls: list = []

    class FakeCopy:
        def cut_at_impact(self, runner, impact_ts, *, before_sec, after_sec,
                          clock_offset_sec=None, detail=None):
            calls.append(dict(impact_ts=impact_ts, before_sec=before_sec,
                              after_sec=after_sec,
                              clock_offset_sec=clock_offset_sec,
                              detail=detail))
            return "sid"

        def flush_pick_best(self, runner, now=None):
            return None

    class FakeTrig:
        confirm_tolerance_sec = 6.0

    agent = object.__new__(teemod.TeeAgent)
    agent._copy = FakeCopy()
    agent._roi_lock = threading.Lock()
    agent.roi = None
    agent._audio_trigger = FakeTrig()
    agent._cuts = None

    decision = {"at": 1793805906.774, "start": 1793805902.3,
                "end": 1793805915.8, "ratio": 12.9, "attack_ms": 9.0,
                "zone": 0, "why": "person in zone 0"}

    agent._cut_at_impact(decision)
    check("with no queue it still cuts, inline", len(calls), 1)
    inline = calls[-1]
    check("  reaching back to the strike", round(inline["before_sec"], 3),
          round(1793805906.774 - 1793805902.3, 3))
    check("  and forward through the flight", round(inline["after_sec"], 3),
          round(1793805915.8 - 1793805906.774, 3))
    check("  carrying the ratio for the tiebreak",
          inline["detail"]["ratio"], 12.9)
    check("  and the zone that confirmed it", inline["detail"]["zone"], 0)

    q8 = CutQueue(work=agent._run_cut_job, flush=agent._flush_cut_window,
                  idle_sec=0.05)
    q8.start()
    agent._cuts = q8
    agent._cut_at_impact(decision)
    check_true("the same decision goes through the queue",
               waitfor(lambda: len(calls) == 2))
    q8.stop(timeout=3.0)
    check("and arrives as the identical call", calls[-1] == inline, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
