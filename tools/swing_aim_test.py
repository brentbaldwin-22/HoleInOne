#!/usr/bin/env python3
"""Does the AI's first look get aimed at the golfer who SWUNG?

WHAT WENT WRONG ON #1280. The AI launch plot reported "1/4 launch
frames" and no ball at address: the zoom square found nothing, and
every look after it fell back to a rest-anchored region that widens
by 4r across and 12r up on each miss -- by the third look the crop was
the full height of the picture and the ball was three pixels in it.
The fifth look picked a cloud edge.

Two causes, both of them a measurement thrown away:

  1. THE BOX WAS AIMED AT THE BIGGEST BODY, not the swinging one. On a
     tee with two people the partner leaning on a trolley is a larger
     continuous-motion blob than a golfer mid-swing, so the first look
     went to the wrong person's feet. The flight chain -- already
     computed, already DRAWN in red on the same composite, and
     exported as a bare length -- starts at the ball that was actually
     struck and cannot pick the wrong person.

  2. THE BOX WAS USED ONCE. A box that was right on the impact frame
     does not stop being right because the model missed; the ball is
     just further up the same path. The operator put it plainly: look
     in the box "for the first couple until we find a ball and then
     follow it from there".

    python3 tools/swing_aim_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from app.services import tracer as tr  # noqa: E402
from app.services import ai_tracer as ai  # noqa: E402

FAIL: list[str] = []

W, H = 1920, 1080
DET = 0.5                      # the heat runs at half width
DW, DH = int(W * DET), int(H * DET)


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Det:
    """The shape _arc_track_from_heatmap returns: native pixels."""

    def __init__(self, frame, x, y):
        self.frame, self.x, self.y = frame, x, y


def two_people():
    """A constant-motion mask with a BIG still body on the left and a
    SMALLER swinging one on the right -- the arrangement that sent the
    first look to the wrong person."""
    m = np.zeros((DH, DW), np.uint8)
    # the partner: wide and tall, the biggest blob in the mask
    m[int(DH * 0.42):int(DH * 0.74), int(DW * 0.22):int(DW * 0.30)] = 1
    # the golfer who swung: smaller
    m[int(DH * 0.46):int(DH * 0.70), int(DW * 0.60):int(DW * 0.65)] = 1
    return m


def main() -> int:
    # ---- the chain runs back to the tee -----------------------------
    # A ball climbing 40px a frame and drifting 6px right, first seen 4
    # frames after impact: the tee is 4 steps back down that line.
    chain = [Det(104 + i, 1200 + 6 * i, 700 - 40 * i) for i in range(8)]
    spot = tr.tee_spot_from_chain(chain, 100, W, H)
    check("a rising chain runs back to a tee spot", spot is not None, True)
    check("  landing where the line says, in x",
          abs(spot[0] - (1200 - 6 * 4)) <= 2, True)
    check("  and in y", abs(spot[1] - (700 + 40 * 4)) <= 2, True)

    # AND IT REFUSES WHAT IT CANNOT MEASURE, rather than guessing. Each
    # of these produced a confident wrong answer in the version that
    # had no gates.
    falling = [Det(104 + i, 1200, 300 + 40 * i) for i in range(8)]
    check("a falling chain is not a launch", 
          tr.tee_spot_from_chain(falling, 100, W, H), None)
    check("  nor is a chain of two points",
          tr.tee_spot_from_chain(chain[:2], 100, W, H), None)
    far = [Det(400 + i, 1200 + 6 * i, 700 - 40 * i) for i in range(8)]
    check("  nor one found 300 frames after the strike",
          tr.tee_spot_from_chain(far, 100, W, H), None)
    off = [Det(104 + i, 10 + 30 * i, 100 - 40 * i) for i in range(8)]
    check("  nor one whose run-back leaves the frame",
          tr.tee_spot_from_chain(off, 100, W, H), None)

    # ---- the aim picks the body the swing belongs to ----------------
    mask = two_people()
    _g_big, _ = tr.golfer_and_ball_box(mask, DET, W, H)
    check("with nothing to aim at, the biggest body wins",
          _g_big[0] < W * 0.4, True)

    # the tee spot sits under the RIGHT-hand (smaller) body
    aim = (W * 0.625, H * 0.72)
    _g_aim, _bb_aim = tr.golfer_and_ball_box(mask, DET, W, H, aim=aim)
    check("given the swing's own spot, the swinging body wins",
          _g_aim[0] > W * 0.5, True)
    check("  and the ball box lands on that body, not the other",
          _bb_aim[0] > W * 0.4, True)

    # AN AIM NOWHERE NEAR EITHER BODY falls back rather than picking
    # the nearest of two wrong answers.
    _g_far, _ = tr.golfer_and_ball_box(mask, DET, W, H, aim=(W * 0.97, H * 0.7))
    check("an aim far from every body falls back to the biggest",
          _g_far[0] < W * 0.4, True)

    # ---- the tee box is tighter than a body's feet ------------------
    # This is the point of it: the model is being asked to find a ball
    # a few pixels across, and it was being shown three body widths.
    _feet = tr.golfer_and_ball_box(mask, DET, W, H, aim=aim)[1]
    _tee = tr.ball_box_at(aim[0], aim[1], W, H)
    check("the tee box is smaller than the feet box",
          _tee[2] * _tee[3] < _feet[2] * _feet[3], True)
    check("  and still big enough to see", min(_tee[2], _tee[3]) >= 24, True)
    check("  and centred on the spot",
          (abs(_tee[0] + _tee[2] / 2 - aim[0]) <= 1
           and abs(_tee[1] + _tee[3] / 2 - aim[1]) <= 1), True)

    # ---- the first looks keep the aim -------------------------------
    # The regression this exists for: with first_rect given and nothing
    # found yet, the crop must stay near the box instead of growing to
    # the whole picture. Measured off the real geometry rather than
    # asserted, by replaying the branch's own arithmetic.
    r = max(10, int(round(0.015 * H)))
    rect = (1180, 660, 70, 60)

    def held(i, misses):
        _bx0, _by0, _bw0, _bh0 = rect
        _wx = (max(_bw0, int(ai.FIRST_LOOK_WIDE_R * r))
               * (1.0 + 0.5 * misses))
        _cx = _bx0 + _bw0 / 2.0
        bx0, bx1 = max(0, int(_cx - _wx / 2.0)), min(W, int(_cx + _wx / 2.0))
        by1 = min(H, _by0 + _bh0)
        by0 = max(0, int(by1 - _bh0 - ai.FIRST_LOOK_CLIMB_R * r
                         * (i + 0.5 * misses)))
        return bx1 - bx0, by1 - by0

    def adrift(misses):
        """What the old branch did: anchored at the rest, widening."""
        rx, ry = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
        _wx, _up = (14 + 4 * misses) * r, (30 + 12 * misses) * r
        bx0, bx1 = max(0, int(rx - _wx)), min(W, int(rx + _wx))
        by0, by1 = max(0, int(ry - _up)), min(H, int(ry + 4 * r))
        return bx1 - bx0, by1 - by0

    for i, m in ((1, 1), (2, 2), (3, 3)):
        _hw, _hh = held(i, m)
        _aw, _ah = adrift(m)
        print(f"      look {i}: held {_hw}x{_hh}px, adrift {_aw}x{_ah}px")
        check(f"  look {i} searches less picture than the old fallback",
              _hw * _hh < _aw * _ah, True)
    check("the third look is still not the whole frame height",
          held(3, 3)[1] < H, True)
    # AND IT STILL REACHES UP, because the ball does. A held box that
    # never grew would be a different failure with the same symptom.
    check("each look reaches higher than the last",
          held(3, 3)[1] > held(1, 1)[1], True)

    # ---- the judge's own picture is framed on the golfer ------------
    # THE REASON THREE ROUNDS OF PROMPT WORDING DID NOTHING. The judge
    # gets the composite at whatever size it is written -- there is no
    # resize anywhere in judge_swing_heat_image -- and the crop came
    # from the trigger zones, 3x their extent, which on a wide tee
    # clamps to most of the frame. A club plume measures perhaps
    # 60x100px in a 1920-wide composite: about three per cent of the
    # picture, and the whole verdict turns on it.
    def judge_crop(golfer):
        _gx, _gy, _gw, _gh = golfer
        _cw = max(tr.JUDGE_CROP_WIDTHS * _gw, 0.9 * _gh)
        _ccx = _gx + _gw / 2.0
        return (int(_ccx - _cw / 2.0),
                int(_gy - tr.JUDGE_CROP_HEADROOM * _gh),
                int(_cw),
                int(_gh * (1.0 + tr.JUDGE_CROP_HEADROOM
                           + tr.JUDGE_CROP_FOOTROOM)))

    # A golfer as the heat actually measures one on this camera.
    GOLFER = [1180, 560, 60, 200]
    _c = judge_crop(GOLFER)
    _plume_px = 60 * 100          # what a club plume spans
    _full = (W * H) / float(_plume_px)
    _cropped = (_c[2] * _c[3]) / float(_plume_px)
    print(f"      plume is 1/{_full:.0f} of the full frame, "
          f"1/{_cropped:.0f} of the crop")
    check("framing on the golfer makes the plume a far bigger share",
          _cropped < _full / 8.0, True)
    check("  the crop clears the ≥160px floor both ways",
          min(_c[2], _c[3]) >= 160, True)
    # IT MUST NOT CUT THE CLUB OFF, which is the one thing it exists to
    # show. The plume reaches above the head by about a body height.
    check("  it reaches a body-height-and-a-half above the head",
          _c[1] <= GOLFER[1] - GOLFER[3], True)
    check("  and keeps the feet in frame",
          _c[1] + _c[3] >= GOLFER[1] + GOLFER[3], True)
    # WIDE ENOUGH THAT THE WRONG PERSON IS SURVIVABLE. If the body pick
    # went to a partner, the swinger beside them must still be in shot.
    check("  and is several body-widths across, not one",
          _c[2] >= 3 * GOLFER[2], True)
    src2 = (ROOT / "backend" / "app" / "services" / "tracer.py").read_text()
    check("the judged picture prefers it over the caller's zones",
          'out["crop_from"] = "the golfer the motion heat found"' in src2,
          True)
    check("  and says so when it fell back to the zones",
          '"the camera\'s trigger zones"' in src2, True)
    check("  and when the crop was too small to use",
          "too small to use" in src2, True)

    # ---- and the composite shows what it aimed at -------------------
    src = (ROOT / "backend" / "app" / "services" / "tracer.py").read_text()
    check("the evidence image draws the first-look box",
          '"first look"' in src, True)
    check("  and says when there was no chain to run back",
          "no chain to run back" in src, True)
    check("the swing check exports the chain's own points",
          '"chain_points"' in src, True)
    adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
    check("and the ascent step carries the tee spot through",
          '"tee_spot", "chain_points"' in adm, True)
    check("  without clipping a measured box to a stale tee box",
          "if _box and not _measured:" in adm, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
