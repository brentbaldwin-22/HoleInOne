"""ONE HEAVY JOB AT A TIME, AND NEVER AT THE WEB SERVER'S EXPENSE.

Production runs inside the web process. On the box we deploy to that is
ONE core, shared by uvicorn's event loop, every OpenCV decode and every
ffmpeg we spawn — all at the same scheduler priority. When a round comes
in, the health check is just another thread asking for a slice of a core
that cv2 and ffmpeg are already using, and Render kills an instance that
cannot answer `/health` within five seconds. The restart then takes the
produce down with it, which is how a busy evening turns into a failed
clip and an "Instance failed" line in the dashboard.

Two rules, and this module is both of them:

`heavy_gate` — the single lock every CPU-heavy media job holds. The
produce queue already had one; the re-encode-and-thumbnail pass that
runs when a Pi finishes uploading deliberately did NOT, and the comment
at its call site said so: "Runs in parallel with the production job."
Now it waits. One job at a time finishes sooner in wall-clock than two
fighting over one core, and it leaves that core answerable.

`deprioritize()` — the heavy threads run nice, so the web server wins
every contest for the CPU. The work takes no longer in practice (it is
the only thing asking for the core), but the event loop is never more
than a scheduler slice away from running.

WHY NICING THE THREAD IS ENOUGH FOR FFMPEG TOO. On Linux a thread is a
task with its own nice value, and a child process inherits the nice
value of the thread that forked it. So one call at the top of a worker
thread covers every ffmpeg that thread will ever spawn — no change at
eighteen subprocess call sites, and no `preexec_fn`, which is unsafe in
a multi-threaded program.

That inheritance is also why this is Linux-only. Elsewhere `nice()` can
apply to the whole process, which would renice uvicorn itself and slow
down exactly what we are protecting. Off Linux it does nothing, and the
single-core box this exists for is Linux.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("golfreelz.workload")

# Reentrant so a heavy job that calls another heavy job on the SAME
# thread proceeds instead of deadlocking against itself. It still blocks
# a second thread, which is the whole point.
heavy_gate = threading.RLock()

# +10 is a long way down an ordinary scheduler run queue without being
# the bottom: these jobs should yield to the web server, not to nothing.
NICE_DELTA = 10

_local = threading.local()


def deprioritize(delta: int = NICE_DELTA) -> int | None:
    """Drop THIS THREAD below the web server. Returns the new nice value.

    Call it once at the top of a dedicated worker thread — never from a
    thread that will go on to serve requests, because a nice value
    cannot be lowered again without privileges. Calling it twice on the
    same thread is a no-op rather than another ten steps down.
    """
    if getattr(_local, "niced", None) is not None:
        return _local.niced
    if sys.platform != "linux":
        return None
    if threading.current_thread() is threading.main_thread():
        # Would renice the whole process on some kernels, and the main
        # thread is where uvicorn's event loop lives.
        log.warning("workload: refusing to deprioritize the main thread")
        return None
    try:
        new = os.nice(delta)
    except (AttributeError, OSError) as exc:  # no nice(), or refused
        log.debug("workload: could not deprioritize this thread: %s", exc)
        return None
    _local.niced = new
    log.info("workload: %s running at nice %+d",
             threading.current_thread().name, new)
    return new


@contextmanager
def heavy(label: str):
    """Hold the gate for a stretch of CPU-heavy media work.

    Logs the wait when there was one, because "slow produce" and "queued
    behind another produce" look identical from the outside and only the
    second one is working as designed.
    """
    t0 = time.monotonic()
    with heavy_gate:
        waited = time.monotonic() - t0
        if waited > 1.0:
            log.info("workload: %s waited %.1fs for the gate", label, waited)
        yield
