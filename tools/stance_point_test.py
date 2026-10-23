#!/usr/bin/env python3
"""A zone is a patch of GROUND, and the detector reports feet.

It used to report the middle of the person's bounding box. That made a
zone mean "a region a golfer's waist passes through", which is not
what anyone draws and is not even constant for one tee -- how far a
waist sits above the ground in pixels depends on how far away the
person is, so the same tee needs a different box from a different
camera position.

Measured on hole 8: boxes drawn around the two tee SURFACES would have
caught neither tee, while the box that did catch waists was tall
enough to also contain the green, the bunkers, and the tees sixty
yards further out -- which is what had been triggering the recordings
of people standing about.

    python3 tools/stance_point_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

import numpy as np  # noqa: E402

from agent import tee as tee_mod  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


W, H = 1920, 1080


def test_yolo_reports_the_feet():
    """Straight at the arithmetic, with the net stubbed out.

    One anchor, one person, a box from y=400 to y=700. Feet are 700,
    the middle is 550, and which one comes back is the whole change.
    """
    d = tee_mod.YoloPersonDetector.__new__(tee_mod.YoloPersonDetector)
    d.conf, d.iou, d.input_size = 0.4, 0.5, 320
    d.min_box_area_frac = 0.0
    d.PERSON_CLASS = tee_mod.YoloPersonDetector.PERSON_CLASS
    n = 320
    # cx, cy, w, h in model pixels -> a box 400..700 in frame pixels.
    bx, bw = 960.0, 200.0
    by0, by1 = 400.0, 700.0
    row = np.zeros(84, dtype=np.float32)
    row[0] = bx / W * n
    row[1] = ((by0 + by1) / 2) / H * n
    row[2] = bw / W * n
    row[3] = (by1 - by0) / H * n
    row[4 + d.PERSON_CLASS] = 0.9
    out = np.zeros((1, 84, 1), dtype=np.float32)
    out[0, :, 0] = row

    class Net:
        def setInput(self, *a, **k):
            pass

        def forward(self):
            return out

    d.net = Net()
    got = d.detect(np.zeros((H, W, 3), dtype=np.uint8))
    check("x is the middle of the box", got[0], 960)
    # Within a pixel of the bottom: the box coords are truncated to int
    # on the way through model space, so this lands on 699 or 700. The
    # thing being told apart is 150px away, so a pixel of slop is not
    # hiding anything -- and asserting 700 exactly would be testing
    # float round-tripping rather than behaviour.
    check("y is the BOTTOM of the box", abs(got[1] - by1) <= 1, True)
    # CONTROL: the old answer, 150px higher on this person -- and the
    # assertion above would fail on it by a mile.
    check("control, the middle would have been", (by0 + by1) / 2, 550.0)
    check("control, and that is not within a pixel of the feet",
          abs(((by0 + by1) / 2) - by1) <= 1, False)


def test_a_ground_zone_now_catches_a_golfer():
    """The case that drove this, in numbers.

    A golfer standing on a tee whose mown surface is the bottom tenth
    of the frame: feet at y=1010, head at y=830, so the middle is 920.
    A zone drawn on the ground runs 980-1060.
    """
    zone = {"frame": {"w": W, "h": H},
            "boxes": [{"x": 700, "y": 980, "w": 600, "h": 80,
                       "label": "back"}]}
    feet = (1000, 1010)
    middle = (1000, 920)
    check("the feet are in the zone", tee_mod._in_roi(feet, zone), True)
    # CONTROL: the point the detector used to report is above it, which
    # is exactly why a sensibly-drawn zone used to fire on nobody.
    check("control, the old middle point is not",
          tee_mod._in_roi(middle, zone), False)


def test_the_distant_tee_is_excluded_by_the_ground_it_stands_on():
    """And this is what it buys.

    Hole 8 has tees at three distances. The far ones appear HIGHER in
    frame because they are further away, so a zone on the near ground
    excludes them -- by geometry, not by a threshold anyone tuned.
    """
    zone = {"frame": {"w": W, "h": H},
            "boxes": [{"x": 700, "y": 980, "w": 600, "h": 80,
                       "label": "back"}]}
    near_feet = (1000, 1010)      # back tee, close to camera
    far_feet = (1000, 800)        # front tees, 60 yards further out
    check("the near tee triggers", tee_mod._in_roi(near_feet, zone), True)
    check("the far tee does not", tee_mod._in_roi(far_feet, zone), False)
    # CONTROL: a box tall enough to have caught the near golfer's WAIST
    # reaches up far enough to swallow the far tee as well -- which is
    # the box hole 8 was actually running.
    tall = {"frame": {"w": W, "h": H},
            "boxes": [{"x": 700, "y": 600, "w": 600, "h": 460}]}
    check("control, a waist-height box catches the far tee too",
          tee_mod._in_roi(far_feet, tall), True)


def test_mediapipe_reports_its_lowest_landmark():
    """No knowledge of which index is an ankle, and it degrades."""
    class LM:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class Res:
        pass

    class Pose:
        def process(self, rgb):
            r = Res()
            r.pose_landmarks = type("P", (), {
                "landmark": [LM(0.4, 0.3), LM(0.6, 0.5), LM(0.5, 0.9)]})()
            return r

    d = tee_mod.MediaPipePersonDetector.__new__(
        tee_mod.MediaPipePersonDetector)
    d.detect_width = W      # no rescale, so the arithmetic is readable
    d.pose = Pose()
    got = d.detect(np.zeros((H, W, 3), dtype=np.uint8))
    check("x is the landmarks' mean", got[0], int(round(0.5 * W)))
    check("y is the LOWEST landmark", got[1], int(round(0.9 * H)))
    # CONTROL: the mean y, which is what it used to return.
    check("control, the mean would have been",
          int(round(((0.3 + 0.5 + 0.9) / 3) * H)), 612)


for fn in (test_yolo_reports_the_feet,
           test_a_ground_zone_now_catches_a_golfer,
           test_the_distant_tee_is_excluded_by_the_ground_it_stands_on,
           test_mediapipe_reports_its_lowest_landmark):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
