#!/usr/bin/env python3
"""Does the COPY engine remember seeing a person?

IT DID NOT, FOR AS LONG AS IT HAS EXISTED, and it cost every swing
swing_select could have found on a copy-engine rig.

swing_select refuses any motion peak with nobody near it -- on a
camera aimed down the hole, moving cloud scored a 4.72 "swing" in
footage containing no golfer at all. The sightings it checks against
live in `_person_seen_at`, and only the DECODE loop ever appended to
it. A copy rig ran its own detector in `_copy_stop_policy`, used the
answer to decide when to stop recording, and threw it away.

So the list stayed empty. And an empty list does not mean "no
information" to find_swings -- it means "we looked and nobody was
there", a distinction it is RIGHT to make and was being told wrongly.
Every peak that cleared the ratio was rejected, and swing_select could
never return a swing.

MEASURED ON HOLE 8, 20 OCT: 17 sessions, 0 swings found. In the twelve
where nothing cleared the 4.0 ratio there was nothing to reject. In
the other five, peaks DID clear it -- and all of them were thrown out
for nobody in the ROI, during sessions that a person standing in that
same ROI had triggered.

    python3 tools/copy_person_seen_test.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import tee as tee_mod  # noqa: E402
from agent.swingfind import find_swings  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


ROI = {"frame": {"w": 1920, "h": 1080},
       "boxes": [{"x": 800, "y": 400, "w": 400, "h": 300, "label": "back"}]}
INSIDE = (1000, 550)
OUTSIDE = (100, 100)


class Buffer:
    def __init__(self, frames=1):
        self._f = [(0.0, object()) for _ in range(frames)]

    def snapshot(self):
        return list(self._f)


class Detector:
    """Returns a fixed centroid, like the real one on a still scene."""

    def __init__(self, centroid):
        self.centroid = centroid

    def detect(self, frame, **kw):
        return self.centroid


class Fake:
    """Only what _copy_stop_policy actually touches."""

    def __init__(self, centroid, frames=1):
        self.buffer = Buffer(frames)
        self._roi_lock = threading.Lock()
        self.roi = ROI
        self._person_seen_at: list = []
        self._det = Detector(centroid)

    def stop_policy(self, det_period=0.0):
        return tee_mod.TeeAgent._copy_stop_policy(
            self, self._det, no_person_timeout=30.0, det_period=det_period)


def test_a_person_in_the_zone_is_remembered():
    f = Fake(INSIDE)
    stop = f.stop_policy()
    for t in (100.0, 100.5, 101.0):
        check(f"  t={t}: keeps recording", stop(t), None)
    check("the sightings are kept", len(f._person_seen_at), 3)
    check("and they are the times they happened",
          f._person_seen_at, [100.0, 100.5, 101.0])


def test_a_person_outside_the_zone_is_not():
    f = Fake(OUTSIDE)
    stop = f.stop_policy()
    for t in (100.0, 100.5, 101.0):
        stop(t)
    check("nobody in the zone, nothing remembered", f._person_seen_at, [])


def test_no_frames_records_nothing():
    """A camera problem is not a sighting, and not an absence either."""
    f = Fake(INSIDE, frames=0)
    stop = f.stop_policy()
    check("it does not stop the clip", stop(100.0), None)
    check("and it claims no sighting", f._person_seen_at, [])


def test_what_the_empty_list_did_to_the_swing_finder():
    """The end-to-end consequence, on a signal with a real swing in it.

    This is the shape of the five sessions on hole 8 that had peaks and
    found nothing.
    """
    import math
    rate, dur = 20.0, 60.0
    n = int(dur * rate)
    times = [i / rate for i in range(n)]
    motion = []
    for t in times:
        dt = t - 30.0
        motion.append(1.0 + 5.0 * math.exp(-(dt * dt) / (2 * 0.2 ** 2)))
    check("control, with sightings the swing is found",
          len(find_swings(times, motion, duration_sec=dur,
                          person_times=[29.0])), 1)
    check("control, with the gate off it is found",
          len(find_swings(times, motion, duration_sec=dur,
                          person_times=None)), 1)
    check("but an EMPTY sighting list rejects it",
          len(find_swings(times, motion, duration_sec=dur,
                          person_times=[])), 0)
    # AND THE REPORT NOW SAYS WHICH IT WAS. "0 rejected" and "rejected
    # because we had nothing to check against" read identically in a
    # log, and that is what made this invisible.
    dbg: dict = {}
    find_swings(times, motion, duration_sec=dur, person_times=[], debug=dbg)
    check("the report says the list was empty", dbg["n_person_times"], 0)
    check("and that it rejected on that", dbg["rejected_no_person"], 1)
    dbg2: dict = {}
    find_swings(times, motion, duration_sec=dur, person_times=None,
                debug=dbg2)
    check("control, gate off reports no list at all",
          dbg2["n_person_times"], None)


for fn in (test_a_person_in_the_zone_is_remembered,
           test_a_person_outside_the_zone_is_not,
           test_no_frames_records_nothing,
           test_what_the_empty_list_did_to_the_swing_finder):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
