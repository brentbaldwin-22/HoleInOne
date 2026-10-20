#!/usr/bin/env python3
"""The dwell has to survive a golfer bending to tee up.

Detection runs at 5 Hz and the dwell clock used to reset on a SINGLE
missed sighting. At 2 seconds that is ten consecutive detections and
it mostly got away with it. A ten-second dwell is FIFTY, with no gaps
-- and the config's own notes say that does not happen: "a golfer
bending to tee up, turning away, or stepping aside briefly makes the
person detector drop out for a few seconds". The STOP condition was
built generous for exactly that. The start condition was not.

So the clock now survives a short gap. The trigger still requires them
to be in the zone at the moment it fires, because a timer that
outlives the person would let a cart drive-by trigger on its way out.

Drives the real `Dwell`, which was pulled out of the loop for exactly
this reason: the loop owns a camera, a detector and a capture thread,
and none of those are what is being decided here. A test that modelled
the rule instead would be a second copy of it, agreeing with the first
by hand -- and neither copy killable.

    python3 tools/dwell_grace_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.tee import Dwell  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def run(seen, dwell, grace, hz=5.0, d=None):
    """`seen` is a list of bools, one per detection tick at `hz`.

    Returns the tick index the real Dwell fires on, or None.
    """
    d = d or Dwell(dwell, grace)
    for i, in_roi in enumerate(seen):
        if d.update(i / hz, in_roi):
            return i
    return None


def ticks(pattern, hz=5.0):
    """'5s on, 1s off, 10s on' as a bool list at `hz`."""
    out = []
    for secs, on in pattern:
        out += [on] * int(round(secs * hz))
    return out


def test_a_clean_ten_seconds_fires_at_ten():
    t = ticks([(20, True)])
    i = run(t, dwell=10.0, grace=3.0)
    check("fires once the dwell is up", i is not None, True)
    check("and at ten seconds, not before", round(i / 5.0), 10)


def test_bending_to_tee_up_does_not_restart_the_clock():
    """Two seconds of the detector losing them, mid-dwell."""
    t = ticks([(4, True), (2, False), (10, True)])
    i = run(t, dwell=10.0, grace=3.0)
    check("it still fires", i is not None, True)
    check("and the blink cost it nothing", round(i / 5.0), 10)
    # CONTROL: with no grace at all -- the old behaviour -- that same
    # two-second blink throws away four seconds of credit, and in the
    # sixteen seconds this golfer is actually there the dwell is never
    # met. Not "later": never.
    check("control, with no grace it never fires at all",
          run(t, dwell=10.0, grace=0.0), None)


def test_a_long_absence_does_start_it_over():
    """Grace is for a blink, not for leaving."""
    t = ticks([(6, True), (5, False), (14, True)])
    i = run(t, dwell=10.0, grace=3.0)
    # They are back at 11s and the clock starts there, so 21s.
    check("the clock restarts after the gap", round(i / 5.0), 21)
    # CONTROL: an unlimited grace carries the six seconds of credit
    # across the absence, and it fires the moment they reappear --
    # ten seconds early, on a dwell that was never actually served.
    check("control, an unlimited grace fires on their return",
          round(run(t, dwell=10.0, grace=99.0) / 5.0), 11)


def test_a_drive_by_cannot_trigger_on_its_way_out():
    """The reason firing requires them to be visible NOW.

    Somebody crosses the zone for a second and goes. The grace keeps
    the clock alive for three more seconds — and in that window the
    elapsed time passes the dwell. Without the in-zone test it would
    trigger on an empty tee.
    """
    t = ticks([(1, True), (30, False)])
    check("a one-second crossing never fires",
          run(t, dwell=2.0, grace=3.0), None)
    # CONTROL: the clock really IS still running while they are gone --
    # which is what would fire on an empty tee if the in-zone test were
    # dropped, and is why it is there.
    d = Dwell(2.0, 3.0)
    for i in range(15):
        d.update(i / 5.0, i < 5)
    check("control, the clock outlives them by the grace",
          d.first_seen is not None, True)
    check("control, and is past the dwell while the tee is empty",
          (14 / 5.0) - d.first_seen >= 2.0, True)


def test_the_old_two_second_behaviour_is_unchanged():
    """Nothing about a continuous two-second dwell moves."""
    t = ticks([(5, True)])
    check("still fires at two seconds",
          round(run(t, dwell=2.0, grace=3.0) / 5.0), 2)


def test_a_split_re_arms_rather_than_restarting():
    """A clip hitting the length cap must not leave a hole.

    The golfer has been there two minutes. Making them serve the dwell
    again puts a gap the length of the dwell between two clips of the
    same person — at ten seconds that is wider than the five-second
    pre-roll, so a swing landing in it is simply gone.
    """
    d = Dwell(10.0, 3.0)
    d.arm(100.0)
    check("the next tick fires immediately", d.update(100.2, True), True)
    # CONTROL: restarting the clock instead makes them wait the full
    # ten seconds, which is the hole.
    e = Dwell(10.0, 3.0)
    e.update(100.0, True)
    check("control, a restart does not fire for ten seconds",
          e.update(100.2, True), False)
    check("control, it fires at a hundred and ten",
          e.update(110.0, True), True)


def test_the_countdown_is_reportable():
    """So a log can say "recording in 7s" rather than nothing at all."""
    d = Dwell(10.0, 3.0)
    check("nobody being timed", d.waiting_for(100.0), None)
    d.update(100.0, True)
    check("ten to go at the start", d.waiting_for(100.0), 10.0)
    check("three to go seven seconds in", d.waiting_for(107.0), 3.0)
    check("never negative", d.waiting_for(200.0), 0.0)


for fn in (test_a_clean_ten_seconds_fires_at_ten,
           test_bending_to_tee_up_does_not_restart_the_clock,
           test_a_long_absence_does_start_it_over,
           test_a_drive_by_cannot_trigger_on_its_way_out,
           test_the_old_two_second_behaviour_is_unchanged,
           test_a_split_re_arms_rather_than_restarting,
           test_the_countdown_is_reportable):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
