#!/usr/bin/env python3
"""Six tiles of the box alone, zoomed, with both answers on them.

WHY A SECOND PICTURE. The swing judge's strip is framed on the
GOLFER, and has to be -- the swing question is about a body and a
club, and a tile holding only grass cannot answer it. But that
framing puts a three-pixel ball in a tile where the golfer is two
hundred pixels tall, and asked where the ball was the judge answered
with a point on the golfer's legs, ABOVE the trigger zone. That is a
fair reading of a picture in which the ball cannot be seen.

So the ball question gets its own picture: nothing but the search
box, at roughly five to ten times the scale. Nothing is added -- the
ball is still three source pixels -- but a feature that spanned a
fraction of one model patch now spans several.

AND BOTH ANSWERS ARE DRAWN, in deliberately different marks: a CIRCLE
for the brightness detector and CROSSHAIRS for the judge, so a glance
says whether they agree. The clean copy carries neither: a model
shown its own previous answer is not being asked again.

    python3 tools/ball_box_strip_test.py
"""
import subprocess
import sys
import tempfile
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

from app.services.tracer import ball_box_strip  # noqa: E402
from app.services import ai_tracer as T  # noqa: E402


def have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


TMP = Path(tempfile.mkdtemp(prefix="ballbox-"))
W, H, FPS = 640, 360, 60
BALL = (350, 245)
BOX = (325, 225, 420, 265)          # 95x40
IMPACT = 1.20

print("\n-- the question's own prompt")
P = T._BALL_BOX_PROMPT
check("it says the crop is blown up and will look blocky",
      "blown up" in P and "blocky" in P, True)
# THE CUE THAT SURVIVES BLOCKINESS. At this zoom "small, pale, round"
# describes a hundred things; "does not move, then is gone" describes
# one.
check("it leans on vanishing, not on roundness",
      "VANISHES" in P or "vanishes" in P, True)
check("  and warns off things that are in every tile",
      "every tile is not" in P, True)
check("it answers in fractions of a tile", "FRACTIONS" in P, True)
check("  with null offered", "null" in P, True)
check("  and a guess called worse than nothing",
      "worse than nothing" in P, True)

print("\n-- only fractions are believed, same as the swing judge")


def _parse(obj):
    _xy = obj.get("xy")
    if isinstance(_xy, (list, tuple)) and len(_xy) == 2:
        try:
            _x, _y = float(_xy[0]), float(_xy[1])
            if 0.0 <= _x <= 1.0 and 0.0 <= _y <= 1.0:
                return [round(_x, 4), round(_y, 4)]
        except (TypeError, ValueError):
            pass
    return None


for _in, _want in (({"xy": [0.26, 0.5]}, [0.26, 0.5]),
                   ({"xy": None}, None),
                   ({}, None),
                   ({"xy": [40, 20]}, None),      # answered in pixels
                   ({"xy": [1.4, 0.2]}, None),
                   ({"xy": "left"}, None)):
    check("  %-18s -> %s" % (str(_in.get("xy", "(absent)")), _want),
          _parse(_in), _want)
_src = __import__("inspect").getsource(T.ball_in_box)
check("the caller uses that bounds check", "0.0 <= _x <= 1.0" in _src, True)
check("  and reports whether it vanished",
      'out["gone_after"]' in _src, True)
check("a missing strip is refused, not guessed",
      T.ball_in_box(TMP / "nope.jpg")["available"], False)

print("\n-- the fraction maps back through the box, and nothing else")
for _frac, _want in (((0.0, 0.0), (325, 225)),
                     ((1.0, 1.0), (420, 265)),
                     ((0.5, 0.5), (372, 245))):
    _got = (int(round(BOX[0] + _frac[0] * (BOX[2] - BOX[0]))),
            int(round(BOX[1] + _frac[1] * (BOX[3] - BOX[1]))))
    check("  fraction %s -> %s" % (str(_frac), _want), _got, _want)

if not have_ffmpeg():
    print("\nffmpeg is not installed here — the strip itself is unchecked")
    print()
    if FAILED:
        print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all good")
    sys.exit(0)

print("\n-- the strip itself, cut from a real clip")
_d = TMP / "f"
_d.mkdir()
for i in range(130):
    rng = np.random.default_rng(i)
    f = np.full((H, W, 3), 70, np.uint8)
    f[:] = (55, 95, 60)
    f += rng.integers(0, 9, (H, W, 3), dtype=np.int16).astype(np.uint8)
    cv2.rectangle(f, (288, 125), (312, 250), (48, 44, 42), -1)
    if i / FPS < IMPACT:
        cv2.circle(f, BALL, 2, (88, 128, 93), -1)
    cv2.imwrite(str(_d / ("f%04d.png" % i)), f)
VID = TMP / "clip.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate",
                str(FPS), "-i", str(_d / "f%04d.png"), "-c:v", "libx264",
                "-crf", "20", "-pix_fmt", "yuv420p", str(VID)],
               check=True, timeout=300)

r = ball_box_strip(VID, IMPACT, TMP / "strip.jpg", box=BOX,
                   spot=list(BALL), ai_xy=[BALL[0] + 2, BALL[1] - 1])
check("it builds", r["ok"], True)
check("  all six offsets", len(r["used"]), 6)
# THE ZOOM IS THE POINT. A box this size has to come up several times
# before a three-pixel ball spans more than one model patch.
check("  zoomed well past 3x", r["zoom"] > 3.0, True)
_img = cv2.imread(r["path"])
check("  and the tiles are the box's shape, not the frame's",
      round((_img.shape[1] / 3) / (_img.shape[0] / 2), 1),
      round((BOX[2] - BOX[0]) / float(BOX[3] - BOX[1]), 1))

print("\n-- the clean copy is what a model is shown")
_clean = cv2.imread(r["clean"])
check("there is a clean copy", _clean is not None, True)
check("  the same size as the marked one", _clean.shape, _img.shape)
# A MODEL SHOWN ITS OWN PREVIOUS ANSWER IS NOT BEING ASKED AGAIN, and
# a model shown the detector's answer is being led.
check("  and it differs from the marked one",
      bool((_clean != _img).any()), True)
_red = ((_clean[:, :, 2].astype(int) - _clean[:, :, 1]) > 60).sum()
check("  with no mark drawn on it", int(_red), 0)

print("\n-- the two answers are drawn differently, and in the box's frame")
_bx = int(round((BALL[0] - BOX[0]) * r["zoom"]))
_by = int(round((BALL[1] - BOX[1]) * r["zoom"]))
_tile = _img[0:int(_img.shape[0] / 2), 0:int(_img.shape[1] / 3)]
_near = _tile[max(0, _by - 20):_by + 20, max(0, _bx - 20):_bx + 20]
check("something is drawn where the ball is", bool(_near.size), True)
_isred = ((_near[:, :, 2].astype(int) - _near[:, :, 1]) > 60).any()
_iscyan = ((_near[:, :, 1].astype(int) - _near[:, :, 2]) > 60).any()
check("  the detector's circle (red)", bool(_isred), True)
check("  and the judge's crosshairs (cyan)", bool(_iscyan), True)

print("\n-- and a box it cannot cut says so")
check("no box -> a reason",
      ball_box_strip(VID, IMPACT, TMP / "x.jpg", box=None)["why"],
      "no box to cut")
check("no clip -> a reason, not a raise",
      ball_box_strip(TMP / "nope.mp4", IMPACT, TMP / "y.jpg",
                     box=BOX)["ok"], False)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
