#!/usr/bin/env python3
"""Does the swing-judge prompt describe THIS course's camera?

A vision prompt is code: it is the specification the model is held to,
and a wrong one produces confident, well-reasoned, wrong answers. The
first version described a side-on view -- "a fan or arc of thin
streaks sweeping around/above the figure" -- and the tee camera stands
BEHIND the hitting area. Down the line a club's arc is foreshortened
into a narrow vertical plume, so the model looked for a fan, correctly
reported there wasn't one, and rejected three plain swings in a row
(uploads 1041, 1042, 1043), each reason naming the missing fan.

Nothing here calls the API. It asserts the prompt still says the
things that were learned the expensive way, so a later edit cannot
quietly reinstate the assumption.

    python3 tools/swing_prompt_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.ai_tracer import (  # noqa: E402
    _JUDGE_SWING_HEAT_PROMPT as P,
)

FAILED = []


def says(label, *needles):
    low = P.lower()
    ok = all(n.lower() in low for n in needles)
    print("  %-62s %s" % (label, "ok" if ok else "FAIL: %s" % (needles,)))
    if not ok:
        FAILED.append(label)


print("the prompt describes the camera this course actually has:")
says("it says the camera is behind the hitting area", "behind")
says("...and names the view as not side-on", "not side-on")

print()
print("it describes the club as this angle renders it:")
says("a narrow vertical plume, not a fan", "narrow vertical plume")
says("...explicitly foreshortened", "foreshorten")
says("...possibly only a few pixels wide", "few pixels")
# THE SENTENCE THAT UNDOES THE OLD FAILURE. Without it the model can
# still reason "I see no fan, therefore no swing" from the negatives.
says("a plume alone is enough, with no fan anywhere",
     "even with no fan shape")

print()
print("it handles the golfer being HIDDEN behind somebody else:")
# SECOND ATTEMPT. The first one let in as much as it let out: told
# that a plume ALONE was a swing and that a ball trail counted as
# evidence, the model accepted row 9 at 43.26s -- two people moving
# together, reasoned as "motion extending upward consistent with a
# club" -- and STILL refused row 8, now saying the plume was absent on
# a picture that has a bright one.
#
# So the clause is narrower: the plume is the deciding feature and
# the only thing that substitutes for the body, and it is described
# tightly enough that a body-shaped mass cannot pass as one.
# THE SAME SHAPE OF BUG AS THE FAN, A SECOND TIME. Every positive
# description in the first version required a compact hot body, so
# there was no route to "true" without one -- and on a tee where
# everybody stands in a line looking down the hole, a playing partner
# in front of the swinger is routine. A person standing still carries
# no heat, so they do not show up as a blob; they just block the body
# behind them and the compact figure never forms.
#
# Upload 1310's neighbour, row 8 at 36.09s: refused "medium", reason
# "two figures standing with clubs grounded; heat is scattered without
# a compact bright body and vertical club plume" -- on a picture with
# a bright vertical plume over the right-hand golfer's head AND a
# dotted ball trail climbing away from it. The model applied the
# rubric it was given, correctly.
says("it says a partner blocks the swinger", "block the swinger behind")
says("...and names who", "playing partner")
says("...explaining why a still bystander is invisible, not a blob",
     "standing still carries no heat")
says("a plume alone is a swing, with no hot body at all",
     "a plume is a swing even with no compact hot body")
says("...and finding it is the stated job", "deciding feature")

print()
print("...without letting a body-shaped blob pass as a plume:")
# WHAT ROW 9 WAS. Two people moving together read as "a hot figure
# with motion extending upward", which the loose version admitted.
says("a plume is narrow, tall and separate", "narrow and tall and separate")
says("...far taller than wide", "taller than it is wide")
says("...clearly above head height", "above head height")
says("...not part of a body-shaped mass", "distinct from any body-shaped")
says("a wide bright patch is explicitly NOT one", "is not a plume")
says("...nor is a figure that merely extends upward",
     "merely extends a little upward")
says("...and the case is named", "two people moving together")
# THE SENTENCE THAT UNDOES THE FAILURE, as "even with no fan shape"
# undid the one before it. Without it the model can still reason "no
# compact figure, therefore no swing" from the positives alone.
says("it forbids refusing on a missing body by itself",
     "do not answer false merely because no compact bright figure")
says("...while still saying when false IS the answer",
     "say false when there is no narrow plume")

print()
print("and the ball trail is NOT used as evidence:")
# MEASURED, NOT ASSUMED. Asked to weigh it, the model reported a trail
# missing on a picture that had one and present on a picture that did
# not. A signal that unreliable makes the verdict worse, so the prompt
# says so rather than leaving a later reader to re-add it.
says("its absence still means nothing", "absence means nothing")
says("...and it is not to be hunted for", "do not go looking for it")
says("...with the reason recorded", "not evidence here either way")

print()
print("it still rejects the things that are not swings:")
says("walking", "walking")
says("...distinguished by travel, not by absence of a fan",
     "along the direction of travel")
says("bending to tee up", "bending to tee")
says("foliage shimmer", "shimmer")
says("bystanders carrying no heat are not the subject", "standing around")
# ...but the negative must not re-create the hole the positives just
# closed: "judge the figure that IS hot" was the old wording, and on
# an occluded swing there is no hot figure to judge.
says("...but they may be in front of the one who swung",
     "standing in front")
says("...which sends the reader back to the plume",
     "judge the plume as well as the figure")

print()
print("and it still returns the shape the caller parses:")
says("is_swing", '"is_swing": true|false')
says("confidence", "confidence")
says("reason", "reason")

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
