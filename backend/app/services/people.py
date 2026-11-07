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


# A PERSON IS TALLER THAN THEY ARE WIDE, in every pose anybody adopts
# on a tee -- standing, addressing, bending to a tee peg, mid-follow-
# through. Nothing on a golf hole that is twice as wide as it is tall
# is a person, and YOLO at a distance will occasionally offer a bush,
# a bunker edge or a tree canopy as one. This is a sanity bound, not
# a pose classifier: it is deliberately loose enough that a golfer
# doubled over picking up a ball still passes.
MAX_PERSON_WIDTH_OVER_HEIGHT = 2.0

# 0.50 WAS TOO HIGH AND IT COST REAL GOLFERS. Measured on the frames
# of 8 Nov: whole-frame detections of two players on a tee came back
# at 0.553 and 0.680, so the floor was sitting just under them and a
# third player did not clear it at all. The tree that prompted the
# rise is refused by the trigger-zone test, which is the right place
# for it -- a tree's feet are not on a tee -- and a threshold high
# enough to exclude trees is high enough to exclude people.
#
# 0.35 sits above the junk this model emits on these frames (a
# half-height 0.294 box over a golfer's torso) and below every real
# player seen so far.
DEFAULT_PERSON_CONF = 0.35

# THE MODEL IS FIXED-SHAPE AT 320x320. Asking cv2.dnn for 640 throws
# inside the DFL reshape -- the anchor count is baked into the export
# -- so resolution cannot be bought by turning this up. It is a
# constant to be read, not tuned.
INPUT_SIZE = 320

# SO BUY IT BY LOOKING AT LESS AT A TIME. A 1920x1080 frame squashed
# into 320x320 leaves a golfer about 15 px wide, and the detector
# behaves accordingly: boxes that stop at the shoulders, players
# missed entirely. Running the same model over overlapping windows
# of the tee band instead, each roughly square and therefore barely
# squashed, measured on the same frames:
#
#   whole frame   0.553, 0.680   and one player missed
#   windowed      0.824, 0.727, 0.472   -- all three found
#
# Windows step by this fraction of their side, so anybody standing
# on a seam is whole in the next one.
WINDOW_STEP_FRAC = 0.6

# A BOX CUT OFF BY THE EDGE HAS NO TRUSTWORTHY FEET. Its bottom is
# wherever the picture stopped, not where anybody is standing, and
# the whole point of detecting people here is to read a foot point
# off that bottom edge. Combined with low confidence it is also where
# the junk lives: on 8 Nov a bush at the right-hand edge was offered
# as a person and its "feet" were drawn on a branch.
#
# BOTH CONDITIONS, not either. A golfer really can stand at the edge
# of the searched band, and a confident detection there is still a
# golfer; a hesitant one flush against the edge is a bush.
EDGE_PX = 2
EDGE_MIN_CONF = 0.55


def _covered(a, b) -> float:
    """How much of box `a` lies inside box `b`, 0..1. Never raises."""
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0, min(ay1, by1) - max(ay0, by0))
    aa = float(max(1, a[2] * a[3]))
    return (iw * ih) / aa


def _detect_window(net, win, conf, iou, min_px):
    """Boxes in ONE window's own pixels. Never raises."""
    h, w = win.shape[:2]
    n = INPUT_SIZE
    blob = cv2.dnn.blobFromImage(win, 1 / 255.0, (n, n),
                                 swapRB=True, crop=False)
    net.setInput(blob)
    out = net.forward()[0].T                   # (anchors, 84)
    scores = out[:, 4 + PERSON_CLASS]
    keep = scores >= float(conf)
    if not np.any(keep):
        return [], []
    rows, scores = out[keep], scores[keep]
    boxes, confs = [], []
    for row, sc in zip(rows, scores):
        cx, cy, bw, bh = row[0], row[1], row[2], row[3]
        bw_px, bh_px = bw / n * w, bh / n * h
        if bw_px * bh_px < min_px:
            continue
        if bh_px <= 0 or (bw_px / bh_px) > MAX_PERSON_WIDTH_OVER_HEIGHT:
            continue
        boxes.append([int((cx - bw / 2) / n * w),
                      int((cy - bh / 2) / n * h),
                      int(bw_px), int(bh_px)])
        confs.append(float(sc))
    return boxes, confs


def _windows(rw, rh):
    """Left edges and widths covering `rw`, each about `rh` across.

    A window roughly as wide as it is tall barely distorts when the
    model squashes it into its square input, which is the whole
    point. One window when the region is already squarish.
    """
    side = max(64, min(int(rw), int(rh)))
    if side >= rw:
        return [(0, int(rw))]
    step = max(32, int(side * WINDOW_STEP_FRAC))
    xs = list(range(0, max(1, int(rw) - side + 1), step))
    if xs[-1] + side < rw:
        xs.append(int(rw) - side)
    return [(x, side) for x in xs]


def find_people(frame, *, region=None, conf: float = DEFAULT_PERSON_CONF,
                iou: float = 0.5, min_area_frac: float = 0.0,
                input_size: int | None = None) -> list:
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
        H, W = frame.shape[:2]
        if H <= 0 or W <= 0:
            return []
        rx, ry, rw, rh = 0, 0, W, H
        if region:
            rx = max(0, min(W - 1, int(region[0])))
            ry = max(0, min(H - 1, int(region[1])))
            rw = max(1, min(W - rx, int(region[2])))
            rh = max(1, min(H - ry, int(region[3])))
        sub = frame[ry:ry + rh, rx:rx + rw]
        min_px = float(min_area_frac) * (rw * rh)
        boxes, confs = [], []
        for wx, side in _windows(rw, rh):
            wb, wc = _detect_window(net, sub[:, wx:wx + side],
                                    conf, iou, min_px)
            for b, c in zip(wb, wc):
                # Back into the FRAME's own pixels, once, here.
                boxes.append([b[0] + wx + rx, b[1] + ry, b[2], b[3]])
                confs.append(c)
        if not boxes:
            return []
        # ACROSS ALL THE WINDOWS. Overlapping windows see the same
        # player twice by design, so the suppression has to run over
        # the merged list and not per window.
        idxs = cv2.dnn.NMSBoxes(boxes, confs, float(conf), float(iou))
        if idxs is None or len(idxs) == 0:
            return []
        kept = sorted((int(i) for i in np.array(idxs).flatten()),
                      key=lambda i: -confs[i])
        people = []
        for i in kept:
            x, y, bw_px, bh_px = boxes[i]
            # AND DROP A BOX THAT SITS INSIDE ANOTHER. Overlapping
            # windows see one golfer twice, and when the two readings
            # disagree about where their head is -- a full body in
            # one window, shoulders-down in the next -- the smaller
            # box can be 70% inside the larger and still score under
            # the 0.5 IoU that NMS suppresses on. Two boxes on one
            # player then reach the picker as two golfers standing a
            # few pixels apart, which it reports as too close to call.
            if any(_covered([x, y, bw_px, bh_px], q["box"]) > 0.70
                   for q in people):
                continue
            if (confs[i] < EDGE_MIN_CONF
                    and (x <= rx + EDGE_PX
                         or y <= ry + EDGE_PX
                         or x + bw_px >= rx + rw - EDGE_PX
                         or y + bh_px >= ry + rh - EDGE_PX)):
                continue
            people.append({
                "box": [x, y, bw_px, bh_px],
                "feet": [int(x + bw_px / 2), int(y + bh_px)],
                "conf": round(confs[i], 3),
            })
        return people
    except Exception as exc:  # noqa: BLE001
        log.debug("people: detection failed (%s)", exc)
        return []


def _overlaps(a, b, *, iou=0.30, cover=0.60) -> bool:
    """Are these two boxes the same person? Never raises."""
    ax1, ay1 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0, min(ay1, by1) - max(a[1], b[1]))
    inter = float(iw * ih)
    if inter <= 0:
        return False
    aa, ba = float(max(1, a[2] * a[3])), float(max(1, b[2] * b[3]))
    if inter / (aa + ba - inter) >= iou:
        return True
    return max(inter / aa, inter / ba) >= cover


def find_people_over_time(video_path, at_sec: float, offsets=(0.0,), *,
                          region=None, **kw) -> list:
    """Everybody seen at ANY of these moments, merged. Never raises.

    ONE FRAME IS NOT ENOUGH, and that was the bug. Detection ran on
    the impact frame alone, so a golfer standing behind a playing
    partner at that instant simply did not exist -- while being in
    clear view, feet and all, half a second either side. The
    operator's words: "he's partially blocked in some frames but feet
    always in view."

    The frames are visited NEAREST THE IMPACT FIRST, and a detection
    is kept only if it does not overlap somebody already found. So
    the impact frame defines the cast wherever it can see, earlier
    and later frames only ADD the people it could not, and a golfer
    who merely walked between frames is not counted twice.

    Each person carries `seen_at`, the offset they were found at, so
    a stance measured half a second from the impact can be recognised
    as such rather than trusted blindly.
    """
    order = sorted({round(float(o), 3) for o in (offsets or (0.0,))},
                   key=lambda o: (abs(o), o))
    found: list = []
    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        for off in order:
            f = int(round((float(at_sec) + off) * fps))
            if f < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            if not ok or fr is None:
                continue
            for p in find_people(fr, region=region, **kw):
                if any(_overlaps(p["box"], q["box"]) for q in found):
                    continue
                found.append(dict(p, seen_at=off, frame=f))
        return found
    except Exception as exc:  # noqa: BLE001
        log.debug("people: multi-frame detection failed (%s)", exc)
        return found
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass


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
