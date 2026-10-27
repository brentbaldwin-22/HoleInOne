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
from agent import tee_roi  # noqa: E402
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
        self._person_seen_at = __import__('collections').deque(
            maxlen=tee_mod._SIGHTING_MAXLEN)
        self._note_sighting = tee_mod.TeeAgent._note_sighting.__get__(self)
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
          tee_roi.seen_times(f._person_seen_at), [100.0, 100.5, 101.0])
    check("...each attributed to the zone they stood in",
          [z for _t, z in f._person_seen_at], [0, 0, 0])


def test_the_detection_loop_records_them_too():
    """THE BUG THIS EXISTS FOR.

    Only the RECORDING paths wrote sightings -- `_copy_stop_policy` and
    `_record_and_upload`, both of which run once a clip is already
    being made. Outside a recording the list stayed empty, and empty
    does not mean "no information" to the readers: it means "we looked
    and nobody was there".

    So the audio trigger, which asks "was anybody in a zone when that
    crack happened", could only ever be satisfied while the dwell
    happened to be recording. Measured on the rig 27 Oct: 146 impacts
    heard, 20 cut, the rest declined for "nobody in any zone" on a tee
    with a golfer standing on it.
    """
    import inspect

    f = Fake(INSIDE)
    f._note_sighting(100.0, INSIDE, ROI)
    f._note_sighting(100.5, INSIDE, ROI)
    check("the loop can record a sighting with nothing recording",
          tee_roi.seen_times(f._person_seen_at), [100.0, 100.5])
    check("...attributed to a zone",
          [z for _t, z in f._person_seen_at], [0, 0])

    # It must not be able to disturb the detector's decision.
    f._note_sighting(101.0, None, "not an roi")
    check("garbage is swallowed, not raised", len(f._person_seen_at), 3)

    # AND THE LOOP MUST ACTUALLY CALL IT. The method existing is not
    # the fix; the fix is the detection loop using it, which is what
    # was missing.
    src = inspect.getsource(tee_mod.TeeAgent.run)
    check("the detection loop calls it", "_note_sighting(" in src, True)
    check("...guarded by in_roi, so a sighting means a tee",
          "if in_roi:" in src, True)


def test_the_list_cannot_grow_for_ever():
    """Written 5/s for the life of the agent now, not just while
    recording -- so an unbounded list is a leak, and every reader
    walks it linearly."""
    f = Fake(INSIDE)
    for i in range(tee_mod._SIGHTING_MAXLEN + 500):
        f._note_sighting(float(i), INSIDE, ROI)
    check("it stops at the bound", len(f._person_seen_at),
          tee_mod._SIGHTING_MAXLEN)
    check("...keeping the NEWEST, which is what every reader wants",
          f._person_seen_at[-1][0], float(tee_mod._SIGHTING_MAXLEN + 499))


def test_a_person_outside_the_zone_is_not():
    f = Fake(OUTSIDE)
    stop = f.stop_policy()
    for t in (100.0, 100.5, 101.0):
        stop(t)
    check("nobody in the zone, nothing remembered",
          list(f._person_seen_at), [])


def test_no_frames_records_nothing():
    """A camera problem is not a sighting, and not an absence either."""
    f = Fake(INSIDE, frames=0)
    stop = f.stop_policy()
    check("it does not stop the clip", stop(100.0), None)
    check("and it claims no sighting", list(f._person_seen_at), [])


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
           test_the_detection_loop_records_them_too,
           test_the_list_cannot_grow_for_ever,
           test_a_person_outside_the_zone_is_not,
           test_no_frames_records_nothing,
           test_what_the_empty_list_did_to_the_swing_finder):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
