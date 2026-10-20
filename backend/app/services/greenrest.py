"""Where the ball came to rest on the green.

A BALL AT REST IS NOT A MOTION PROBLEM, and that is the whole reason
this file exists separately from the rest of the vision code. Every
detector in debug3 is a frame difference -- the ascent sweep, the
chains, the descent gates. A stationary ball produces no frame-to-frame
difference at all, so it is not merely hard for them to find, it is
structurally invisible to them. You can see it plainly in a still and
the software cannot see it at any sensitivity.

So this asks a different question. Not "what moved between these two
frames" but "what is on the green now that was not on it before".

    plate_before = median of N frames from before the shot
    plate_after  = median of N frames once everything has settled
    arrivals     = where after is brighter than before, inside the green

The median is what makes it work in the clutter that defeats the
descent search. Brush swaying in the wind medians to its mean position
in BOTH plates and cancels. A golfer walking across is in a minority of
frames at any one pixel, so the median throws them out. The ball
survives because it is the one thing that is genuinely static in the
second plate and genuinely absent from the first.

THE PARTNER'S BALL. A green very often already has a ball on it when
the shot is played, and that case has three endings, all of which this
has to get right:

  it stays        -- it is in both plates and cancels. Nothing to do.
  it is picked up -- it is in the before plate only, so it appears as a
                     DEPARTURE. Reported separately rather than
                     ignored: an operator looking at a confusing answer
                     deserves to know a ball left the green.
  another lands   -- two arrivals. Both are reported, ranked; nothing
                     here can know which one was the shot, and guessing
                     silently is worse than saying there were two.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("golfreelz.greenrest")

# HOW MANY FRAMES GO INTO A PLATE. Enough that a swaying bush visits
# both sides of its arc and medians to the middle; not so many that
# reading them dominates the run. At 60 fps 31 frames spread over a few
# seconds is about a full cycle of most wind movement.
PLATE_FRAMES = 31

# ...SPREAD OVER THIS MANY SECONDS. Consecutive frames are the wrong
# thing to average: at 60 fps thirty of them are half a second, over
# which a bush has barely moved and the median is just that bush. The
# point is to decorrelate the clutter, which takes seconds, not frames.
PLATE_SPAN_SEC = 4.0

# HOW MUCH BRIGHTER THE BALL HAS TO BE than the plate under it, in grey
# levels. A white ball on mown turf is a long way clear of this; the
# number is set by what the turf itself does between two plates, which
# is mostly mowing stripes shifting in the light.
DIFF_THRESHOLD = 18

# BALL-SIZED, IN PIXELS OF AREA. Wide, because a green camera sees the
# front of the green at a very different scale from the back. The job
# of this filter is only to throw out single-pixel noise at one end and
# a golfer's shirt at the other.
MIN_AREA = 2
MAX_AREA = 400

# A BALL IS ROUND. A mowing stripe, a shadow edge and a sprinkler line
# are not, and they are what survives the area filter. Measured as the
# bounding box's long side over its short side.
MAX_ASPECT = 3.0

# IS IT STILL THERE WHEN YOU LOOK AT SINGLE FRAMES? The median can be
# fooled by something that sat still for half the window. A ball that
# has come to rest is in EVERY frame of the after window, so check a
# handful of them individually and demand most of them agree.
PERSIST_SAMPLES = 7
PERSIST_FRAC = 0.7

# How much of the full threshold a single frame has to clear. Lower
# than the plate's, because one frame carries its own noise where the
# median does not.
PERSIST_REL = 0.6


def _frame_count(cap) -> int:
    return int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)


def _read_at(cap, idx: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0, idx)))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _plate(cap, centre: int, fps: float, n: int = PLATE_FRAMES,
           span_sec: float = PLATE_SPAN_SEC):
    """The median of `n` frames spread across `span_sec` around `centre`.

    Spread, not consecutive — see PLATE_SPAN_SEC. Returns None if the
    clip could not give us enough frames to median honestly.
    """
    total = _frame_count(cap)
    half = max(1, int(round(span_sec * max(1.0, fps) / 2.0)))
    lo, hi = max(0, centre - half), min(total - 1, centre + half)
    if hi <= lo:
        return None
    idxs = np.linspace(lo, hi, num=max(3, n)).round().astype(int)
    frames = []
    for i in idxs:
        g = _read_at(cap, int(i))
        if g is not None:
            frames.append(g)
    if len(frames) < 3:
        return None
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def polygon_mask(polygon, shape, drawn_at=None):
    """A filled mask from [[x,y], ...], rescaled if it was drawn on a
    differently-sized frame. None polygon means the whole frame."""
    h, w = shape[:2]
    if not polygon:
        return np.full((h, w), 255, np.uint8)
    pts = np.array(polygon, dtype=np.float64)
    if drawn_at:
        dw, dh = float(drawn_at[0] or w), float(drawn_at[1] or h)
        if dw > 0 and dh > 0:
            pts[:, 0] *= w / dw
            pts[:, 1] *= h / dh
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts.round().astype(np.int32)], 255)
    return mask


def _blobs(diff_mask, diff, mask):
    """Ball-shaped components of a thresholded difference."""
    n, _lbl, stats, cent = cv2.connectedComponentsWithStats(
        cv2.bitwise_and(diff_mask, mask))
    out = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if not (MIN_AREA <= area <= MAX_AREA):
            continue
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        long_, short_ = max(bw, bh), max(1, min(bw, bh))
        if long_ / short_ > MAX_ASPECT:
            continue
        x, y = int(round(cent[i][0])), int(round(cent[i][1]))
        out.append({
            "x": x, "y": y, "area": area,
            "strength": int(abs(int(diff[y, x]))) if (
                0 <= y < diff.shape[0] and 0 <= x < diff.shape[1]) else 0,
        })
    return out


def _persistence(cap, blob, plate_before, centre, fps, mask_shape):
    """In how many individual after-frames is this blob actually there?

    The median's blind spot: something that sat still for half the
    window shows up in it. A ball at rest is in every frame.
    """
    total = _frame_count(cap)
    half = max(1, int(round(PLATE_SPAN_SEC * max(1.0, fps) / 2.0)))
    lo, hi = max(0, centre - half), min(total - 1, centre + half)
    idxs = np.linspace(lo, hi, num=PERSIST_SAMPLES).round().astype(int)
    x, y = blob["x"], blob["y"]
    r = 2
    h, w = mask_shape[:2]
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    base = float(plate_before[y0:y1, x0:x1].mean())
    hits = 0
    seen = 0
    for i in idxs:
        g = _read_at(cap, int(i))
        if g is None:
            continue
        seen += 1
        if float(g[y0:y1, x0:x1].mean()) - base >= DIFF_THRESHOLD * PERSIST_REL:
            hits += 1
    return (hits / seen) if seen else 0.0


def find_rest_spot(input_path, polygon=None, drawn_at=None,
                   before_frame: int | None = None,
                   after_frame: int | None = None) -> dict:
    """What arrived on the green, and what left it.

    `before_frame` / `after_frame` are the CENTRES of the two plates.
    Left unset they default to near the start and near the end of the
    clip, which is the question "where did it end up" asked of the
    whole capture.
    """
    path = Path(input_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"ok": False, "error": f"could not open {path.name}"}
    try:
        total = _frame_count(cap)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
        if total < 10:
            return {"ok": False, "error": "clip too short to plate"}
        pad = int(round(PLATE_SPAN_SEC * fps / 2.0)) + 1
        b = int(before_frame if before_frame is not None else pad)
        a = int(after_frame if after_frame is not None else total - 1 - pad)
        if a <= b:
            return {"ok": False,
                    "error": "the after plate must come after the before one"}

        plate_b = _plate(cap, b, fps)
        plate_a = _plate(cap, a, fps)
        if plate_b is None or plate_a is None:
            return {"ok": False, "error": "not enough frames to build a plate"}

        mask = polygon_mask(polygon, plate_a.shape, drawn_at)
        diff = plate_a.astype(np.int16) - plate_b.astype(np.int16)
        pos = (diff >= DIFF_THRESHOLD).astype(np.uint8) * 255
        neg = (diff <= -DIFF_THRESHOLD).astype(np.uint8) * 255

        arrivals = _blobs(pos, diff, mask)
        departures = _blobs(neg, diff, mask)

        for blob in arrivals:
            blob["persist"] = round(
                _persistence(cap, blob, plate_b, a, fps, plate_a.shape), 2)
        arrivals = [x for x in arrivals if x["persist"] >= PERSIST_FRAC]
        # Steadiest first, then brightest. Not biggest: at the back of a
        # green the ball is a handful of pixels and the biggest thing
        # that survives the filters is more likely to be a shadow.
        arrivals.sort(key=lambda z: (-z["persist"], -z["strength"]))

        notes = []
        if not polygon:
            notes.append(
                "No green outline for this camera, so the whole frame was "
                "searched — expect fairway and brush in the candidates.")
        if len(arrivals) > 1:
            notes.append(
                f"{len(arrivals)} things arrived on the green. If a playing "
                "partner also hit in, nothing here can tell which ball was "
                "the shot — pick the one that matches the flight.")
        if departures:
            notes.append(
                f"{len(departures)} thing(s) LEFT the green between the two "
                "plates — most likely a ball that was already there being "
                "marked or picked up. Harmless, and noted so the picture "
                "makes sense.")
        if not arrivals:
            notes.append(
                "Nothing new is on the green. Either the shot missed it, or "
                "the ball came to rest outside the outline.")

        return {
            "ok": True,
            "best": arrivals[0] if arrivals else None,
            "arrivals": arrivals,
            "departures": departures,
            "before_frame": b,
            "after_frame": a,
            "frame_size": [int(plate_a.shape[1]), int(plate_a.shape[0])],
            "fps": round(fps, 3),
            "notes": notes,
        }
    finally:
        cap.release()
