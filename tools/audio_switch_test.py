#!/usr/bin/env python3
"""Switching the ears off actually stops the ring.

The backend already refuses an event from a switched-off mechanism and
the agent already declines to upload the clip, so NOTHING HERE IS
ABOUT CORRECTNESS. It is about a rig that has been told it is not
listening and is still holding a second RTSP session open to the
camera, decoding audio and running a detector over it for answers that
will all be thrown away.

Which makes the state machine the whole risk, because every way it can
be wrong is silent:

  * latching the backend's answer before there is anything to apply it
    to -- the next poll sees the same value, returns early, and the
    switch never happens at all;
  * treating a missing key as OFF, which would make every rig running
    ahead of its backend go deaf;
  * treating a ring that would not restart as "this rig cannot
    listen", which turns one unreachable moment into a round with no
    ears;
  * standing the dwell down for ears that have been switched off,
    which records nothing whatever.

This drives the real handler on a stub, so it is the shipped code
being asked, not a copy of it.

    python3 tools/audio_switch_test.py
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))

from agent import tee  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class FakeRing:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class FakeTrigger:
    def __init__(self):
        self.ring = FakeRing()
        self.shadow = False
        self.confirm_zones = None


class FakeCopy:
    def __init__(self):
        self.flushes = 0

    def flush_pick_best(self, runner):
        self.flushes += 1


class Stub:
    """Only the attributes the handler touches."""

    def __init__(self, capable=True, built=True, build_returns="trigger"):
        self.live = FakeTrigger() if capable else None
        self._audio_trigger = self.live
        self._audio_capable = capable
        self._audio_wanted = None
        self._audio_lock = threading.Lock()
        self._copy = FakeCopy()
        self.zone_checks = 0
        self.builds = 0
        self._build_returns = build_returns
        if built:
            self._audio_build = self._build
        else:
            self._audio_build = None

    def _build(self):
        self.builds += 1
        if self._build_returns == "trigger":
            self.live = FakeTrigger()
            return self.live
        return None

    def _check_dwell_yield_zones(self):
        self.zone_checks += 1

    # the real method, bound to this stub
    def note(self, audio_on, dwell_on=True):
        tee.TeeAgent._note_trigger_mechanisms(self, audio_on, dwell_on)

    @property
    def listening(self):
        return self._audio_trigger is not None


print("absent is not off:")
s = Stub()
s.note(None)
check("a missing key leaves the ears listening", s.listening, True)
check("...and teaches it nothing", s._audio_wanted, None)
check("...and stops no ring", s._audio_trigger.ring.stopped, 0)

print()
print("switching off:")
s = Stub()
s.note(False)
check("the ring is stopped", s.live.ring.stopped, 1)
check("the trigger is let go", s.listening, False)
check("the answer is remembered", s._audio_wanted, False)
# A CANDIDATE WAITING OUT ITS CONTEST WINDOW was heard before the
# switch was flipped and nothing else will ever flush it: the flush
# only happens on a tick, and there are about to be no more ticks.
check("a held swing is shipped first, not stranded", s._copy.flushes, 1)
# Idempotent: watch-status repeats the same answer about once a second.
s.note(False)
s.note(False)
check("repeating the answer stops nothing twice", s.live.ring.stopped, 1)
check("...and does not re-flush", s._copy.flushes, 1)

print()
print("switching back on:")
s.note(True)
check("it is rebuilt", s.builds, 1)
check("it is listening again", s.listening, True)
check("the answer is remembered", s._audio_wanted, True)
# The zones have to be re-read: which of them stand down for the ears
# depends on the trigger's own confirm_zones, and this is a new one.
check("the dwell-yield zones are re-checked", s.zone_checks, 1)
s.note(True)
check("repeating it does not rebuild", s.builds, 1)

print()
print("answered before the run loop built anything:")
# THE LIVE WATCHER POLLS FIRST. If the answer were latched here, the
# next poll would see the same value, return early, and the switch
# would never be applied at all.
s = Stub(capable=False, built=False)
s.note(False)
check("the answer is NOT latched", s._audio_wanted, None)
s.note(False)
check("...so a later poll still has work to do", s._audio_wanted, None)

print()
print("a rig that cannot listen at all:")
# config.yaml never asked for an audio trigger, or the camera is not on
# rtsp. There is nothing for the switch to start, and it must not try
# on every poll for the rest of the round.
s = Stub(capable=False, built=True)
s.note(True)
check("nothing is built", s.builds, 0)
check("the answer is latched so it stops asking", s._audio_wanted, True)
s.note(False)
check("...and the other answer too", s._audio_wanted, False)

print()
print("the ring will not restart (camera unreachable, session limit):")
# A TRANSIENT, not a verdict about the rig. Latching it would turn one
# unreachable moment into a round with no ears.
s = Stub(build_returns=None)
s.note(False)
check("off still works", s.listening, False)
s.note(True)
check("the rebuild was attempted", s.builds, 1)
check("it is still not listening", s.listening, False)
check("the answer is NOT latched, so the next poll retries",
      s._audio_wanted, False)
s.note(True)
check("...and it does retry", s.builds, 2)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
