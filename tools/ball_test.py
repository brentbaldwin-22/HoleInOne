"""The ball-at-address rule's filters, without a video.

THE OPERATOR'S REQUIREMENT, 8 Nov: "the ball needs to be clearly
identified - i don't want a white shoe or skin identified as a ball."
Two filters carry that, and both were written against things the
detector actually picked on the frames from that report: a golfer's
white GLOVE at his hip, holding still across all six frames, and a
bright spot in the BACKGROUND a fifth of the way down a crop whose
golfer stood at four fifths.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ball_vanish import (                 # noqa: E402
    BALL_BAND_ABOVE_FEET, BALL_BAND_BELOW_FEET, _on_a_person,
    _on_the_ground,
)

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok' if ok else 'FAIL'}    {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def main() -> int:
    # The golfer from the 8 Nov crop: box [80, 26, 94, 208], so their
    # feet are at y=234 and they are 208 px tall.
    GOLFER = [{"box": [80, 26, 94, 208]}]

    # ---- anything on a body is not a ball -------------------------
    # The glove, at his hip. Inside the box, so out.
    check("a glove on the golfer is not a ball",
          _on_a_person(141, 119, GOLFER), True)
    # His shoe, at the bottom of the same box.
    check("  nor is a shoe", _on_a_person(120, 228, GOLFER), True)
    # His cap.
    check("  nor is a white cap", _on_a_person(125, 30, GOLFER), True)
    # The actual ball, on the turf beside him and outside the box.
    check("a ball on the turf is not on a person",
          _on_a_person(201, 204, GOLFER), False)
    # NOBODY DETECTED is not a reason to refuse everything.
    check("with nobody detected nothing is on a person",
          _on_a_person(201, 204, []), False)

    # ---- and a ball is on the ground ------------------------------
    # Feet at 234, 208 tall: the band runs 234-0.35*208 = 161 up to
    # 234+0.15*208 = 265.
    lo = 234 - BALL_BAND_ABOVE_FEET * 208
    hi = 234 + BALL_BAND_BELOW_FEET * 208
    check("the band is anchored on the feet", (round(lo), round(hi)),
          (161, 265))
    check("the real ball is in the band", _on_the_ground(204, GOLFER), True)
    # The background spot that got picked before this filter existed.
    check("a bright spot in the background is not",
          _on_the_ground(35, GOLFER), False)
    # A ball teed well back still counts; one in the sky does not.
    check("  a ball teed further away still counts",
          _on_the_ground(170, GOLFER), True)
    check("  a gull overhead does not", _on_the_ground(60, GOLFER), False)
    # Below the near edge of the tee is nothing at all.
    check("  and neither is something under the camera",
          _on_the_ground(290, GOLFER), False)
    # ABSENCE OF PEOPLE IS NOT A REFUSAL: with nobody detected there
    # is no ground line to measure against, and the person filter is
    # the one doing the heavy lifting anyway.
    check("with nobody detected every height is allowed",
          _on_the_ground(35, []), True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
