"""Find a swing by the ball leaving, not by the golfer moving.

WHY THIS AND NOT MOTION. Eight clips from hole 8 settled it: mean
frame-difference, a top-k reduction, optical flow and a rolling
baseline all failed to separate a swing from a group milling about,
because every one of them asks "did a lot of pixels change somewhere",
and three people walking change more pixels than one club ever will.

This asks a much narrower question with a much sharper answer: was
there a small bright thing sitting perfectly still in the hitting
area, and did it vanish? A struck ball goes from present to absent
between two frames and never comes back, which is not something
anything else on a tee box does.

THE TWO WAYS THIS HAS FAILED BEFORE, and what answers them here:

  WHITE SHOES. A shoe is bright, roughly ball-coloured, and sits near
  the ball. Three things separate them, and the operator has been
  bitten by detectors that used only the first:
    - size and shape (a shoe is bigger and not round), AND
    - STILLNESS: a ball at address does not move by one pixel for
      seconds, while a golfer shifts their feet constantly, AND
    - the manner of leaving: a shoe WALKS out of frame over many
      frames, moving first; a ball is there and then is not.
  The last one is the strongest and the cheapest, because it needs no
  judgement about what the thing looked like.

  THE CLUB AT ADDRESS. A clubhead placed behind the ball hides it, and
  a waggle hides it repeatedly, for a few tenths of a second each
  time. Every one of those is a disappearance. So a disappearance only
  counts when the ball NEVER COMES BACK -- which is true of a strike
  and false of every occlusion, without having to model the club at
  all.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# A ball at 1080p from the tee camera is a handful of pixels across.
# The range is generous at the top end because a sunlit ball blooms.
BALL_MIN_AREA = 3
BALL_MAX_AREA = 400

# Roundness, as area over the area of its bounding box. A ball fills
# most of its box (pi/4 = 0.79 for a perfect circle); a shoe, a tee
# marker edge or a strip of sunlit grass does not.
MIN_FILL = 0.55
# ...and its box must not be a sliver.
MAX_ASPECT = 2.2

# How much brighter than its surroundings. Mown grass in low sun has
# bright patches; they are not brighter than what is right beside them.
MIN_LOCAL_CONTRAST = 18.0

# A detection this far from a track joins it rather than starting a new
# one. Generous enough for sampling jitter, tight enough that two balls
# a foot apart stay separate.
MATCH_PX = 6.0

# HOW LONG IT MUST SIT STILL to count as a ball at address. This is the
# shoe filter doing its work: the thing has to be motionless for
# seconds before anybody cares that it left.
REST_SEC = 1.5
# ...and "still" means this still. Measured as the spread of its
# positions, not drift from the first one, so a slow crawl fails it.
REST_JITTER_PX = 4.0

# ABSENT THIS LONG, AND NEVER SEEN AGAIN. The second half is what makes
# a club at address harmless: an occlusion ends.
GONE_SEC = 0.8

# A GAP LONGER THAN THIS STARTS A NEW BALL, rather than continuing the
# old one. Without it, matching purely on position merges every ball
# ever teed on the same spot into a single track, and a clip with four
# swings in it reports one -- which is the failure this detector exists
# to avoid. Three seconds is comfortably longer than a club hiding the
# ball at address and far shorter than the minutes between players.
SPLIT_GAP_SEC = 3.0


def _detections(gray, roi_boxes):
    """Small, round, locally bright things inside the boxes."""
    import cv2
    import numpy as np

    out = []
    for (x0, y0, x1, y1) in roi_boxes:
        sub = gray[y0:y1, x0:x1]
        if sub.size == 0:
            continue
        # White top-hat: what is brighter than its own neighbourhood.
        # A plain threshold picks up the whole sunlit half of the box;
        # this picks up only things that stand out from what surrounds
        # them, which is the definition being used here anyway.
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        hat = cv2.morphologyEx(sub, cv2.MORPH_TOPHAT, k)
        if hat.max() < MIN_LOCAL_CONTRAST:
            continue
        thr = max(MIN_LOCAL_CONTRAST, 0.55 * float(hat.max()))
        _, mask = cv2.threshold(hat, thr, 255, cv2.THRESH_BINARY)
        n, _lab, stats, cents = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if not (BALL_MIN_AREA <= area <= BALL_MAX_AREA):
                continue
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            if w == 0 or h == 0:
                continue
            if max(w / h, h / w) > MAX_ASPECT:
                continue
            if area / float(w * h) < MIN_FILL:
                continue
            cx, cy = cents[i]
            out.append((float(cx) + x0, float(cy) + y0, area,
                        float(hat[int(cents[i][1]), int(cents[i][0])])))
    return out


def _track(frames):
    """Join per-frame detections into tracks by position.

    `frames` is [(t, [(x, y, area, contrast), ...]), ...].
    """
    tracks: list = []
    for t, dets in frames:
        for (x, y, area, contrast) in dets:
            best, best_d = None, None
            for tr in tracks:
                if t - tr["t"][-1] > SPLIT_GAP_SEC:
                    continue
                lx, ly = tr["x"][-1], tr["y"][-1]
                d = ((x - lx) ** 2 + (y - ly) ** 2) ** 0.5
                if d <= MATCH_PX and (best_d is None or d < best_d):
                    best, best_d = tr, d
            if best is None:
                tracks.append({"t": [t], "x": [x], "y": [y],
                               "area": [area], "contrast": [contrast]})
            else:
                best["t"].append(t)
                best["x"].append(x)
                best["y"].append(y)
                best["area"].append(area)
                best["contrast"].append(contrast)
    return tracks


def find_ball_vanish(video_path, *, zones=None, zones_fractional: bool = False,
                     sample_hz: float = 20.0,
                     before_sec: float = 4.5, after_sec: float = 9.0,
                     rest_sec: float = REST_SEC,
                     gone_sec: float = GONE_SEC,
                     debug: Optional[dict] = None) -> list:
    """Windows around every ball that sat still and then vanished."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        boxes = []
        for b in (zones or []):
            if zones_fractional:
                x0, y0 = float(b["x"]) * W, float(b["y"]) * H
                w, h = float(b["w"]) * W, float(b["h"]) * H
            else:
                x0, y0, w, h = (float(b["x"]), float(b["y"]),
                                float(b["w"]), float(b["h"]))
            boxes.append((max(0, int(x0)), max(0, int(y0)),
                          min(W, int(x0 + w)), min(H, int(y0 + h))))
        if not boxes:
            boxes = [(0, 0, W, H)]

        step = max(1, int(fps / max(1.0, float(sample_hz)) + 0.5))
        frames = []
        i = 0
        while True:
            if not cap.grab():
                break
            if i % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                gray = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        if frame.ndim == 3 else frame)
                frames.append((i / fps, _detections(gray, boxes)))
            i += 1
        duration = (i / fps) if fps else 0.0
    finally:
        cap.release()

    tracks = _track(frames)
    last_t = frames[-1][0] if frames else 0.0

    kept, rejected = [], []
    for tr in tracks:
        ts = tr["t"]
        seen_for = ts[-1] - ts[0]
        spread = max(
            (max(tr["x"]) - min(tr["x"])), (max(tr["y"]) - min(tr["y"])))
        # Measured against the end of the SCAN, so a ball still sitting
        # there when the clip stops is not reported as struck.
        gone_for = last_t - ts[-1]
        why = None
        if seen_for < rest_sec:
            why = f"only sat for {seen_for:.1f}s"
        elif spread > REST_JITTER_PX:
            # THE SHOE TEST. Something that wandered while it sat is
            # attached to a person.
            why = f"moved {spread:.1f}px while sitting"
        elif gone_for < gone_sec:
            # Still there at the end of the clip: never struck.
            why = f"still present at the end ({gone_for:.1f}s)"
        if why:
            rejected.append({"at": round(ts[-1], 2), "why": why,
                             "seen_for": round(seen_for, 2),
                             "spread_px": round(spread, 1)})
            continue
        kept.append({
            "vanished_at": round(ts[-1], 2),
            "seen_for": round(seen_for, 2),
            "spread_px": round(spread, 1),
            "x": round(sum(tr["x"]) / len(tr["x"]), 1),
            "y": round(sum(tr["y"]) / len(tr["y"]), 1),
            "area": round(sum(tr["area"]) / len(tr["area"]), 1),
            "contrast": round(sum(tr["contrast"]) / len(tr["contrast"]), 1),
        })

    kept.sort(key=lambda k: k["vanished_at"])
    windows = [{
        "peak_time_sec": k["vanished_at"],
        "start_sec": round(max(0.0, k["vanished_at"] - before_sec), 2),
        "end_sec": round(min(duration, k["vanished_at"] + after_sec), 2),
        "ratio": k["contrast"],
        "confidence": "high" if k["seen_for"] >= 2 * rest_sec else "medium",
        "burst_duration_sec": None,
    } for k in kept]

    if debug is not None:
        debug.update({
            "balls": kept,
            # The near-misses, because "nothing found" is three
            # different problems and they need different fixes: no
            # bright thing at all (wrong box), plenty that moved (the
            # box has feet in it), or one that never left (no swing).
            "rejected": sorted(rejected,
                               key=lambda r: -r["seen_for"])[:12],
            "n_tracks": len(tracks),
            "duration_sec": round(duration, 1),
            "sample_hz": sample_hz,
            "zones_used": len(boxes) if zones else 0,
            "frames_scanned": len(frames),
        })
    return windows
