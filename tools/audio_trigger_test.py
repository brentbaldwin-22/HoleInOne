#!/usr/bin/env python3
"""The audio trigger: a crack AND a golfer on that tee.

The two signals exist because neither works alone on this course.
Motion found 13 of 20 swings at its best setting with no stable pair;
sound cannot tell whose swing it is, and at ratio 8 it clears talking
by only about 1.3x. The zone check is what makes that thin margin
survivable -- a false peak has to coincide with somebody standing in
the named zone before it costs anything.

What is pinned down here:

  * both signals required, and each one alone rejected
  * None zones ("not asking") is not [] ("these zones", naming none)
  * a stopped person detector does NOT gate -- a broken detector must
    not silently stop clips
  * a sound whose footage has aged out of the video ring is reported
    as THAT, not as "no swing"
  * a restart starts at the live edge, so the window is not replayed
    and already-uploaded swings re-reported
  * shadow mode decides everything and cuts nothing

    python3 tools/audio_trigger_test.py
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent.audiotrigger import AudioTrigger  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-12s %s" % (label, repr(got),
                               "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


class FakeSeg:
    def __init__(self, name):
        self.name = name

    def read_bytes(self):
        return b"\0" * 32000


class FakeRing:
    """Names carry the time, exactly as audiocap's do."""

    def __init__(self, names):
        self._names = list(names)

    def segments(self):
        return [FakeSeg(n) for n in self._names]


class FakeListener:
    """Emits scripted hits instead of analysing anything."""

    def __init__(self, script):
        self.script = dict(script)   # {segment_name: [hit, ...]}
        self.last = None

    def feed(self, pcm, ts):
        return self.script.pop(self.last, [])


def build(hits_at, *, zones=None, sightings=(), span=None, shadow=True,
          seen_is_none=False):
    names = ["aud-20261026-21%04d.pcm" % i for i in range(10)]
    ring = FakeRing(names)
    li = FakeListener({})
    cuts = []

    def feed(pcm, ts):
        return li_hits.pop(0) if li_hits else []
    li_hits = [[] for _ in names]
    for idx, h in hits_at:
        li_hits[idx] = [h]
    li.feed = feed

    t = AudioTrigger(
        ring, li,
        sightings=(lambda: None) if seen_is_none else (lambda: list(sightings)),
        video_span=(lambda: span) if span is not None else None,
        confirm_zones=zones, confirm_tolerance_sec=6.0,
        shadow=shadow, on_cut=cuts.append)
    t.tick()                      # first tick: adopt the live edge
    ring._names = names + ["aud-20261026-22%04d.pcm" % i for i in range(10)]
    li_hits[:] = [[] for _ in range(10)]
    for idx, h in hits_at:
        li_hits[idx] = [h]
    return t, t.tick(), cuts


HIT = {"at": 1000.0, "ratio": 22.0, "attack_sec": 0.008}

print("both signals, or nothing:")
d = build([(1, HIT)], zones=[0], sightings=[(999.6, 0)])[1]
check("crack + golfer on the back tee", d[0]["confirmed"], True)

d = build([(1, HIT)], zones=[0], sightings=[(999.6, 1)])[1]
check("crack, but the golfer is on the FORWARD tee", d[0]["confirmed"], False)

d = build([(1, HIT)], zones=[0], sightings=[])[1]
check("crack, nobody anywhere", d[0]["confirmed"], False)

d = build([(1, HIT)], zones=[0], sightings=[(900.0, 0)])[1]
check("golfer on that tee, but 100s earlier", d[0]["confirmed"], False)

print()
print("None is not []:")
d = build([(1, HIT)], zones=None, sightings=[(999.6, 1)])[1]
check("None = any zone will do", d[0]["confirmed"], True)
d = build([(1, HIT)], zones=[], sightings=[(999.6, 0)])[1]
check("[] names no zone and can never pass", d[0]["confirmed"], False)

print()
print("a stopped detector must not gate:")
d = build([(1, HIT)], zones=[0], seen_is_none=True)[1]
check("sightings is None -> not gated", d[0]["confirmed"], True)

print()
print("footage that has aged out is reported as THAT:")
d = build([(1, HIT)], zones=[0], sightings=[(999.6, 0)],
          span=(998.0, 1002.0))[1]
check("ring too short", d[0]["confirmed"], False)
check("...and says so", "video ring holds" in (d[0]["why"] or ""), True)
d = build([(1, HIT)], zones=[0], sightings=[(999.6, 0)],
          span=(900.0, 1100.0))[1]
check("ring covers it", d[0]["confirmed"], True)

print()
print("shadow decides but does not cut:")
t, d, cuts = build([(1, HIT)], zones=[0], sightings=[(999.6, 0)], shadow=True)
check("confirmed", d[0]["confirmed"], True)
check("...but cut is False", d[0]["cut"], False)
check("...and nothing was handed to on_cut", len(cuts), 0)

t, d, cuts = build([(1, HIT)], zones=[0], sightings=[(999.6, 0)], shadow=False)
check("live: cut is True", d[0]["cut"], True)
check("...and on_cut was called once", len(cuts), 1)

print()
print("a restart starts at the live edge:")
ring = FakeRing(["aud-20261026-21%04d.pcm" % i for i in range(5)])
li = FakeListener({})
li.feed = lambda pcm, ts: [HIT]
t = AudioTrigger(ring, li, sightings=lambda: [(999.6, 0)], confirm_zones=[0])
first = t.tick()
check("the first tick replays nothing", len(first), 0)
ring._names.append("aud-20261026-210005.pcm")
check("...and the next new segment is judged", len(t.tick()), 1)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
