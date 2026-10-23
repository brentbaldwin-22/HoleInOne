"""WHO HELD THE EVENT LOOP? A watchdog that answers it in the log.

Render kills an instance that cannot answer /health inside five
seconds, and /health here is two lines returning a dict -- it touches
nothing. So a failed health check never means "/health is slow". It
means the process could not get round to running it, and there are
three different ways that happens:

  1. THE EVENT LOOP IS BLOCKED. Something ran synchronous work on it --
     a DB round trip in an `async def`, a file write, a long CPU stretch
     -- and nothing else on the loop runs until it finishes.

  2. THE THREADPOOL IS FULL. A sync `def` endpoint (which /health is)
     runs in anyio's worker pool. Forty slots, and if forty requests are
     sitting in there blocked on a dry connection pool, /health queues
     behind them with the loop perfectly healthy.

  3. THE GIL IS HELD. Produce runs in this process. `nice` makes the
     heavy threads lose every CPU contest, which is the right thing and
     is not the whole story: a thread doing pure-Python or numpy work
     holds the interpreter lock whatever its nice value, and no other
     Python code runs at all until it lets go.

These look identical from the dashboard -- one line, "Instance failed",
and a recovery a minute later -- and they have three different fixes. So
rather than reason about which one it was, measure: a coroutine touches
a timestamp four times a second, and a plain thread watches that
timestamp. When it goes stale, the loop was not running, and the stack
of every thread in the process at that moment says what was.

The threadpool is read from the same coroutine, so a report also says
whether the workers were saturated -- which separates (2) from the rest
even when the loop never stalled at all.

Costs nothing when nothing is wrong: one timestamp write per 250 ms and
one comparison per second, and not a line of log until something takes
longer than it should.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("golfreelz.stall")

# A loop that has not ticked for this long was not merely busy. The
# health check gives five seconds; this reports well under it, so a
# stall that was survived is still on the record -- those are the ones
# worth seeing, because they are the same fault a size smaller.
STALL_SECONDS = 2.0

# How often the loop says it is alive, and how often the watchdog looks.
TICK_SECONDS = 0.25
WATCH_SECONDS = 1.0

# Frames per thread in a report. Enough to see the call site and how it
# got there; not so many that one stall fills a log page.
STACK_DEPTH = 14

_state = {
    "tick": 0.0,          # monotonic, last time the loop ran
    "borrowed": 0,        # anyio worker threads in use
    "total": 0,           # anyio worker threads available
    "worst": 0.0,         # longest stall seen since boot
    "ticks": 0,           # how many times the loop has reported in
}
_started = False


def _thread_names() -> dict[int, str]:
    return {t.ident: t.name for t in threading.enumerate() if t.ident}


def _dump_stacks() -> str:
    """Every thread's stack, as one block, most interesting first.

    The thread holding things up is nearly always doing something
    recognisable -- cv2, ffmpeg's communicate(), psycopg's wait, our own
    produce -- so the names in these frames are the answer.
    """
    names = _thread_names()
    out: list[str] = []
    for ident, frame in sys._current_frames().items():
        name = names.get(ident, "?")
        stack = traceback.format_stack(frame)[-STACK_DEPTH:]
        # A thread parked in a queue or sleeping is not the culprit and
        # there are several of them; keep the line, drop the stack.
        tail = "".join(stack).rstrip()
        out.append(f"--- {name} ({ident}) ---\n{tail}")
    return "\n".join(out)


async def _ticker() -> None:
    """Runs on the event loop. Its only job is to prove the loop runs."""
    try:
        from anyio.to_thread import current_default_thread_limiter
    except Exception:  # noqa: BLE001 - older anyio; the rest still works
        current_default_thread_limiter = None  # type: ignore[assignment]
    while True:
        _state["tick"] = time.monotonic()
        _state["ticks"] += 1
        if current_default_thread_limiter is not None:
            try:
                lim = current_default_thread_limiter()
                _state["borrowed"] = int(lim.borrowed_tokens)
                _state["total"] = int(lim.total_tokens)
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(TICK_SECONDS)


def _watch() -> None:
    """Runs in a plain thread, so a blocked loop cannot silence it."""
    reported = False
    worst_this_stall = 0.0
    while True:
        time.sleep(WATCH_SECONDS)
        # STARTUP IS NOT A STALL. Starlette calls a sync startup handler
        # on the loop itself, so migrations and seeding block it by
        # design and for as long as they take. Arm only once the loop
        # has reported in under its own steam, or every boot would open
        # with an error about the boot.
        if _state["ticks"] < 2:
            continue
        tick = _state["tick"]
        if not tick:
            continue
        lag = time.monotonic() - tick
        if lag < STALL_SECONDS:
            if reported:
                # It came back. The length of the stall is the number
                # that matters -- five seconds is an instance Render
                # kills, two is a warning -- and it is only knowable
                # here, at the end. The first report cannot carry it.
                log.warning("stall: event loop running again after %.1fs",
                            worst_this_stall)
                reported = False
                worst_this_stall = 0.0
            continue
        # Keep measuring while it lasts, even though only the first
        # crossing is reported: otherwise "worst" records the threshold
        # the stall tripped rather than how bad it got.
        worst_this_stall = max(worst_this_stall, lag)
        if lag > _state["worst"]:
            _state["worst"] = lag
        if reported:
            continue  # one report per stall, not one per second
        reported = True
        heavy = _heavy_label()
        log.error(
            "stall: the event loop has not run for %.1fs "
            "(worst %.1fs) — anyio workers %s/%s%s\n%s",
            lag, _state["worst"], _state["borrowed"], _state["total"],
            f", heavy job: {heavy}" if heavy else ", no heavy job running",
            _dump_stacks(),
        )


def _heavy_label() -> str | None:
    try:
        from . import workload
        return workload.current_heavy()
    except Exception:  # noqa: BLE001
        return None


def start() -> None:
    """Begin watching. Idempotent; safe to call from FastAPI startup."""
    global _started
    if _started:
        return
    _started = True
    _state["tick"] = time.monotonic()
    try:
        asyncio.get_running_loop().create_task(_ticker())
    except RuntimeError:
        # No loop yet (called outside startup). Nothing to watch.
        _started = False
        log.warning("stall: no running loop; watchdog not started")
        return
    threading.Thread(target=_watch, daemon=True, name="stall-watch").start()
    log.info("stall: watching the event loop (reports past %.1fs)",
             STALL_SECONDS)


def snapshot() -> dict:
    """Current lag and worst-since-boot, for an admin readout."""
    tick = _state["tick"]
    return {
        "lag_seconds": round(time.monotonic() - tick, 3) if tick else None,
        "worst_seconds": round(_state["worst"], 3),
        "anyio_workers_busy": _state["borrowed"],
        "anyio_workers_total": _state["total"],
        "heavy_job": _heavy_label(),
    }
