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
#
# WAS 1.5, WHICH WAS TUNED ON FIXTURES AND IS FAR TOO LOOSE ON GRASS.
# Real 1080p turf under H.264 is full of small bright things that sit
# still for a moment and then quantise away. Measured on upload 1259
# (back tees, impact at frame 1077 = 17.949s), zoned to the hitting
# area:
#
#   rest_sec   windows returned   real swing among them
#      1.5           36                  yes
#      3.0           26                  yes
#      5.0           16                  yes
#      8.0            8                  yes   <- 5 of the 8 ARE the swing
#
# A teed ball sits while its player addresses it; on that clip it sat
# for 17.65 s. Compression noise does not. Length of rest is the
# cheapest thing that separates them, and it costs nothing to ask for.
# 5.0 is the compromise shipped here: 8.0 was better on the one real
# clip measured, but a player who walks up and hits quickly would miss
# it, and one clip is not enough to spend that. Both are tunable from
# the Test cut UI -- sweep them per course.
REST_SEC = 5.0
# ...and "still" means this still. Measured as the spread of its
# positions, not drift from the first one, so a slow crawl fails it.
#
# WAS 4.0, AND IT REJECTED A REAL BALL BY A TENTH OF A PIXEL. On 1259
# the detector found the ball, tracked it sitting for 17.65 s, saw it
# vanish at the right moment -- and threw it out for "moved 4.1px
# while sitting". A drawn fixture ball does not move at all; an
# 8-pixel blob on real turf wobbles as the encoder re-quantises it.
REST_JITTER_PX = 6.0

# ABSENT THIS LONG, AND NEVER SEEN AGAIN. The second half is what makes
# a club at address harmless: an occlusion ends.
GONE_SEC = 0.8

# A GAP LONGER THAN THIS STARTS A NEW BALL, rather than continuing the
# old one. Without it, matching purely on position merges every ball
# ever teed on the same spot into a single track, and a clip with four
# swings in it reports one -- the failure this detector exists to
# avoid, arriving quietly.
#
# WAS 3.0, AND THAT WAS TOO SHORT. A hide longer than the gap ends the
# track, so the hide itself reads as a strike: measured on a fixture
# where the ball was obscured for 4.5s and struck at 26s, the detector
# returned 11.93 AND 25.93 -- one real swing and one invented. Eight
# seconds covers any plausible address, and the thing it must not
# swallow is two balls teed on the same spot, which takes a player
# walking up and setting up again. Nobody does that inside eight
# seconds.
SPLIT_GAP_SEC = 8.0


def _detections(gray, roi_boxes, rel: float = 0.55):
    """Small, round, locally bright things inside the boxes.

    `rel` scales the threshold off the BRIGHTEST thing in the box.
    That is right for a flight, where the ball in the air is the
    brightest thing there is -- and wrong for a ball sitting on a tee
    beside a golfer in a white shirt, because then the shirt sets the
    bar and the ball never clears it. Measured on the 8 Nov clip: the
    ball's top-hat response is 85-102 across the six frames, steady
    and strong, while the brightest thing in the crop is 202, so the
    bar sits at 111 and the ball is refused in every frame. Worse, it
    is refused INCONSISTENTLY -- whichever frame happens to have the
    dimmest highlight is the one where the ball survives, which is
    exactly how a ball visible in all six comes back found in two.
    Pass rel=0 to threshold on absolute local contrast instead.
    """
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
        thr = max(MIN_LOCAL_CONTRAST, float(rel) * float(hat.max()))
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
                     rest_jitter_px: float = REST_JITTER_PX,
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
        elif spread > rest_jitter_px:
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


# ---- the ball on the tee, across the six judged frames ---------------

# HOW STILL "the exact same spot" HAS TO BE. A ball at address does
# not move at all; this is sampling and sub-pixel centroid noise, not
# real travel. Tighter than MATCH_PX above, which has to survive a
# ball rolling out after a chip.
ADDRESS_JITTER_PX = 4.0

# AND HOW MUCH BRIGHTER THAN ITS SURROUNDINGS before it is called a
# ball at all. _detections already applies MIN_LOCAL_CONTRAST; this
# is a second, higher bar for THIS use, because the operator's
# requirement is explicit: "the ball needs to be clearly identified -
# i don't want a white shoe or skin identified as a ball". A shoe is
# large and fails on area; skin is not much brighter than sunlit
# grass and fails here.
ADDRESS_MIN_CONTRAST = 30.0

# A ball at address is SMALL, and how small depends on how big the
# golfer is in the picture -- an absolute pixel count means nothing
# across crops that are scaled to fit a tile. A golf ball is 1.68
# inches against a golfer of about 70, so a ball is ~0.024 of their
# height across. Doubled for bloom and blur: anything wider than
# this fraction of the golfer is not a ball.
BALL_MAX_DIAM_OVER_GOLFER = 0.05

# The fallback when nobody was detected and there is no golfer to
# measure against.
ADDRESS_MAX_AREA = 250

# AND A BALL IS NOT ON A PERSON. This is the filter that does the
# work the operator asked for -- "i don't want a white shoe or skin
# identified as a ball". A glove, a shoe, a white cap, a sleeve, a
# patch of forearm: every one of them is attached to a body and
# lands INSIDE that body's detection box. A ball sitting on the turf
# does not. Measured on the 8 Nov frames, where the thing it found
# before this filter went in was a golfer's white glove at his hip,
# an 80 px blob holding still across all six frames -- exactly the
# shape of evidence this check is supposed to produce, and entirely
# wrong.


# A BALL IS ON THE GROUND, and so is the golfer. Their feet and the
# ball are therefore at nearly the same height in the picture: a ball
# teed further from the camera sits higher, one nearer sits lower,
# and perspective bounds both. Without this the check reaches up into
# the background and calls a bright leaf in a tree a ball -- which it
# did, on the 8 Nov frames, at a fifth of the way down a crop whose
# golfer's feet were at four fifths.
#
# As fractions of the golfer's height, above and below their feet.
# Generous upward, because the tee can be well behind where they
# stand; tight downward, because nothing is closer to the camera than
# the near edge of the tee.
BALL_BAND_ABOVE_FEET = 0.35
BALL_BAND_BELOW_FEET = 0.15


def _on_the_ground(y, people) -> bool:
    """Is this height plausible for a ball, given where people stand?

    True when nobody was detected: absence of evidence is not a
    reason to refuse, and the person filter above is the one doing
    the heavy lifting.
    """
    bands = []
    for p in people or []:
        b = p.get("box") if isinstance(p, dict) else p
        if not b or b[3] <= 0:
            continue
        feet = b[1] + b[3]
        bands.append((feet - BALL_BAND_ABOVE_FEET * b[3],
                      feet + BALL_BAND_BELOW_FEET * b[3]))
    if not bands:
        return True
    return any(lo <= y <= hi for lo, hi in bands)


# HOW FAR OUTSIDE A PERSON'S BOX STILL COUNTS AS ON THEM. A
# detection box is drawn to the body it found and a white SHOE pokes
# out of the bottom of it -- which is how, on 8 Nov, a shoe was
# reported as a ball that "was NOT hit", in the right size, the right
# brightness, perfectly still, just outside the box. As fractions of
# the box, so it scales with how far away they are standing.
PERSON_PAD_W = 0.12
PERSON_PAD_H = 0.06


def _on_a_person(x, y, people) -> bool:
    """Is this point inside somebody's detection box, or just off it?"""
    for p in people or []:
        b = p.get("box") if isinstance(p, dict) else p
        if not b:
            continue
        px, py = PERSON_PAD_W * b[2], PERSON_PAD_H * b[3]
        if (b[0] - px <= x <= b[0] + b[2] + px
                and b[1] - py <= y <= b[1] + b[3] + py):
            return True
    return False


def classify_balls(good, tail_live, jitter_px=ADDRESS_JITTER_PX):
    """Split the balls found before the strike into gone and stayed.

    THE +0.00 FRAME IS THE STRIKE ITSELF and says nothing. The
    operator's reason is better than the one first written here: the
    audio trigger LAGS, so +0.00 is usually a little after the sound
    and typically still shows the ball sitting there. On the 8 Nov
    clip it is already gone by then. Either way the frame cannot be
    read, which is why it is excluded rather than weighed.

    Only the frames AFTER it answer the question, and they have to
    AGREE: absent from all of them is a ball that left, present in
    all of them is a ball nobody touched, one of each is no answer.
    The operator wants it that way and the detector can now deliver
    it -- the reason a ball visible in all six frames was coming back
    found in two was the relative threshold in _detections, not the
    ball, and that is fixed at the source rather than papered over
    here by accepting a single sighting.

    EVERY BALL, NOT THE BEST ONE. Judging the brightest or steadiest
    alone would call a real swing "not a swing" because some other
    ball, never touched, sat exactly where it was the whole time.
    Each entry gains `still`, how many of the frames after the strike
    still show it.

    I FIRST JUSTIFIED THIS BY SAYING THE 8 NOV FRAMES HAD THREE BALLS
    ON THE TURF. They do not. Asked to point them out, I found two
    bright things at -1.20s and only one of them -- the ball the
    golfer then struck -- is a ball in every frame before the strike;
    the other appears once and is gone. The rule still earns its
    place on a range tee or a practice green, where several balls
    genuinely do sit together, but it was argued from a count I had
    not checked.
    """
    def _there(c, dets):
        return any(((x - c["x"]) ** 2 + (y - c["y"]) ** 2) ** 0.5
                   <= float(jitter_px) for (x, y, _a, _c) in dets)

    late = tail_live[1:] if len(tail_live) > 1 else tail_live
    gone, stayed = [], []
    for c in good:
        c["still"] = sum(1 for d in tail_live if _there(c, d))
        if not late:
            continue
        hits = sum(1 for d in late if _there(c, d))
        if hits == 0:
            gone.append(c)
        elif hits == len(late):
            stayed.append(c)
    return gone, stayed


def ball_at_address(video_path, at_sec: float, offsets, *, rect=None,
                    people=None,
                    before: int = 3, jitter_px: float = ADDRESS_JITTER_PX,
                    min_contrast: float = ADDRESS_MIN_CONTRAST,
                    max_area: int = ADDRESS_MAX_AREA) -> dict:
    """Was a ball sitting there before the strike, and gone after?

    THE OPERATOR'S RULE, 8 Nov: find the ball in at least 2 of the
    first 3 of the six judged frames, in the same spot each time --
    it is at address, it does not move. If it is then absent from the
    last frames, the golfer hit it, and that is about as direct a
    piece of evidence as this system can get: a ball does not leave a
    tee by itself.

    "If the ball cannot be clearly identified because it's obstructed
    then don't make a judgement based on ball presence." So the
    absence of a ball is never evidence of anything here. Only a ball
    that was definitely THERE and then definitely GONE counts, and
    everything else returns left=None.

    Returns {"left": True|False|None, "spot": [x, y]|None,
    "seen_before": n, "seen_after": n, "why": str}, in the frame's
    own pixels. Never raises.
    """
    import cv2

    out = {"left": None, "spot": None, "seen_before": 0, "seen_after": 0,
           "balls": 0, "why": "not checked"}
    cap = None
    try:
        offs = list(offsets or ())
        if len(offs) < 4:
            out["why"] = "needs the six frames"
            return out
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            out["why"] = "could not open the clip"
            return out
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if rect:
            x0 = max(0, min(W - 1, int(rect[0])))
            y0 = max(0, min(H - 1, int(rect[1])))
            x1 = max(x0 + 1, min(W, int(rect[2])))
            y1 = max(y0 + 1, min(H, int(rect[3])))
        else:
            x0, y0, x1, y1 = 0, 0, W, H
        boxes = [(x0, y0, x1, y1)]
        # SCALED TO THE GOLFER when there is one to scale to.
        cap_area = int(max_area)
        _hs = [p["box"][3] for p in (people or [])
               if isinstance(p, dict) and p.get("box")]
        if _hs:
            _d = BALL_MAX_DIAM_OVER_GOLFER * (sum(_hs) / float(len(_hs)))
            cap_area = max(4, int(_d * _d))

        per_frame = []
        for off in offs:
            f = int(round((float(at_sec) + float(off)) * fps))
            if f < 0:
                per_frame.append(None)
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            if not ok or fr is None:
                per_frame.append(None)
                continue
            gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            # ABSOLUTE, NOT RELATIVE. See _detections: a golfer in a
            # white shirt standing over the ball otherwise sets a bar
            # the ball cannot clear. The ADDRESS_MIN_CONTRAST filter
            # below is the real bar here.
            dets = [d for d in _detections(gray, boxes, rel=0.0)
                    if d[2] <= int(cap_area) and d[3] >= float(min_contrast)
                    and not _on_a_person(d[0], d[1], people)
                    and _on_the_ground(d[1], people)]
            per_frame.append(dets)

        head = per_frame[:int(before)]
        tail = per_frame[int(before):]
        # THE SAME SPOT IN AT LEAST TWO OF THE FIRST THREE. Cluster
        # the pre-strike detections by position; a ball at address
        # lands in the same few pixels every time, a flicker of
        # sunlit grass does not.
        clusters: list = []
        for dets in head:
            for (x, y, area, con) in (dets or []):
                hit = None
                for c in clusters:
                    if ((x - c["x"]) ** 2 + (y - c["y"]) ** 2) ** 0.5 \
                            <= float(jitter_px):
                        hit = c
                        break
                if hit is None:
                    clusters.append({"x": x, "y": y, "n": 1,
                                     "con": con, "frames": 1})
                else:
                    hit["x"] = (hit["x"] * hit["n"] + x) / (hit["n"] + 1)
                    hit["y"] = (hit["y"] * hit["n"] + y) / (hit["n"] + 1)
                    hit["n"] += 1
                    hit["con"] = max(hit["con"], con)
                    hit["frames"] += 1
        good = [c for c in clusters if c["frames"] >= 2]
        if not good:
            out["why"] = ("no ball clearly in the same spot in 2 of the "
                          "first %d frames — no judgement from the ball"
                          % len(head))
            return out
        # EVERY BALL, NOT THE BEST ONE: the one that matters is
        # whichever LEFT. See classify_balls.
        tail_live = [d for d in tail if d is not None]
        gone, stayed = classify_balls(good, tail_live, jitter_px)
        out["balls"] = len(good)
        if gone:
            best = max(gone, key=lambda c: (c["frames"], c["con"]))
            out["spot"] = [int(round(best["x"])), int(round(best["y"]))]
            out["seen_before"] = int(best["frames"])
            out["seen_after"] = int(best["still"])
            out["left"] = True
            out["why"] = ("a ball sat at (%d, %d) in %d of the first %d "
                          "frames and is gone from every frame after the "
                          "strike%s" % (out["spot"][0], out["spot"][1],
                                        out["seen_before"], len(head),
                                        (" (%d balls on the turf)"
                                         % len(good)) if len(good) > 1
                                        else ""))
            return out
        if stayed:
            best = max(stayed, key=lambda c: (c["still"], c["frames"]))
            out["spot"] = [int(round(best["x"])), int(round(best["y"]))]
            out["seen_before"] = int(best["frames"])
            out["seen_after"] = int(best["still"])
            out["left"] = False
            out["why"] = ("the ball is still at (%d, %d) in %d frames "
                          "after the strike and no ball on the turf left "
                          "its spot" % (out["spot"][0], out["spot"][1],
                                        best["still"]))
            return out
        best = max(good, key=lambda c: (c["frames"], c["con"]))
        out["spot"] = [int(round(best["x"])), int(round(best["y"]))]
        out["seen_before"] = int(best["frames"])
        out["seen_after"] = int(best.get("still") or 0)
        out["why"] = ("a ball sat at (%d, %d) before the strike but what "
                      "happened to it is not clear"
                      % (out["spot"][0], out["spot"][1]))
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("ball_at_address failed (%s)", exc)
        out["why"] = "ball check failed"
        return out
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
