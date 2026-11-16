#!/usr/bin/env python3
"""Ask the judge where the ball is, and draw only that.

The swing judge already looks at an address tile and a finish tile,
and it can plainly see a ball the OpenCV detector cannot -- measured:
the detector reported "no ball identified" on clips where the model
wrote "golfer at address". So it is asked, on the call being made
anyway.

IN FRACTIONS OF THE ADDRESS TILE, which is the only frame of
reference the model can hold. The tile is a crop of the video at
whatever scale fitted the grid, so pixels there are a space that
exists nowhere else; a fraction maps back through the crop rectangle
the caller already has, with no scale to reconstruct.

AND THE PICTURE IS EMPTY APART FROM IT. Every diagnostic mark added
over the past few rounds answered a real question, and their sum was
a band of circles and crosses across the tee with the ball somewhere
underneath. The measurements are all still reported; only the drawing
is gone.

    python3 tools/ai_ball_xy_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


import numpy as np  # noqa: E402
import cv2  # noqa: E402

from app.services import ai_tracer as T  # noqa: E402
from app.services.tracer import draw_zones  # noqa: E402

P = T._JUDGE_SWING_FRAMES_PROMPT

print("\n-- the judge is asked, about ONE tile, in fractions")
check("it asks for a position", "ball_xy" in P, True)
check("  of the address tile only", "ADDRESS TILE ONLY" in P, True)
check("  as fractions, not pixels", "FRACTIONS" in P, True)
check("  with the corners defined",
      "[0, 0]" in P and "[1, 1]" in P, True)
# A GUESS HERE IS SEARCHED AND BELIEVED, which is the whole risk of
# asking a vision model for coordinates. The prompt has to say so.
check("  and null is offered for 'not sure'", "null" in P, True)
check("  saying why a guess is worse than nothing",
      "worse than nothing" in P, True)
check("it still may not move the verdict",
      "may change `is_swing`" in P, True)


print("\n-- and only a real fraction pair is believed")
def _parse(obj):
    """The function's own validation, over a canned reply."""
    out = {"ball_xy": None}
    _b = obj.get("ball_xy")
    if isinstance(_b, (list, tuple)) and len(_b) == 2:
        try:
            _x, _y = float(_b[0]), float(_b[1])
            if 0.0 <= _x <= 1.0 and 0.0 <= _y <= 1.0:
                out["ball_xy"] = [round(_x, 4), round(_y, 4)]
        except (TypeError, ValueError):
            pass
    return out["ball_xy"]


for _in, _want in (
        ({"ball_xy": [0.42, 0.81]}, [0.42, 0.81]),
        ({"ball_xy": [0, 1]}, [0.0, 1.0]),
        ({"ball_xy": None}, None),
        ({}, None),
        # A MODEL THAT ANSWERED PIXELS. Mapping 310 as a fraction
        # would put the ball several frames to the right of the crop.
        ({"ball_xy": [310, 190]}, None),
        ({"ball_xy": [-0.1, 0.5]}, None),
        ({"ball_xy": [0.5]}, None),
        ({"ball_xy": "middle"}, None),
        ({"ball_xy": [0.5, "x"]}, None)):
    check("  %-24s -> %s" % (str(_in.get("ball_xy", "(absent)")), _want),
          _parse(_in), _want)
_src = __import__("inspect").getsource(T.judge_swing_with_frames)
check("the judge publishes it", 'out["ball_xy"]' in _src, True)
check("  with the same bounds check", "0.0 <= _bx <= 1.0" in _src, True)


print("\n-- the fraction maps back through the crop rectangle")
# THE MAPPING IS THE CROP AND NOTHING ELSE. Asserted here as
# arithmetic so a change to it has to come past this line.
ADM = (ROOT / "backend" / "app" / "routers"
       / "admin.py").read_text(encoding="utf-8")
check("admin maps it", 'cut_entry["frames_ball_xy"]' in ADM, True)
check("  through frames_rect", '_brect = _chk.get("frames_rect")' in ADM,
      True)
_rect = (600.0, 690.0, 760.0, 770.0)
for _frac, _want in ((( 0.0, 0.0), (600, 690)),
                     ((1.0, 1.0), (760, 770)),
                     ((0.5, 0.5), (680, 730))):
    _got = (int(round(_rect[0] + _frac[0] * (_rect[2] - _rect[0]))),
            int(round(_rect[1] + _frac[1] * (_rect[3] - _rect[1]))))
    check("  fraction %s of that rect -> %s" % (str(_frac), _want),
          _got, _want)


print("\n-- and the picture draws THAT, and nothing else")
_img = np.full((300, 420, 3), 70, np.uint8)
_img[:] = (55, 95, 60)
cv2.rectangle(_img, (180, 70), (210, 215), (60, 55, 50), -1)
ZON = [(150, 190, 160, 40)]
# Everything the old overlay would have drawn, handed in at once.
_clutter = {
    "spot": None,
    "limits": {"ground band y": [188, 218], "min contrast": 30.0,
               "stance box": [217, 191, 382, 230]},
    "region": [217, 191, 382, 230],
    "near": {"best_in_band": 26.0, "best_in_band_at": [150, 200]},
    "looked_at": [{"x": 150 + 8 * i, "y": 200, "verdict": "too faint",
                   "score": 26.0} for i in range(12)],
    "shape_rejects": {"in_band": [{"x": 300, "y": 206, "area": 9,
                                   "why": "sliver"}]},
}
_zones_only = draw_zones(_img, ZON, ball={"spot": None})
_with_clutter = draw_zones(_img, ZON, ball=_clutter)
check("a failed check draws no diagnostic marks at all",
      int((_with_clutter != _zones_only).any(axis=2).sum()), 0)

_with_ai = draw_zones(_img, ZON, ball=dict(_clutter, ai_xy=[258, 212]))
_diff = (_with_ai != _zones_only).any(axis=2)
check("  but it does draw the judge's ball", bool(_diff.any()), True)
check("  at the point it was given",
      bool(_diff[212 - 10:212 + 10, 258 - 10:258 + 10].any()), True)
# ONE MARK. The complaint that caused this was a dozen per tile.
_ys, _xs = np.where(_diff)
check("  as one small mark, not a band across the tee",
      int(_xs.max() - _xs.min()) < 40, True)
# AND NOT IN THE BALL'S OWN RED: this is a claim from another
# process that nothing has checked.
check("a FOUND ball still draws its own mark",
      draw_zones(_img, ZON, ball={"spot": [258, 212]}) is not None, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
