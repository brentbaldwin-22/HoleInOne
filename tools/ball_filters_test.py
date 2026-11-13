#!/usr/bin/env python3
"""When no ball is found, say which test refused it.

THE FIELD QUESTION, off a Test cut whose six tiles show a golfer at
address, a full backswing and a follow-through in bright sun: "i
wonder why no ball identified here."

The answer it gave was "no ball clearly in the same spot in 2 of the
first 3 frames", which names none of the four tests a bright speck has
to pass to be called a ball:

    area <= cap        scaled to the golfer's height
    contrast >= 30     absolute, not relative
    not on a person    the box, padded 4% wide and 6% tall
    on the ground      feet -0.18h .. feet +0.02h

On a clip where the golfer, the turf and the swing are all plainly
visible, that sentence leaves nothing to do but guess at a constant.

AND THE GUESS WOULD HAVE BEEN WRONG ANYWAY, which is the point. The
two ground tests overlapped almost exactly: `_on_the_ground` REQUIRES
the ball to be at foot level, and `_on_a_person` REJECTED everything
within the stance PLUS its padding at that height -- which is where a
ball teed off the lead heel sits.

`SHOE_INSET_FRAC` is the answer to that, and the rest of this file
measures it: at ground level the sides come IN rather than going out,
so a speck has to be properly within the stance to be a shoe. The
middle of the stance, where every shoe the detector has actually
picked sat, is refused exactly as before.

    python3 tools/ball_filters_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    import numpy as np
    import cv2
    from app.services.ball_vanish import (
        ball_in_frames, _on_a_person, _on_the_ground,
        PERSON_PAD_W, PERSON_PAD_H, SHOE_INSET_FRAC,
        BALL_BAND_ABOVE_FEET, BALL_BAND_BELOW_FEET)

    # ── THE OVERLAP, MEASURED ───────────────────────────────────────
    #
    # A golfer roughly the scale of the reported tiles: 90 wide, 400
    # tall, feet (the box's bottom-centre) at y=400.
    bx, bw, bh = 155, 90, 400
    feet_y = bh
    person = [{"box": [bx, 0, bw, bh]}]
    anchor = (feet_y, bh)

    # AS THE REAL RULE ASKS IT. Both callers test the ground first and
    # then pass `at_ground`, so a check written without it measures a
    # path the rule no longer takes.
    def _kept(x, y):
        return (_on_the_ground(y, anchor)
                and not _on_a_person(x, y, person, at_ground=True))

    ok("a ball on the turf mid-stance is still refused",
       _kept(200, feet_y), False)
    ok("  while one clear of the stance is kept",
       (_kept(bx - 20, feet_y), _kept(bx + bw + 20, feet_y)), (True, True))

    # ── THE LOOSENING, MEASURED ─────────────────────────────────────
    #
    # At address the ball is teed off the LEAD HEEL, level with the
    # outside edge of the stance. That is where the box edge plus
    # PERSON_PAD_W used to reach, so the shoe rule was taking the ball
    # with it. At ground level the sides now come IN by
    # SHOE_INSET_FRAC instead of going out.
    _dead = [x for x in range(0, 500)
             if _on_a_person(x, feet_y, person, at_ground=True)]
    _was = [x for x in range(0, 500)
            if _on_a_person(x, feet_y, person)]
    print("     refused across x %d..%d (was %d..%d) — %d px narrower"
          % (min(_dead), max(_dead), min(_was), max(_was),
             len(_was) - len(_dead)))
    ok("the refused band is narrower than the box",
       (min(_dead) > bx and max(_dead) < bx + bw), True)
    import math
    _step = math.ceil(SHOE_INSET_FRAC * bw)
    ok("  by SHOE_INSET_FRAC of its width each side",
       (min(_dead) - bx, (bx + bw) - max(_dead)), (_step, _step))
    ok("  and a ball at the lead heel now survives",
       _kept(bx + bw - 2, feet_y), True)
    ok("  while the middle of the stance, where shoes are, does not",
       _kept(bx + bw // 2, feet_y), False)
    ok("  with nothing changed above the foot line",
       _on_a_person(bx + bw + 2, 100, person), True)

    # The vertical band the ball must be in, for the record.
    print("     ground band is y %.0f..%.0f, on-a-person pads %.0fpx below"
          % (feet_y - BALL_BAND_ABOVE_FEET * bh,
             feet_y + BALL_BAND_BELOW_FEET * bh, PERSON_PAD_H * bh))

    # ── AND THE REPORT NAMES THE TEST ───────────────────────────────
    #
    # Six frames, a golfer-sized dark box, and one bright ball sitting
    # squarely in the stance. Nothing should be found, and the answer
    # must say why rather than shrugging.
    W, H = 420, 460
    def _shot():
        im = np.full((H, W, 3), 120, np.uint8)
        cv2.rectangle(im, (bx, 40), (bx + bw, feet_y), (45, 45, 50), -1)
        cv2.circle(im, (200, feet_y - 4), 5, (250, 250, 250), -1)
        return im

    r = ball_in_frames([_shot() for _ in range(6)],
                       rect=(0, 0, W, H), people=person, anchor=anchor)
    ok("nothing is found", r["left"], None)
    ok("  the counts are reported", isinstance(r.get("counts"), dict), True)
    ok("  and the bars beside them",
       isinstance(r.get("limits"), dict), True)
    _c = r["counts"] or {}
    print("     counts: %s" % _c)
    print("     limits: %s" % (r.get("limits"),))
    ok("  every speck is accounted for exactly once",
       _c.get("seen"),
       sum(_c.get(k, 0) for k in ("too big", "too faint", "on the golfer",
                                  "off the ground", "kept")))
    ok("  the ball in the stance was refused as on the golfer",
       _c.get("on the golfer", 0) >= 1, True)
    ok("  and the sentence names it, not just the dict",
       "on the golfer" in r["why"], True)
    ok("  and quotes the bars it had to clear",
       ("contrast" in r["why"] and "area" in r["why"]), True)

    # ── THE SAME BALL, CLEAR OF THE STANCE, IS FOUND ────────────────
    #
    # The control: nothing about the ball changed except where it sits,
    # so anything else failing would show up here.
    def _shot2(ball=True):
        im = np.full((H, W, 3), 120, np.uint8)
        cv2.rectangle(im, (bx, 40), (bx + bw, feet_y), (45, 45, 50), -1)
        if ball:
            cv2.circle(im, (bx - 30, feet_y - 4), 5, (250, 250, 250), -1)
        return im

    r2 = ball_in_frames([_shot2(True), _shot2(True), _shot2(True),
                         _shot2(False), _shot2(False), _shot2(False)],
                        rect=(0, 0, W, H), people=person, anchor=anchor)
    ok("a ball clear of the stance is found and seen to leave",
       r2["left"], True)
    ok("  and is reported where it sat",
       r2["spot"] is not None and abs(r2["spot"][0] - (bx - 30)) <= 6, True)
    print("     control: %s" % r2["why"])

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
