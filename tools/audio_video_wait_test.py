#!/usr/bin/env python3
"""A strike heard now wants video that does not exist yet.

THE BUG, FROM NINETY MINUTES OF A LIVE TEE RIG'S JOURNAL. Every single
sound was refused, and every refusal read the same way:

  heard x7.3 at 1793288861.080 — not cutting (the video ring holds
  1793288708.0..1793288862.0, this needs 1793288856.6..1793288870.1)

The sound is at ...861. The ring ends at ...862 -- which is NOW. The
clip wants ...870, nine seconds into the future, because the window is
4s before the strike to 9s after it. At the instant a strike is heard
that footage has not been recorded. One `or` tested the whole window
against the ring and refused the lot.

It only ever cut by accident. The ten cuts that rig did make all came
while the audio pipeline was running 152 seconds BEHIND the video, so
the "future" was already on disk -- and they stopped the moment a
restart put the audio back in real time. The CPU load of the dwell
recording clips is the most likely source of that lag, which is why
switching the dwell off looked like it had silenced the ears.

So the two halves of that test are separated. Footage that AGED OUT is
gone and refusing is right. Footage NOT YET RECORDED is a wait.

    python3 tools/audio_video_wait_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))

from agent import audiotrigger as AT  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class Stub:
    """Only what _video_wait and _judge_pending touch."""

    def __init__(self, first, last, before=4.0, after=9.0):
        self.span = (first, last)
        self.before_sec = before
        self.after_sec = after
        self.video_span = lambda: self.span
        self._pending = []
        self.heard = 0
        self.judged = []

    def _judge(self, hit):
        self.judged.append(float(hit["at"]))
        self.heard += 1
        return {"at": float(hit["at"]), "cut": True}

    def _video_wait(self, at):
        return AT.AudioTrigger._video_wait(self, at)

    def wait(self, at):
        return self._video_wait(at)

    def drain(self):
        return AT.AudioTrigger._judge_pending(self)


def hit(at):
    return {"at": at, "ratio": 7.3}


# The rig's own numbers, to the decimal.
AT_SOUND = 1793288861.080
RING_FIRST, RING_NOW = 1793288708.0, 1793288862.0

print("the ninety minutes of refusals:")
s = Stub(RING_FIRST, RING_NOW)
check("a strike heard in real time waits for its video",
      s.wait(AT_SOUND), "wait")
# ...and the window it was asking for, which the log printed.
check("the clip it wants ends 9s after the strike",
      round(AT_SOUND + s.after_sec, 1), 1793288870.1)
check("...which is past the end of the ring",
      AT_SOUND + s.after_sec > RING_NOW, True)

print()
print("the accident that made it work at all:")
# THE TEN CUTS THAT RIG DID MAKE. The audio had fallen ~130s behind the
# video, so by the time a strike was judged its whole window -- future
# included -- was already recorded. This is the first of them:
#
#   13:09:05 cutting 1793293606.9..1793293620.4s around 1793293611.424
#
# The ring holds about 154s, so at that moment it covered roughly
# ...590..., ...744 and the window sat comfortably inside it.
s = Stub(1793293590.0, 1793293744.0)
check("a strike judged ~130s late has all its footage",
      s.wait(1793293611.424), None)

# AND THE EDGE THE LAG WAS RIDING. Thirty seconds later a strike at
# ...621.9 was refused, because by then the ring's START had advanced
# past the beginning of its clip:
#
#   13:09:35 heard x4.8 at 1793293621.910 — not cutting (the video ring
#   holds 1793293620.0..1793293774.0, this needs 1793293617.4..)
#
# Which is the other half of why the lag was never a fix: it put every
# strike within seconds of aging out of the video entirely.
s = Stub(1793293620.0, 1793293774.0)
check("...but a lag that big ages strikes out of the video",
      s.wait(1793293621.910), "gone")

print()
print("waiting, then cutting:")
# ON THE CURRENT CLOCK, because the give-up timer below measures how
# long a strike has really been waiting. A fixed epoch from the rig's
# journal is years stale and is given up on instantly -- correctly.
import time as _time  # noqa: E402

_NOW = _time.time()
_STRIKE = _NOW - 1.0
s = Stub(_NOW - 154.0, _NOW)
check("a strike heard a second ago waits", s.wait(_STRIKE), "wait")
s._pending = [hit(_STRIKE)]
check("while the video is short, nothing is judged", s.drain(), [])
check("...and the strike is still held", len(s._pending), 1)
check("...and it is not counted as heard yet", s.heard, 0)
# Nine seconds later the camera has recorded it.
s.span = (_NOW - 154.0, _STRIKE + 9.5)
_out = s.drain()
check("once the video arrives it is judged", len(_out), 1)
check("...the right strike", s.judged, [_STRIKE])
check("...and it is no longer held", len(s._pending), 0)

print()
print("footage that aged out is a refusal, not a wait:")
# The ring has rolled past the start of the clip. No waiting brings
# that back, so it must go through _judge and be refused there.
s = Stub(AT_SOUND - 1.0, AT_SOUND + 20.0)
check("a strike older than the ring is gone", s.wait(AT_SOUND), "gone")
s._pending = [hit(AT_SOUND)]
check("...so it is judged rather than held forever", len(s.drain()), 1)
check("...and not left pending", len(s._pending), 0)

print()
print("in the order they were heard:")
# THE REFRACTORY IS SEQUENTIAL, so judging a later strike before an
# earlier one would let the later one silence the swing that came
# first.
s = Stub(RING_FIRST, AT_SOUND + 60.0)
s._pending = [hit(AT_SOUND), hit(AT_SOUND + 7.0), hit(AT_SOUND + 14.0)]
s.drain()
check("oldest first", s.judged,
      [AT_SOUND, AT_SOUND + 7.0, AT_SOUND + 14.0])

print()
print("a ring that has stalled rather than lagged:")
# Waiting forever would hide the real problem: a camera that has
# stopped recording. Given up on, counted as heard, and said out loud.
s = Stub(RING_FIRST, RING_NOW)
_old = _time.time() - (AT._VIDEO_WAIT_MAX_SEC + 5.0)
s._pending = [hit(_old)]
check("the strike is dropped", len(s.drain()), 0)
check("...and not held", len(s._pending), 0)
check("...but it IS counted as heard", s.heard, 1)
# One still inside the window is kept.
s = Stub(RING_FIRST, RING_NOW)
s._pending = [hit(_time.time() - 2.0)]
s.drain()
check("a strike heard two seconds ago keeps waiting",
      len(s._pending), 1)

print()
print("no video ring configured at all:")
s = Stub(None, None)
s.video_span = None
check("nothing to wait for", s.wait(AT_SOUND), None)
s = Stub(None, None)
check("an empty ring is left to _judge to report", s.wait(AT_SOUND), None)

print()
print("the wiring:")
_src = (Path(__file__).resolve().parents[1] / "pi-agent" / "agent"
        / "audiotrigger.py").read_text(encoding="utf-8")
check("tick holds a strike whose video is not there",
      'if self._video_wait(float(hit["at"])) == "wait":' in _src, True)
check("...and drains the held ones first",
      "out = self._judge_pending()" in _src, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
