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
    _on_the_ground, classify_balls,
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

    # ---- which ball left -------------------------------------------
    def ball(x, y):
        return {"x": x, "y": y, "frames": 3, "con": 90}

    def frame(*pts):
        return [(x, y, 6, 90) for x, y in pts]

    def verdict(balls, tail):
        gone, stayed = classify_balls([dict(b) for b in balls], tail)
        return True if gone else False if stayed else None

    # The plain cases.
    check("a ball that goes is a swing",
          verdict([ball(200, 204)], [frame(), frame(), frame()]), True)
    check("a ball that stays is not",
          verdict([ball(200, 204)], [frame((200, 204))] * 3), False)
    # THE +0.00 FRAME IS THE STRIKE and proves nothing either way: the
    # ball is being hit in it. Gone from the two frames after is a
    # struck ball whether or not it was still visible at impact.
    check("  still there AT the strike, gone after, is a swing",
          verdict([ball(200, 204)],
                  [frame((200, 204)), frame(), frame()]), True)
    # THE FRAMES AFTER THE STRIKE HAVE TO AGREE. Present in all of
    # them is a ball nobody touched; absent from all is one that
    # left; one of each is no answer. The detector can deliver that
    # now -- the reason a ball visible in all six frames came back
    # found in two was the relative threshold in _detections, fixed
    # at the source rather than papered over by accepting a single
    # sighting.
    check("  present in every later frame means it was not hit",
          verdict([ball(200, 204)], [frame(), frame((200, 204)),
                                     frame((200, 204))]), False)
    check("  gone then back again is no answer",
          verdict([ball(200, 204)],
                  [frame(), frame(), frame((200, 204))]), None)
    check("  and there then gone is no answer either",
          verdict([ball(200, 204)],
                  [frame(), frame((200, 204)), frame()]), None)
    # THREE BALLS ON THE TURF, which is the normal case on a tee and
    # the one that makes "the ball" the wrong question. Whichever
    # LEFT is the one that matters; the others never moved and never
    # had anything to do with it.
    THREE = [ball(200, 204), ball(240, 206), ball(280, 203)]
    check("one of three balls struck is still a swing",
          verdict(THREE, [frame((240, 206), (280, 203))] * 3), True)
    check("  and none of three struck is not",
          verdict(THREE,
                  [frame((200, 204), (240, 206), (280, 203))] * 3), False)
    # A BALL THAT MOVED A LITTLE IS THE SAME BALL. Jitter is 4 px.
    check("a ball 3 px off is the same ball",
          verdict([ball(200, 204)], [frame((203, 204))] * 3), False)
    # AND THE THRESHOLD THAT CAUSED ALL THIS. _detections scales its
    # bar off the brightest thing in the crop, which is right for a
    # ball in flight and wrong for one on a tee beside a golfer in a
    # white shirt. rel=0 thresholds on absolute local contrast.
    import numpy as _np
    from app.services.ball_vanish import _detections as _det
    _img = _np.full((120, 160), 60, _np.uint8)
    _img[60:64, 40:44] = 150          # the ball: contrast ~90
    _img[20:40, 110:150] = 255        # a white shirt, far brighter
    _near = lambda ds: any(abs(d[0]-42) <= 3 and abs(d[1]-62) <= 3
                           for d in ds)
    check("the relative bar loses a ball beside something bright",
          _near(_det(_img, [(0, 0, 160, 120)], rel=0.55)), False)
    check("  and the absolute bar keeps it",
          _near(_det(_img, [(0, 0, 160, 120)], rel=0.0)), True)
    check("  one 20 px away is not, so the first one left",
          verdict([ball(200, 204)], [frame((220, 204))] * 3), True)
    # NOTHING AFTER THE STRIKE AT ALL -> no answer, not a verdict.
    check("no frames after the strike is no answer",
          verdict([ball(200, 204)], []), None)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
