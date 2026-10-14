#!/usr/bin/env python3
"""Does a swing survive a backend that cannot be reached?

Before this, it did not. The tee's /event-trigger was the first call
on the capture path and the only one with no fallback: it failed, the
loop logged "skipping", and the swing was never recorded. Not lost in
transit -- never cut. Two went that way inside two minutes on 14 Oct
on a rig whose modem re-enumerates every three minutes.

Everything downstream already survived an outage. The three pieces
tested here are what connect the trigger to it:

  1. BackendUnreachable -- silence told apart from refusal. A 4xx is
     the backend saying no; anything else is nobody answering.
  2. TriggerGate -- record when the trigger does not land, UNLESS the
     last thing the backend said was that this camera is paused.
  3. The recovery tri-state -- "the backend refused to make an event"
     deletes the clip; "we could not ask" must not.

Every assertion has a control beside it: the thing that must still
behave the old way, so a change that makes the new case pass by
breaking the old one fails here.

    python3 tools/trigger_queue_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.common import (  # noqa: E402
    BackendClient,
    BackendUnreachable,
    TriggerGate,
    UnattachableClip,
    BackgroundUploader,
)

# NO REAL BACKOFF. _retry sleeps 1+2+4+8s between its four attempts,
# so the two exhausted-retry checks below would spend thirty seconds
# of this test doing nothing -- and mutation-testing it runs the whole
# file once per mutation. The backoff is not what is under test; which
# exception comes out the end of it is.
#
# This replaces time.sleep for the PROCESS, not just for common.py
# (`common.time` is the stdlib module itself). Nothing else in this
# file sleeps, so that is harmless here -- but it is the reason not to
# copy these three lines into a test that waits on a thread.
import time as _time  # noqa: E402

_time.sleep = lambda *_a, **_k: None

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


# ---------------------------------------------------------------------
# 1. Silence vs refusal
# ---------------------------------------------------------------------

class FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text
        self.content = text.encode()

    def json(self):
        return json.loads(self.text) if self.text else {}


class FakeSession:
    """Answers each request from a scripted list."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        nxt = self.script.pop(0) if self.script else self.script_default()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def script_default(self):
        return FakeResp(500, "still broken")


def client_with(script):
    c = BackendClient("http://x", "tok")
    c.session = FakeSession(script)
    return c


def raised(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__
    return None


import requests  # noqa: E402

_NET = requests.ConnectionError("[Errno 101] Network is unreachable")

# The real failure from the tee's journal: the interface went away
# mid-POST. Every attempt dies the same way, nothing ever answers.
check(
    "a trigger that never gets an answer raises BackendUnreachable",
    raised(lambda: client_with([_NET] * 4).event_trigger("s1")),
    "BackendUnreachable",
)
# CONTROL: the backend answering 403 is a refusal, and must NOT come
# back as unreachable -- that is what tells the loop not to record.
check(
    "a 403 refusal stays a plain RuntimeError",
    raised(lambda: client_with(
        [FakeResp(403, "camera is disabled")]).event_trigger("s2")),
    "RuntimeError",
)
# CONTROL: the orphan rejection keeps its own class, which is what
# drives recovery rather than a retry.
check(
    "an orphan 404 is still UnattachableClip",
    raised(lambda: client_with([FakeResp(
        404, "no event for that session_id; trigger first",
    )]).event_trigger("s3")),
    "UnattachableClip",
)
# CONTROL: a working trigger still returns its body. A test that only
# proved the failures would pass against a client that never succeeds.
check(
    "a good trigger still returns the ack",
    client_with([FakeResp(200, '{"event_id": 7}')]).event_trigger("s4"),
    {"event_id": 7},
)
# A 5xx is a server that cannot answer. Same thing to a capture loop
# as a dead modem: nobody refused, so record.
check(
    "exhausted 5xx retries are unreachable too, not a refusal",
    raised(lambda: client_with([FakeResp(503, "upstream")] * 4
                               ).event_trigger("s5")),
    "BackendUnreachable",
)
# The subclassing is load-bearing: every existing `except
# RuntimeError` around a backend call must keep catching this.
check(
    "BackendUnreachable is still a RuntimeError",
    issubclass(BackendUnreachable, RuntimeError),
    True,
)


# ---------------------------------------------------------------------
# 2. The gate
# ---------------------------------------------------------------------

def gate(state_dir=None, told=None):
    g = TriggerGate(state_dir)
    if told is not None:
        g.note_backend_state(told)
    return g


with tempfile.TemporaryDirectory() as td:
    d = Path(td)

    check(
        "told enabled, trigger lost -> record the swing",
        gate(d, True).should_record_unregistered("a"),
        True,
    )
    # CONTROL: the operator's pause is the one veto. Without this the
    # feature would record on a camera sitting paused on a bench.
    check(
        "told PAUSED, trigger lost -> do not record",
        gate(d / "p", False).should_record_unregistered("b"),
        False,
    )
    # Never heard from the backend at all. Keeping footage is the
    # chosen default; a rig that has never polled is far likelier to be
    # a live camera with a sick modem than a paused one.
    check(
        "never told anything -> record",
        gate(d / "fresh").should_record_unregistered("c"),
        True,
    )
    # An older backend omits the key. ABSENT IS NOT PAUSED -- if None
    # were read as False, a Pi running ahead of its backend would stop
    # recording entirely.
    g = gate(d / "old", True)
    g.note_backend_state(None)
    check(
        "a missing field does not overwrite 'enabled'",
        (g.last_known_enabled, g.should_record_unregistered("d")),
        (True, True),
    )
    # ...and it must not resurrect a pause either.
    g = gate(d / "old2", False)
    g.note_backend_state(None)
    check(
        "a missing field does not overwrite 'paused'",
        (g.last_known_enabled, g.should_record_unregistered("e")),
        (False, False),
    )
    # The pause has to outlive a reboot: a browning-out rig restarts,
    # and in memory the answer would be gone exactly when it matters.
    pdir = d / "persist"
    pdir.mkdir()
    gate(pdir, False)
    check(
        "a pause is remembered across a restart",
        TriggerGate(pdir).should_record_unregistered("f"),
        False,
    )
    # CONTROL: and so is an enable, rather than everything persisting
    # as "do not record".
    edir = d / "persist2"
    edir.mkdir()
    gate(edir, True)
    check(
        "an enable is remembered across a restart",
        TriggerGate(edir).last_known_enabled,
        True,
    )
    # A corrupt state file must not be read as a pause, and must not
    # raise on a capture loop.
    cdir = d / "corrupt"
    cdir.mkdir()
    (cdir / TriggerGate.FILENAME).write_text("{not json")
    check(
        "a corrupt state file reads as 'never told' and still records",
        TriggerGate(cdir).should_record_unregistered("g"),
        True,
    )
    # With no state dir at all the gate still answers.
    check(
        "no state dir -> still answers, still records",
        TriggerGate(None).should_record_unregistered("h"),
        True,
    )


# ---------------------------------------------------------------------
# 3. Recovery: refused deletes the clip, unreachable must not
# ---------------------------------------------------------------------

class RecoveryProbe(BackgroundUploader):
    """A real uploader with only the wire replaced.

    Subclassed rather than stubbed so _send's actual control flow runs:
    the bug being guarded against lived in that flow, not in the
    client.
    """

    def __init__(self, deliver_raises, recover_raises):
        self._deliver_raises = deliver_raises
        self._recover_raises = recover_raises
        self.recover_calls = 0
        self.deliver_calls = 0
        # Only the fields _send touches; no thread is ever started.
        self._kbps_now = 0
        self.compress_kbps = 0
        self.scale_height = None
        self._last_progress = 0
        self._last_orphaned = False

    def _deliver(self, session_id, clip_path, ts, retries, timeout):
        self.deliver_calls += 1
        raise self._deliver_raises

    def _recover_lost_event(self, session_id):
        self.recover_calls += 1
        if self._recover_raises is None:
            return self.RECOVERED
        if isinstance(self._recover_raises, BackendUnreachable):
            return self.RECOVER_UNREACHABLE
        return self.RECOVER_REFUSED


class RecoveryClient:
    """Only the wire. _recover_lost_event itself is the thing on trial."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def recover_event(self, session_id):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return {"event_id": 42}


def real_recovery(exc):
    """Run the REAL _recover_lost_event with a scripted client.

    __new__ rather than __init__: the uploader's constructor wants a
    client, a spool and a thread, and this method reads none of them.
    Tested directly because the probe below overrides it -- which is
    how a mutation that collapsed "refused" into "unreachable" slipped
    through the first version of this file.
    """
    u = BackgroundUploader.__new__(BackgroundUploader)
    u.client = RecoveryClient(exc)
    return u._recover_lost_event("s")


# The three outcomes have to BE three. A mutation that returns the
# same string for two of them is the bug this guards.
check(
    "the three recovery outcomes are distinct",
    len({BackgroundUploader.RECOVERED,
         BackgroundUploader.RECOVER_REFUSED,
         BackgroundUploader.RECOVER_UNREACHABLE}),
    3,
)
check(
    "recovery that cannot reach the backend -> unreachable",
    real_recovery(BackendUnreachable("no answer")),
    BackgroundUploader.RECOVER_UNREACHABLE,
)
# CONTROL: an answered refusal is a different outcome, and it is the
# one that lets the clip be deleted.
check(
    "recovery the backend refuses -> refused",
    real_recovery(RuntimeError("400: session_id is required")),
    BackgroundUploader.RECOVER_REFUSED,
)
# A DELETED session comes back as UnattachableClip (the backend's 403
# says "not part of that event"). A deletion is final, so this must
# land on refused, not unreachable, or a deleted event's clip is
# retried for 24 hours.
check(
    "recovery of a DELETED session -> refused, not unreachable",
    real_recovery(UnattachableClip("403: this session was deleted")),
    BackgroundUploader.RECOVER_REFUSED,
)
# CONTROL: the success path still reports success, rather than
# everything funnelling into one failure answer.
check(
    "recovery that works -> registered",
    real_recovery(None),
    BackgroundUploader.RECOVERED,
)


_ORPHAN = UnattachableClip("404: no event for that session_id")


def send_outcome(deliver_exc, recover_exc):
    p = RecoveryProbe(deliver_exc, recover_exc)
    ok = p._send("s", Path("/nonexistent.mp4"), None, None,
                 compress=False, retries=1, timeout=5)
    return {"ok": ok, "orphaned": p._last_orphaned,
            "recoveries": p.recover_calls}

# THE BUG THIS GUARDS. Upload 404s, recovery cannot reach the backend.
# _last_orphaned must stay False, because True DELETES the clip -- on
# both the fresh path (never spooled) and the spool path (unlinked).
check(
    "upload orphaned + recovery unreachable -> keep the clip",
    send_outcome(_ORPHAN, BackendUnreachable("no answer")),
    {"ok": False, "orphaned": False, "recoveries": 1},
)
# CONTROL: a backend that ANSWERS and refuses -- the operator deleted
# the session -- must still mark it orphaned, or the uploader spends
# the link re-sending a dead clip for 24 hours.
check(
    "upload orphaned + recovery refused -> drop it as orphaned",
    send_outcome(_ORPHAN, RuntimeError("403: this session was deleted")),
    {"ok": False, "orphaned": True, "recoveries": 1},
)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
