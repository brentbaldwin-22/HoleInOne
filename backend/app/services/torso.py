"""WHERE THE SHIRT IS IN A PICTURE OF A GOLFER.

Pairs with shirt_colour.py, which decides what colour the pixels are.
This decides which pixels to look at, and that is the harder half: a
rectangle guessed off a bounding box is mostly fairway and sky, and a
colour read off mostly-fairway is a colour reading of fairway.

So: pose landmarks. Shoulders and hips bound the torso, which is the
only part of a golfer reliably covered by the shirt -- sleeves move,
the head is skin and hat, the legs are trousers.

cv2 and mediapipe are imported INSIDE the functions, the convention
everywhere else in this codebase: the module has to stay importable on
a box where the vision stack is not installed.
"""
from __future__ import annotations

import logging

log = logging.getLogger("golfreelz.torso")

# Pose landmark indices. Stable across mediapipe versions.
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24

# How far to pull the torso quad in toward its own centre before
# sampling. The quad's EDGES are where the shirt stops: a pixel on the
# shoulder line is half collar and half sky, and a pixel on the hip
# line is half shirt and half belt. 0.25 keeps the middle half.
SHRINK = 0.25

# Landmarks below this visibility are guesses. A golfer at the top of
# the backswing has one shoulder hidden behind the other, and a quad
# built from an invented corner can land anywhere.
MIN_VISIBILITY = 0.5

_POSE = None


def _pose():
    """One Pose, built once. Constructing it costs about a second."""
    global _POSE
    if _POSE is None:
        import mediapipe as mp  # type: ignore
        _POSE = mp.solutions.pose.Pose(
            static_image_mode=True,
            model_complexity=1,
            min_detection_confidence=0.3,
        )
    return _POSE


def torso_pixels(bgr, max_samples: int = 600):
    """RGB samples from the shirt, or [] when there is no clear torso.

    `bgr` is an OpenCV image. Returning [] is a normal outcome and the
    caller must read it as "no opinion" -- half the reasons it happens
    (the golfer mid-swing, a figure too small, nobody in frame at all)
    are not errors.
    """
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return []

    try:
        h, w = bgr.shape[:2]
        res = _pose().process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        lm = getattr(res, "pose_landmarks", None)
        if lm is None:
            return []
        pts = lm.landmark
        corners = []
        for idx in (L_SHOULDER, R_SHOULDER, R_HIP, L_HIP):
            p = pts[idx]
            if getattr(p, "visibility", 1.0) < MIN_VISIBILITY:
                return []
            corners.append((p.x * w, p.y * h))

        cx = sum(c[0] for c in corners) / 4.0
        cy = sum(c[1] for c in corners) / 4.0
        quad = np.array(
            [[cx + (x - cx) * (1 - SHRINK), cy + (y - cy) * (1 - SHRINK)]
             for x, y in corners],
            dtype=np.int32,
        )

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillConvexPoly(mask, quad, 255)
        ys, xs = np.nonzero(mask)
        if ys.size < 40:
            # Too small to say anything. A torso this size is a figure
            # a few pixels tall, and its "colour" is whatever it is
            # standing in front of.
            return []
        if ys.size > max_samples:
            step = ys.size // max_samples
            ys, xs = ys[::step], xs[::step]
        px = bgr[ys, xs]
        return [(int(p[2]), int(p[1]), int(p[0])) for p in px]
    except Exception as exc:  # noqa: BLE001
        log.warning("torso: %s", exc)
        return []


def shirt_of(bgr):
    """(family, confidence) for the shirt in this picture."""
    from . import shirt_colour
    return shirt_colour.dominant(torso_pixels(bgr))


def shirt_of_file(path):
    """(family, confidence) for the shirt in an image file on disk."""
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return None, 0.0
    try:
        img = cv2.imread(str(path))
    except Exception:  # noqa: BLE001
        return None, 0.0
    if img is None:
        return None, 0.0
    return shirt_of(img)
