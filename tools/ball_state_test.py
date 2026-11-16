#!/usr/bin/env python3
"""The four answers, named, and the frames each one was read from.

`ball_in_frames` has always had four outcomes and `left` can only say
three of them: True, False, and None for both "nothing ball-like was
found" and "a ball, and no idea what it did". Callers reconstructed
the difference from `balls`, which reads like a count. `state` says it.

And `frames` says what it was read from. The counts report how many
specks died on each bar across the whole set; they cannot say that the
-1.20s frame was never read, which silently turns "the same spot in 2
of the first 3" into "in 2 of the 2 that could be read".

This builds synthetic frames -- a bright disc on grey -- so every
state is reachable on demand and the timing edges can be walked one
frame at a time.

    python3 tools/ball_state_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


import numpy as np  # noqa: E402
import cv2  # noqa: E402

from app.services.ball_vanish import (  # noqa: E402
    ball_in_frames, classify_balls, ADDRESS_JITTER_PX,
)

OFFS = (-1.20, -0.60, -0.20, 0.0, 0.20, 0.60)
W, H = 320, 240
BALL = (160, 170)


def frame(ball=True, at=BALL):
    """Grey turf, optional bright ball. Contrast is well over the bar."""
    f = np.full((H, W, 3), 60, np.uint8)
    # A little texture, so the detector is not working on a flat field.
    f[::7, ::7] = 72
    if ball:
        cv2.circle(f, at, 3, (245, 245, 245), -1)
    return f


def run(present, **kw):
    """`present` is six bools: was the ball drawn in that frame.

    None in place of a bool means the frame could not be read.
    """
    shots = [None if p is None else frame(bool(p)) for p in present]
    return ball_in_frames(shots, offsets=OFFS, **kw)


print("\n-- the ball is found at all, on synthetic frames")
r = run([1, 1, 1, 0, 0, 0])
check("a ball before and gone after is a departure",
      r["state"], "BALL_PRESENT_DISAPPEARED")
check("  and `left` still says so for the old callers", r["left"], True)
check("  at the spot it was drawn",
      r["spot"] and abs(r["spot"][0] - BALL[0]) <= 3
      and abs(r["spot"][1] - BALL[1]) <= 3, True)

print("\n-- and the other three states are reachable")
r = run([1, 1, 1, 1, 1, 1])
check("a ball that never moves REMAINED", r["state"],
      "BALL_PRESENT_REMAINED")
check("  and `left` is False, not None", r["left"], False)
r = run([0, 0, 0, 0, 0, 0])
check("an empty tee is NO_BALL_DETECTED", r["state"], "NO_BALL_DETECTED")
check("  and that is not a judgement about a ball", r["left"], None)
# ONE OF EACH IS NO ANSWER: present at +0.20, gone by +0.60.
r = run([1, 1, 1, 0, 1, 0])
check("after-frames that disagree are INCONCLUSIVE",
      r["state"], "INCONCLUSIVE")
check("  and it still reports where the ball sat",
      bool(r["spot"]), True)

print("\n-- a check that COULD NOT RUN is never NO_BALL")
# The distinction the spec asks for and the one that matters: an empty
# search is evidence, a search that did not happen is not.
r = run([1, 1, None, None, None, None])
check("too few readable frames is INCONCLUSIVE",
      r["state"], "INCONCLUSIVE")
check("  and says so", "needs the six frames" in r["why"], True)

print("\n-- the per-frame report says what was read")
r = run([1, 1, 1, 0, 0, 0])
fr = r["frames"]
check("one entry per frame", len(fr), 6)
check("  each named by its offset",
      [f["at"] for f in fr], [-1.2, -0.6, -0.2, 0.0, 0.2, 0.6])
check("  the first three are before the strike",
      [f["role"] for f in fr[:3]], ["before"] * 3)
check("  the +0.00 frame is the strike", fr[3]["role"], "strike")
check("  and the last two come after", [f["role"] for f in fr[4:]],
      ["after"] * 2)
# THE VOTE, which is the thing the counts could never show.
check("the strike frame never gets a vote", fr[3]["in_rule"], False)
check("  the after-frames do", [f["in_rule"] for f in fr[4:]],
      [True, True])
check("a ball is kept in the frames it was drawn in",
      [f["kept"] > 0 for f in fr[:3]], [True] * 3)
check("  and nothing is kept where none was drawn",
      [f["kept"] for f in fr[4:]], [0, 0])

print("\n-- an unread frame is reported as unread, not as empty")
r = run([None, 1, 1, 0, 0, 0])
fr = r["frames"]
check("the missing frame says read=False", fr[0]["read"], False)
check("  it gets no vote", fr[0]["in_rule"], False)
check("  and the ones that were read still answer",
      r["state"], "BALL_PRESENT_DISAPPEARED")
# A FRAME THAT WAS READ AND HELD NOTHING LOOKS DIFFERENT. Both report
# kept=0; only one of them was looked at.
r2 = run([0, 1, 1, 0, 0, 0])
check("an empty frame that WAS read says read=True",
      r2["frames"][0]["read"], True)
check("  so kept=0 means two different things, distinguishably",
      (fr[0]["read"], r2["frames"][0]["read"]), (False, True))


print("\n-- the strike frame is a SLOT, not 'the first one readable'")
# THE BUG THIS REPLACES. `late` used to be `tail_live[1:]` -- drop the
# first frame that could be READ, on the assumption it is +0.00. With
# +0.00 unreadable that dropped +0.20 instead, leaving the single
# +0.60 frame to decide, so "gone from every frame after the strike"
# became "gone from one frame" and an occlusion satisfied it.
_cl = [{"x": float(BALL[0]), "y": float(BALL[1]), "n": 3,
        "con": 99.0, "frames": 3}]
_at = [(float(BALL[0]), float(BALL[1]), 9, 99.0)]
# tail slots: +0.00 unread, +0.20 shows the ball, +0.60 does not.
_tail_live = [_at, []]          # the two that could be read
_late_by_slot = [_at, []]       # +0.20 and +0.60 -- both still vote
gone, stayed = classify_balls([dict(_cl[0])], _tail_live,
                              ADDRESS_JITTER_PX, late=_late_by_slot)
check("the ball is in one after-frame, so neither gone nor stayed",
      (len(gone), len(stayed)), (0, 0))
# The old behaviour, for contrast: drop the first READABLE frame.
gone_old, _ = classify_balls([dict(_cl[0])], _tail_live,
                             ADDRESS_JITTER_PX)
check("  where dropping the first readable frame called it GONE",
      len(gone_old), 1)
check("and ball_in_frames passes the slots",
      run([1, 1, 1, None, 1, 0])["state"], "INCONCLUSIVE")

print("\n-- THE TIMING WINDOW, walked one lag at a time")
# WHAT THIS IS FOR. The six tiles sit at fixed offsets around the
# AUDIO PEAK, and the first three are the ones searched for a ball at
# address. The peak is not the impact: it is the impact plus whatever
# the microphone, the encoder and the ring buffer add (the code says
# so in three places), and nothing ties that lag to these offsets.
#
# So walk it. `lag > 0` is a LATE peak -- the strike happened before
# the sound -- and `lag < 0` an EARLY one, which is what a pre-impact
# noise loud enough to win the peak would produce.
#
# The two directions are NOT symmetric, and the code only ever
# discusses the late one:
#
#   late  up to +0.59s   answers correctly
#   late  from +0.60s    NO_BALL_DETECTED -- wrong, but fails SAFE:
#                        "no judgement from the ball"
#   early to -0.20s      answers correctly
#   early -0.25..-0.60s  INCONCLUSIVE -- no answer, which is honest
#   early past -0.60s    BALL_PRESENT_REMAINED -- fails UNSAFE: a real
#                        swing reported as a ball nobody touched
#
# Pinned here because the offsets are a tuned constant that has
# already been changed twice for the JUDGE's benefit, with nothing to
# say it also moves this.
def _state_at(lag):
    _impact = -lag
    return run([t < _impact - 1e-9 for t in OFFS])["state"]


for _lag, _want in (
        (0.00, "BALL_PRESENT_DISAPPEARED"),
        (0.30, "BALL_PRESENT_DISAPPEARED"),
        (0.59, "BALL_PRESENT_DISAPPEARED"),
        # THE LATE EDGE. One frame later and only -1.20 holds a ball,
        # so "the same spot in 2 of the first 3" cannot be met.
        (0.60, "NO_BALL_DETECTED"),
        (1.00, "NO_BALL_DETECTED"),
        (-0.15, "BALL_PRESENT_DISAPPEARED"),
        (-0.20, "BALL_PRESENT_DISAPPEARED"),
        # THE EARLY SIDE GIVES UP FOUR TIMES SOONER, at a quarter of a
        # second: the ball is still sitting there in the +0.20 frame.
        (-0.25, "INCONCLUSIVE"),
        (-0.50, "INCONCLUSIVE"),
        # EXACTLY -0.60 PUTS THE IMPACT ON THE LAST FRAME, which that
        # frame does not show (the comparison is strict), so this is
        # still the honest no-answer. One hundredth further is not.
        (-0.60, "INCONCLUSIVE"),
        # AND THEN IT GETS IT CONFIDENTLY WRONG. This is the one worth
        # knowing about: a struck ball reported as one nobody touched.
        (-0.61, "BALL_PRESENT_REMAINED"),
        (-1.00, "BALL_PRESENT_REMAINED")):
    check("lag %+.2fs" % _lag, _state_at(_lag), _want)

check("the usable window is 0.20s early to 0.59s late",
      (_state_at(-0.20), _state_at(0.59)),
      ("BALL_PRESENT_DISAPPEARED", "BALL_PRESENT_DISAPPEARED"))
# WHICH WAY IT FAILS, asserted rather than described, because the
# asymmetry is the whole finding: too late and it declines to answer,
# too early and it answers wrongly.
check("  too late fails safe (declines to judge)",
      _state_at(0.80) in ("NO_BALL_DETECTED", "INCONCLUSIVE"), True)
check("  too early fails UNSAFE (a real swing reads as no swing)",
      _state_at(-0.80), "BALL_PRESENT_REMAINED")

print("\n-- and both new fields reach a screen")
# A STAGE THAT REPORTS NOTHING IS A STAGE NOBODY CAN CHECK, which is
# the whole reason these two exist. Added to the dict and not wired
# through, they would be exactly the dead keys this detector has been
# bitten by twice.
SA = (ROOT / "backend" / "app" / "services"
      / "swinganalysis.py").read_text(encoding="utf-8")
check("swinganalysis passes `state` out",
      'out["ball_state"] = bl.get("state")' in SA, True)
check("  and the per-frame report",
      'out["ball_frames"] = bl.get("frames")' in SA, True)
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("the test-cut entry carries both",
      'cut_entry["ball_state"]' in ADM
      and 'cut_entry["ball_frames"]' in ADM, True)
# A FAILED CHECK IS NOT A SEARCH THAT CAME BACK EMPTY, on this path
# either -- the except branch has to say so itself.
check("  and a thrown ball check says INCONCLUSIVE, not NO_BALL",
      'cut_entry["ball_state"] = "INCONCLUSIVE"' in ADM, True)
UI = (ROOT / "frontend" / "src" / "components"
      / "CutVerdicts.jsx").read_text(encoding="utf-8")
check("the screen names the state from `ball_state`",
      "c.ball_state" in UI, True)
for _st in ("BALL_PRESENT_DISAPPEARED", "BALL_PRESENT_REMAINED",
            "NO_BALL_DETECTED", "INCONCLUSIVE"):
    check("  it has a label for %s" % _st, _st in UI, True)
check("  and still reads the old fields for rows stored before it",
      "c.ball_left === true" in UI, True)
check("the screen draws the per-frame strip",
      "c.ball_frames" in UI, True)
check("  marking the frames that did not count",
      "f.in_rule" in UI, True)
check("  and the ones that could not be read",
      "f.read" in UI, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
