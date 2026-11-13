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
# THE FAN IS STILL SAID NOT TO BE REQUIRED, which is what stops the
# model reasoning "I see no fan, therefore no swing" from the
# negatives. This used to be checked by pinning one sentence:
#
#     "A bright compact figure with a thin plume above it IS a swing,
#      even with no fan shape anywhere in the picture."
#
# 9f550ba removed it ON PURPOSE, 7 Nov. It was written as a
# SUFFICIENCY claim, and sufficiency over "a blob with an edge" is a
# very low bar -- it is the line that passed the 150x199 near-square
# orange mass on 5 Nov, described back as "a compact hot figure with a
# cooler plume extending upward", which was a fair reading of that
# picture against that sentence. The judge was obeying its
# instructions.
#
# So this now checks the thing that was meant to survive rather than
# the words it was once carried in.
says("the fan is explicitly not what to look for", "not a wide sweeping fan")
says("...because the arc points away and is foreshortened",
     "foreshorten")

# AND THE SUFFICIENCY CLAIM MUST NOT COME BACK. The opposite guard,
# and the one with a false positive behind it rather than a false
# negative. Re-adding it is a prompt edit that looks like a kindness
# and costs precision; four attempts at this wording have gone in and
# three were reverted the same evening.
_low = P.lower()
_resurrected = ("even with no fan shape" in _low
                or "is a swing, even with" in _low)
print("  %-62s %s" % ("and the plume is not called proof on its own",
                      "ok" if not _resurrected else "FAIL"))
if _resurrected:
    FAILED.append("the plume sufficiency claim came back")

print()
print("it still rejects the things that are not swings:")
says("walking", "walking")
says("...distinguished by travel, not by absence of a fan",
     "along the direction of travel")
says("bending to tee up", "bending to tee")
says("foliage shimmer", "shimmer")
says("bystanders carrying no heat are not the subject", "standing around")

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
