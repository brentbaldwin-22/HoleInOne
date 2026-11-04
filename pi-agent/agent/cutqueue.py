"""A single worker thread for everything downstream of a heard impact.

WHY THIS EXISTS. The audio trigger used to do all of its work on the
detection loop: `tick()` read the audio segments and decided, and then
the same call stack cut the clip with ffmpeg, built the swing-heat
composite, asked the backend to judge it, held it for the pick-best
contest and finally registered and uploaded it. Measured on the tee
rig, 4 Nov: about thirteen seconds per confirmed strike, almost all of
it the sub-cut plus the /verify-swing round trip over the modem.

Thirteen seconds is survivable once. The problem is that it is paid on
the thread that reads the microphone, so a burst of strikes puts the
EARS that far behind -- and the lag compounds, because every second
spent cutting is a second of audio segments piling up unread. From
that day's journal, matching each impact's own epoch against the
instant its line was printed:

    sound at 07:20:32   printed 07:20:33     1s behind
    sound at 08:16:27   printed 08:16:28     1s behind
    sound at 10:25:06   printed 10:27:29   143s behind
    sound at 12:38:40   printed 12:40:21   101s behind

Idle, the ears are instant. Busy, they fall over two minutes behind,
and two minutes is longer than the segment ring keeps. So the lag does
not merely delay swings, it DESTROYS them: the same journal has

    copy: impact at 1793805858.7 needs 1793805854.2..1793805867.7
          and the ring holds 1793805855.0..1793806008.0 — not cutting

which is a confirmed swing, with a golfer in the zone, lost by eight
tenths of a second of ring retention because it was not processed until
143 seconds after it happened.

WHAT MOVES AND WHAT DOES NOT. The cut, the judge, the contest and the
ship move here. `tick()` stays on the detection loop, deliberately:
`tee_roi.seen_in_zones` ITERATES the sightings deque that the same loop
appends to, and reading it from another thread would risk "deque
mutated during iteration" on every judged strike. The ears keep their
current home; they simply stop waiting on anything.

ONE WORKER, IN ORDER. Not a pool. The work is already serialised by
ffmpeg and by the link, two cuts at once would contend for both, and
the pick-best contest only makes sense if candidates are compared in
the order they were heard. FIFO, one at a time, exactly as before --
just not in front of the microphone.

THE REFRACTORY CHANGES CHARACTER, which is worth saying out loud
because it is a behaviour change and not a bug. `note_swing` fires at
ship time and nowhere else, on purpose: quiet starts only once a swing
has been CONFIRMED, never on loudness. With the cut off this thread, a
strike heard during those thirteen seconds now reaches the judge
instead of being silenced by a quiet period that had not been set yet.
The twelve-second pick-best contest is the mechanism meant for exactly
that overlap -- it judges both and uploads the better one. So a burst
costs more judge calls than it used to and still produces one clip per
window. `note_swing` is left where it is rather than being moved
earlier, because setting quiet at decision time would suppress on
loudness, which is the one thing this trigger has always refused to do.

A STALE JOB COSTS NOTHING. `CopyEngine.cut_at_impact` checks the ring
before it spends anything, so a job whose footage has already aged out
returns in microseconds and the queue catches up by itself. That is
why the depth cap here is generous and is a memory bound rather than a
scheduling policy.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Optional

log = logging.getLogger("golfreelz_agent.cutqueue")

# Jobs waiting to be cut. Each is a small dict; the cap is a memory
# bound, not a scheduling decision -- see the module docstring on why a
# stale job is nearly free. Deep enough that a long burst on a busy tee
# is never shed, shallow enough to notice if something has wedged.
_MAX_DEPTH = 64

# How long the worker waits for a job before looking at the held
# candidate anyway. The pick-best window is twelve seconds and has to
# close on time whether or not another strike arrives, and the
# detection loop used to poll it every tick.
_IDLE_SEC = 1.0

# How long `stop` waits for the worker to put down what it is holding.
# One job is a sub-cut plus a judge round trip; this is generous enough
# to let that finish rather than killing a swing on shutdown.
_STOP_TIMEOUT = 20.0


class CutQueue:
    """Runs `work(job)` on its own thread, FIFO, one at a time.

    `flush` is called after every job AND on every idle tick, because
    the pick-best window has to close on schedule even when nothing
    else is being heard. Both callables are invoked with no arguments
    beyond the job, and neither may raise: anything that escapes is
    logged and swallowed, because this thread dying silently would
    strand every swing after it.
    """

    def __init__(self, work: Callable[[dict], None],
                 flush: Optional[Callable[[], None]] = None,
                 max_depth: int = _MAX_DEPTH,
                 idle_sec: float = _IDLE_SEC,
                 name: str = "tee-cutqueue"):
        self._work = work
        self._flush = flush
        self._idle_sec = float(idle_sec)
        self._name = name
        self._q: deque = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._max_depth = int(max_depth)
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        # Counters, for the journal and for `status()`. Nothing reports
        # them to the backend yet. `shed` is the one worth an alert: a
        # non-zero value means strikes arrived faster than they could
        # be cut for long enough to fill the queue, which is the state
        # this class exists to make visible instead of silent.
        self.queued = 0
        self.done = 0
        self.shed = 0

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=self._name,
        )
        self._thread.start()
        log.info("cut queue: started — sub-cuts and the swing judge run "
                 "here from now on, so the microphone never waits on them")

    def stop(self, timeout: float = _STOP_TIMEOUT) -> None:
        """Finish the job in hand, ship what is held, abandon the rest.

        THE JOB IN HAND IS NOT INTERRUPTED, and what is held is still
        shipped: the worker may be mid-upload on a confirmed swing, and
        the whole point of the parked clips and the recovery path is
        that a swing survives things going wrong. Cutting the thread
        off at the knees here would be the one case that strands one on
        purpose.

        What is still QUEUED is abandoned, and said out loud. Draining
        it would be thirteen seconds a job against a ring that is being
        stopped underneath us, so those cuts would mostly fail on
        retention anyway -- and a shutdown that takes a quarter of an
        hour is how an operator ends up pulling the power instead.
        """
        self._stopping.set()
        with self._wake:
            abandoned = len(self._q)
            self._q.clear()
            self._wake.notify_all()
        if abandoned:
            log.warning("cut queue: shutting down with %d job(s) still "
                        "queued — those swings are not cut", abandoned)
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=timeout)
            if t.is_alive():
                log.warning("cut queue: the worker is still busy after "
                            "%.0fs — leaving it to finish", timeout)

    # ---- submission -----------------------------------------------------

    def submit(self, job: dict, front: bool = False) -> bool:
        """Queue a job. True if it was taken, False if it was refused.

        Returns rather than blocks, because the caller is the detection
        loop and blocking it is the entire problem this class exists to
        solve. A full queue drops the OLDEST job, not this one: the
        oldest is the closest to having its footage swept out of the
        ring and so the least likely to still be cuttable.

        `front` jumps the queue, and exactly one caller uses it: a
        finished DWELL clip. The two job kinds have very different
        amounts of slack against the ring. An audio cut needs about
        13.5s of footage around an impact a second or two old, so it
        can wait more than two minutes and still be cuttable. A dwell
        clip can be 126s long, and the ring holds about 153 -- so it
        has perhaps 25 seconds before its own beginning is swept out
        from under it. Behind a burst of ten audio cuts at 13s each it
        would be gone, and today it is never lost at all. So it goes
        first. There is only one worker either way, so this reorders
        the queue without ever running two ffmpegs at once.
        """
        with self._wake:
            # CHECKED UNDER THE LOCK, not before it. `stop` sets the
            # flag and clears the queue in one critical section, so a
            # check outside could pass, lose the race, and append a job
            # to a queue nobody will ever look at again -- a swing lost
            # with nothing said about it.
            if self._stopping.is_set():
                log.info("cut queue: shutting down — not taking another cut")
                return False
            dropped = None
            if len(self._q) >= self._max_depth:
                dropped = self._q.popleft()
                self.shed += 1
            if front:
                self._q.appendleft(job)
            else:
                self._q.append(job)
            self.queued += 1
            depth = len(self._q)
            self._wake.notify()
        if dropped is not None:
            log.warning(
                "cut queue: %d deep and still filling — dropped the oldest "
                "job (the impact at %.1f). Strikes are arriving faster than "
                "they can be cut; %d shed so far.",
                depth, float(dropped.get("at") or 0.0), self.shed,
            )
        elif depth > 1:
            # WORTH SAYING, because a queue that is never empty is the
            # symptom this was built to make visible. At depth 1 it is
            # just working.
            log.info("cut queue: %d jobs waiting to be cut", depth)
        return True

    def depth(self) -> int:
        with self._lock:
            return len(self._q)

    def status(self) -> dict:
        with self._lock:
            depth = len(self._q)
        return {"depth": depth, "queued": self.queued,
                "done": self.done, "shed": self.shed}

    # ---- the worker -----------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            job = None
            with self._wake:
                if not self._q and not self._stopping.is_set():
                    self._wake.wait(self._idle_sec)
                # CHECKED AGAIN under the lock: `stop` clears the queue
                # and sets the flag while we were waiting, and starting
                # a thirteen-second cut after that is exactly what the
                # flag is for.
                if self._q and not self._stopping.is_set():
                    job = self._q.popleft()
            if job is not None:
                try:
                    self._work(job)
                except Exception as exc:  # noqa: BLE001
                    # A cut that fails costs this swing and nothing
                    # else. The dwell is still recording, the next
                    # strike is still queued, and this thread must not
                    # be the thing that stops.
                    log.warning("cut queue: the cut failed (%s)", exc)
                else:
                    self.done += 1
            if self._stopping.is_set():
                break
            self._run_flush()
        # ONE LAST FLUSH ON THE WAY OUT. A candidate waiting out its
        # contest window was heard before the stop was asked for, and
        # after this thread there is nobody left to ship it.
        self._run_flush()
        log.info("cut queue: stopped (%d cut, %d shed)", self.done, self.shed)

    def _run_flush(self) -> None:
        """Close the pick-best window if it is due. Never raises."""
        if self._flush is None:
            return
        try:
            self._flush()
        except Exception as exc:  # noqa: BLE001
            log.warning("cut queue: could not flush the held swing (%s)", exc)
