"""Where the people are in a frame, from YOLOv8n via cv2.dnn.

WHY THIS EXISTS. Audio 2 picks the golfer "whose feet are in a
trigger zone and furthest up the picture", and for three rounds it
read those feet off the motion-heat blob -- the bottom of its box,
then the bottom of its middle half, then the 97th percentile of its
pixels. Every one of them was wrong, and they were wrong for a reason
no amount of statistics fixes: the blob is MOTION, and a golfer at
address or at the top of a backswing is not moving their legs. The
heat simply stops at the waist. There are no pixels down there to
measure, so no measurement of those pixels can find the feet.

A person detector has no such problem: it finds a body whether or not
it moved, and the bottom of its box is where that body meets the
ground. The rig has run one continuously for months -- see
YoloPersonDetector in pi-agent/agent/tee.py, which this mirrors -- on
a Raspberry Pi, through OpenCV's DNN module, with no package the
backend does not already have.

The difference from the rig's copy: that one answers "is somebody
there", so it returns the single most confident person. This one
answers "which of these people is hitting", so it returns them all.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# COCO class 0 is "person". The model emits 4 bbox values plus 80 class
# scores per anchor; only the person column is ever read.
PERSON_CLASS = 0

# Shipped in the repo and copied into the image by the Dockerfile.
# Overridable because the path differs between a container, a checkout
# and a test.
_MODEL_ENV = "YOLO_MODEL_PATH"
_MODEL_CANDIDATES = (
    "/app/pi-agent/models/yolov8n.onnx",
    "pi-agent/models/yolov8n.onnx",
)

_NET = None
_NET_TRIED = False


def model_path() -> str | None:
    """Where the ONNX lives, or None. Never raises."""
    p = os.environ.get(_MODEL_ENV)
    if p and Path(p).exists():
        return p
    for c in _MODEL_CANDIDATES:
        if Path(c).exists():
            return c
    # From this file: backend/app/services -> repo root.
    guess = Path(__file__).resolve().parents[3] / "pi-agent" / "models" \
        / "yolov8n.onnx"
    return str(guess) if guess.exists() else None


def _net():
    """The loaded network, or None. Loaded ONCE and cached.

    A miss is cached too. Without that, a backend with no model file
    would pay a filesystem walk and a failed read on every candidate
    of every test cut.
    """
    global _NET, _NET_TRIED
    if _NET_TRIED:
        return _NET
    _NET_TRIED = True
    mp = model_path()
    if not mp:
        log.info("people: no YOLO model on disk; falling back to the heat")
        return None
    try:
        n = cv2.dnn.readNetFromONNX(mp)
        n.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        n.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        _NET = n
        log.info("people: YOLO person detector loaded from %s", mp)
    except Exception as exc:  # noqa: BLE001
        log.warning("people: could not load %s (%s)", mp, exc)
        _NET = None
    return _NET


def find_people(frame, *, input_size: int = 320, conf: float = 0.35,
                iou: float = 0.5, min_area_frac: float = 0.0) -> list:
    """Every person in the frame, feet first. Never raises.

    Returns a list of {"box": [x, y, w, h], "feet": [x, y], "conf": f}
    in the frame's own pixels, sorted by confidence, highest first.
    `feet` is the bottom-centre of the box -- where they are STANDING,
    which is the whole point of using this rather than the heat.

    An empty list means "could not tell", NOT "nobody there": no model
    on disk, a frame that would not decode, a detector that threw. The
    caller must treat it as absence of evidence, because every other
    "could not tell" in this codebase is treated that way and one that
    silently meant "nobody" would delete swings.
    """
    net = _net()
    if net is None or frame is None:
        return []
    try:
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return []
        n = int(input_size)
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (n, n),
                                     swapRB=True, crop=False)
        net.setInput(blob)
        out = net.forward()[0].T               # (anchors, 84)
        scores = out[:, 4 + PERSON_CLASS]
        keep = scores >= float(conf)
        if not np.any(keep):
            return []
        rows, scores = out[keep], scores[keep]
        min_px = float(min_area_frac) * (w * h)
        boxes, confs = [], []
        for row, sc in zip(rows, scores):
            cx, cy, bw, bh = row[0], row[1], row[2], row[3]
            bw_px, bh_px = bw / n * w, bh / n * h
            if bw_px * bh_px < min_px:
                continue
            boxes.append([int((cx - bw / 2) / n * w),
                          int((cy - bh / 2) / n * h),
                          int(bw_px), int(bh_px)])
            confs.append(float(sc))
        if not boxes:
            return []
        idxs = cv2.dnn.NMSBoxes(boxes, confs, float(conf), float(iou))
        if idxs is None or len(idxs) == 0:
            return []
        people = []
        for i in np.array(idxs).flatten():
            x, y, bw_px, bh_px = boxes[int(i)]
            people.append({
                "box": [x, y, bw_px, bh_px],
                "feet": [int(x + bw_px / 2), int(y + bh_px)],
                "conf": round(confs[int(i)], 3),
            })
        people.sort(key=lambda p: -p["conf"])
        return people
    except Exception as exc:  # noqa: BLE001
        log.debug("people: detection failed (%s)", exc)
        return []


def frame_at(video_path, at_sec: float):
    """One decoded frame from a video, or None. Never raises."""
    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(float(at_sec) * fps)))
        ok, fr = cap.read()
        return fr if ok else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
