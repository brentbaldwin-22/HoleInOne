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
import math
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

# HOW FAR BELOW THE FLOOR TO LOOK WHEN NOTHING WAS FOUND. Reporting
# only: see the second pass in `ball_in_frames`. Low enough to catch a
# ball the floor is hiding, high enough that the turf's own texture
# does not fill the picture with marks.
UNDER_FLOOR_PROBE = 7.0

# THE SHAPE BARS INSIDE THE STANCE BOX. Looser than the wide search's
# for one measured reason: a blob at the contrast the box exists to
# reach is ragged, and MIN_FILL 0.55 refuses it on exactly the clips
# where lowering the floor was supposed to help. Still bars enough to
# throw out a blade of grass or a shoe edge -- a streak is a streak at
# any threshold.
REGION_MIN_FILL = 0.35
REGION_MAX_ASPECT = 2.6

# AND THE BAR ITSELF, WELL UNDER THE DETECTOR'S FLOOR, because that is
# the entire point of narrowing the search. Defaulting the box to
# MIN_LOCAL_CONTRAST made it a slower way to get today's answer: the
# ball that prompted all this reads about 11.
#
# What stops this admitting grass is not the bar. It is everything
# after it: ball-sized, inside a box the size of a stance, in the same
# few pixels in two of the three frames before the strike, and gone
# from every frame after. Turf texture does none of that.
REGION_CONTRAST = 8.0

# HOW MANY SPECKS A BOX MAY HOLD BEFORE IT HAS STOPPED HELPING.
#
# THE BAR IS ONLY SAFE BECAUSE THE BOX IS EMPTY. Drop it to 8 over a
# patch of grass that is genuinely quiet and a ball is the one thing
# in there -- measured, 1 detection. Drop it over turf whose texture
# reaches that far and the same box returns ninety-odd, and with that
# many candidates something lands in the same few pixels in two of the
# first three frames and nowhere after BY CHANCE. On synthetic noise
# that produced "a ball sat at (254, 198) ... and is gone from every
# frame after the strike (42 balls on the turf)" -- a confident false
# positive on a clip where nobody swung, which is the worst thing this
# detector can do.
#
# So the count is the premise, and the premise is checked: a crowded
# box is abandoned rather than believed, and the report says so. The
# wide search still stands, which is where this clip was anyway.
REGION_MAX_CANDIDATES = 6

# ── A BALL IN FLIGHT IS NOT ROUND ───────────────────────────────────
#
# AND MAX_ASPECT THREW EVERY ONE OF THEM AWAY. The two gates above
# describe a ball SITTING STILL, which is what they were written for
# and what they are measured against. A ball crossing thirty or forty
# pixels during one exposure is smeared into a capsule, and the two
# gates reject it from opposite directions depending only on which way
# it happens to be travelling:
#
#   along an axis   a 15x4 streak is 3.75:1 and dies on MAX_ASPECT
#   diagonally      the same streak has a near-square bounding box, so
#                   it passes MAX_ASPECT and dies on MIN_FILL instead,
#                   because a thin diagonal line fills about a third
#                   of the box drawn around it
#
# That is the whole reason the reverse descent walk has never once
# reached the flight: on the clip that prompted this it tracked 315
# frames of a ball ROLLING, where the gates are right, and lost it at
# the first frame the ball was in the air, where they are guaranteed
# to refuse it.
#
# So when a caller knows which way the ball is going, a stretched blob
# is allowed -- but only as far as that speed could have stretched it,
# and only if it is stretched ALONG the direction of travel. That
# second condition is what keeps a fence rail, a cart path edge or a
# bunker lip out: they are all far more elongated than any speed
# allows, and none of them points where the ball is going.
#
# Off unless a caller passes `motion`. Every existing caller -- the
# tee's ball at address, the green's ball at rest -- is asking about a
# stationary ball and gets exactly the behaviour it had before.

# Below this, in pixels per frame, the ball is not moving enough to
# smear and the round gates are simply correct. A ball rolling on a
# green is 1-2px/frame.
STREAK_MIN_SPEED = 6.0

# HOW MUCH OF THE FRAME INTERVAL THE SHUTTER IS OPEN, as a fraction,
# for working out how long a streak that speed could have produced.
# A capsule from a ball of width W moving s pixels per frame is about
# W + s*duty long, so the allowed elongation is 1 + s*duty/W.
#
# Set to 1.0, the widest it can physically be: the alignment test is
# what does the discriminating, and a too-generous length bound only
# admits longer streaks pointing exactly the right way. Too TIGHT a
# bound, on the other hand, silently refuses the real ball on any
# camera with a slower shutter than guessed -- which is the failure
# that is impossible to see from the outside.
STREAK_DUTY = 1.0

# HOW CLOSELY THE SMEAR MUST POINT ALONG THE TRAVEL, in degrees, on an
# axis (so 180 degrees out is the same line -- the walk runs backwards
# and a streak has no arrow on it).
STREAK_ALIGN_DEG = 30.0

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


def _oriented(lab, label, x, y, w, h):
    """The component's real long axis, short axis and angle.

    A BOUNDING BOX CANNOT MEASURE A DIAGONAL. That is the whole reason
    the two round gates disagree with each other about the same
    streak: `w` and `h` come from an axis-aligned box, so a 15x4
    capsule lying along x reads 3.75:1 and the same capsule at 45
    degrees reads about 1:1. One dies on aspect, the other on fill,
    and neither number describes the shape.

    These come from the component's own second moments, so they
    describe the blob and not the rectangle somebody drew round it.
    Returns (long, short, angle_deg, fill) with the lengths as the
    pixel EXTENT along each axis -- the oriented bounding box -- and
    `fill` the share of that box the component actually occupies.
    """
    import numpy as np

    sub = (lab[y:y + h, x:x + w] == label)
    ys, xs = np.nonzero(sub)
    n = xs.size
    if n < 2:
        return 1.0, 1.0, 0.0, 1.0
    fx = xs.astype(np.float64)
    fy = ys.astype(np.float64)
    mx, my = fx.mean(), fy.mean()
    dx, dy = fx - mx, fy - my
    cxx = float((dx * dx).mean())
    cyy = float((dy * dy).mean())
    cxy = float((dx * dy).mean())
    # Principal axis of the covariance: the direction the blob is
    # longest in. The factor of a half is the usual one for the
    # double-angle form.
    ang = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)
    ca, sa = math.cos(ang), math.sin(ang)
    # Project every pixel onto the two axes and take the real extent,
    # rather than inferring a length from the variance -- a capsule
    # and a dumbbell have the same variance and very different ends.
    p1 = dx * ca + dy * sa
    p2 = -dx * sa + dy * ca
    long_ = float(p1.max() - p1.min()) + 1.0
    short = float(p2.max() - p2.min()) + 1.0
    if short > long_:
        long_, short = short, long_
        ang += math.pi / 2.0
    fill = float(n) / max(1.0, long_ * short)
    return long_, short, math.degrees(ang) % 180.0, fill


def _axis_gap_deg(a, b):
    """Angle between two directions treated as AXES, 0..90.

    A streak has no arrow on it, and the reverse walk reads its
    velocity backwards anyway, so a smear 180 degrees from the
    travel is the same line and must not be refused for it.
    """
    d = abs((float(a) - float(b)) % 180.0)
    return min(d, 180.0 - d)


def _detections(gray, roi_boxes, rel: float = 0.55, rejects=None,
                stats_out=None, min_area=None, max_area=None,
                kernel_px: int = 15, motion=None, motion_toward=None,
                shapes_out=None, floor=None,
                min_fill=None, max_aspect=None):
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

    `rejects` and `stats_out` are optional bookkeeping, off by
    default so the rig's own path pays nothing for them. `rejects`
    collects every component that crossed the brightness threshold and
    then failed a gate, with the measurement and the limit;
    `stats_out` records the strongest top-hat response in each box
    whether or not anything passed. Between them they separate the
    four failures that "found nothing" used to cover: nothing was
    brighter than its surroundings at all, something was but it was
    one pixel, something was but it was a streak, something was but it
    was a blob the size of a shirt. They need opposite fixes, and the
    old output could not tell them apart -- which is how a green that
    plainly has a ball on it comes back empty with nothing to look at.

    `min_area` and `max_area` override BALL_MIN_AREA / BALL_MAX_AREA
    FOR THIS CALL, in the pixels of the image handed in. They exist
    for callers that scale a crop up before calling -- the green's
    zoned search and the reverse descent walk both do, because a ball
    a hundred yards out is three pixels across and the top-hat's 15px
    kernel swallows it. Those callers must scale the bounds by the
    square of the zoom, or the gate means something different from
    what it says: at 6x a 50px ball becomes 1800px of area and is
    thrown out by a MAX_AREA written for 400. Not a hypothetical --
    it is how the first reverse walk found the ball at rest in zero
    of the twenty-two frames it was plainly sitting in.

    `kernel_px` is the width of the top-hat's structuring element, and
    a caller that scales a crop up MUST scale this with it. The
    top-hat is "what is brighter than its own neighbourhood", and the
    neighbourhood is the kernel: once the ball is wider than the
    kernel, the opening keeps the ball's own interior and the top-hat
    returns a thin rim, which fragments into specks and fails the
    roundness and fill tests. Measured on one frame of a ball sitting
    on a green, at a fixed 15px kernel:

        1x  one blob, 49px, fill 0.60   -- the ball
        2x  one blob, 242px             -- still the ball
        3x  twenty-odd blobs of ~20px   -- the rim, in pieces
        6x  eighty-odd blobs of ~85px   -- the rim, in more pieces

    With the kernel scaled alongside, every zoom from 1x to 6x returns
    one blob with the same area and the same fill in the frame's own
    pixels. The detector becomes scale-free, which is the only thing
    that makes zooming worth doing.
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
        _kp = max(3, int(kernel_px) | 1)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_kp, _kp))
        hat = cv2.morphologyEx(sub, cv2.MORPH_TOPHAT, k)
        # THE FLOOR IS A BINARISATION THRESHOLD, NOT A FILTER, which
        # is the whole reason it needs an override. Everything under
        # it is never a connected component, so it cannot be reported
        # as refused -- it does not exist. A caller that has found
        # nothing and wants to know whether there was anything THERE
        # has no way to ask without lowering the threshold and
        # looking again. See ball_in_frames' second pass.
        _floor = (float(MIN_LOCAL_CONTRAST) if floor is None
                  else max(1.0, float(floor)))
        if stats_out is not None:
            stats_out.append({"box": [x0, y0, x1, y1],
                              "hat_max": int(hat.max()),
                              "needs": int(round(_floor))})
        if hat.max() < _floor:
            continue
        thr = max(_floor, float(rel) * float(hat.max()))
        _, mask = cv2.threshold(hat, thr, 255, cv2.THRESH_BINARY)
        n, _lab, stats, cents = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), 8)
        # ── WHICH WAY IS THE BALL GOING, IF THE CALLER KNOWS ────
        #
        # Everything below falls back to the round gates when it does
        # not. See STREAK_MIN_SPEED: a caller that passes no motion,
        # or motion too slow to smear anything, gets exactly the
        # behaviour this detector has always had.
        _spd = 0.0
        _mang = 0.0
        if motion:
            _mvx, _mvy = float(motion[0]), float(motion[1])
            _spd = (_mvx * _mvx + _mvy * _mvy) ** 0.5
            _mang = math.degrees(math.atan2(_mvy, _mvx)) % 180.0
        _streaking = _spd >= STREAK_MIN_SPEED

        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            cx, cy = cents[i]

            # ── THE DIRECTION A CANDIDATE WOULD HAVE TO BE GOING ────
            #
            # `motion` is one velocity for the whole call, which suits
            # a tracker that already knows where the ball is going.
            # `motion_toward` suits a SEARCH, and is stronger: given
            # that the ball is known to be at (tx, ty) `frames_ahead`
            # frames later, a blob here can only be the ball if it is
            # travelling towards that point, at the speed that trip
            # implies. Both are then fully determined by the blob's own
            # position -- nothing is guessed and nothing is carried
            # over from a previous frame.
            #
            # So the test becomes self-consistent: a blob is the ball
            # only if its own smear matches the journey it would have
            # had to make to BE the ball. A branch lying at the wrong
            # angle fails; a speck too far away to have got there
            # fails on the speed its own distance implies.
            if motion_toward is not None:
                _tx, _ty, _fa = motion_toward
                _ddx = float(_tx) - (float(cx) + x0)
                _ddy = float(_ty) - (float(cy) + y0)
                _dist = (_ddx * _ddx + _ddy * _ddy) ** 0.5
                _spd = _dist / max(1.0, float(_fa))
                _mang = math.degrees(math.atan2(_ddy, _ddx)) % 180.0
                _streaking = _spd >= STREAK_MIN_SPEED

            def _no(why, _cx=cx, _cy=cy, _a=area, _w=w, _h=h):
                if rejects is not None:
                    rejects.append({"x": float(_cx) + x0,
                                    "y": float(_cy) + y0,
                                    "area": _a, "w": _w, "h": _h,
                                    "why": why})

            _lo = BALL_MIN_AREA if min_area is None else float(min_area)
            _hi = BALL_MAX_AREA if max_area is None else float(max_area)
            if area < _lo:
                _no(f"{area}px of area, under {_lo:g}")
                continue
            if w == 0 or h == 0:
                _no("no width or no height")
                continue

            # ── THE ROUND GATES DECIDE FIRST, ALWAYS ────────────────
            #
            # Byte for byte what they were, and asked before anything
            # about motion. Everything the detector accepted before
            # the elongation gate existed, it still accepts: a caller
            # that passes `motion` gets a strict SUPERSET of what the
            # same call returned without it, and no clip can detect
            # less than it used to.
            #
            # THAT IS A PROPERTY, NOT AN INTENTION. Written as two
            # independent tests it was quietly violated at the
            # boundary -- a capsule measuring 2.23:1 on its own axes
            # failed the motion branch while the same blob measured
            # 2.2:1 on its bounding box and passed the round one, so
            # turning the gate ON lost a frame it used to find. The
            # two ratios are different measurements and MAX_ASPECT is
            # calibrated against the bounding box; comparing the
            # oriented one to it was never going to agree. Structured
            # this way the question cannot arise.
            # ROUNDNESS IS A BIG-AREA DISCRIMINATOR, and the caller
            # may not be searching a big area. Over a whole crop these
            # keep sunlit grass and tee markers out; inside a box the
            # size of a stance they are mostly refusing the ball,
            # because a blob near the noise floor is RAGGED -- measured
            # at a top-hat of 11, "fills 0.47 of its 5x6 box". The
            # overrides exist for that caller and nobody else.
            _fill_bar = MIN_FILL if min_fill is None else float(min_fill)
            _asp_bar = (MAX_ASPECT if max_aspect is None
                        else float(max_aspect))
            _why_round = None
            if area > _hi:
                _why_round = f"{area}px of area, over {_hi:g}"
            elif max(w / h, h / w) > _asp_bar:
                _why_round = (f"{w}x{h} — {max(w / h, h / w):.1f}:1, a "
                              f"sliver (limit {_asp_bar}:1)")
            elif area / float(w * h) < _fill_bar:
                _why_round = (f"fills {area / float(w * h):.2f} of its "
                              f"{w}x{h} box, needs {_fill_bar} — not round")

            if _why_round is None:
                if shapes_out is not None:
                    shapes_out.append({"x": float(cx) + x0,
                                       "y": float(cy) + y0,
                                       "area": area, "streak": False,
                                       "ratio": round(max(w / h, h / w), 2),
                                       "why": "round"})
            elif not _streaking:
                _no(_why_round)
                continue
            else:
                # ── THE ELONGATION GATE: A SECOND CHANCE, NOT A SECOND
                #    HURDLE ────────────────────────────────────────────
                #
                # Reached only by a blob the round gates have already
                # turned down. Measured on the blob's own axes,
                # because a bounding box cannot describe a diagonal --
                # see `_oriented`, and see the two ways the round
                # gates refuse the same capsule depending purely on
                # which way it is pointing.
                _L, _W, _ang, _fill = _oriented(
                    _lab, i,
                    int(stats[i, cv2.CC_STAT_LEFT]),
                    int(stats[i, cv2.CC_STAT_TOP]), w, h)
                _ratio = _L / max(_W, 1.0)
                # HOW LONG THAT SPEED COULD HAVE DRAWN IT. A ball of
                # width W crossing s pixels in one exposure is about
                # W + s*duty long, so this is the elongation the
                # motion itself accounts for.
                _allow = max(MAX_ASPECT, 1.0 + STREAK_DUTY * _spd
                             / max(_W, 1.0))
                _gap = _axis_gap_deg(_ang, _mang)
                # THE AREA BOUND GROWS WITH THE SMEAR, because a
                # streak of a ball IS bigger than the ball. Bounded
                # by the elongation actually measured, not the one
                # allowed, so a long smear cannot buy itself room.
                if area > _hi * max(1.0, _ratio):
                    _no(f"{area}px of area, over "
                        f"{_hi * max(1.0, _ratio):g} even allowing a "
                        f"{_ratio:.1f}:1 smear at {_spd:.0f}px/frame")
                    continue
                # THE WIDTH IS STILL A BALL'S WIDTH. Motion stretches
                # a ball along one axis and leaves the other alone,
                # so the short axis says whether this was ever
                # ball-sized. A bush does not pass.
                _w_hi = 2.0 * (_hi / math.pi) ** 0.5
                if _W > _w_hi:
                    _no(f"{_W:.0f}px across its short axis, wider than a "
                        f"ball ({_w_hi:.0f}px) — a smear is stretched, "
                        f"not fattened")
                    continue
                if _ratio > _allow:
                    _no(f"{_L:.0f}x{_W:.0f} — {_ratio:.1f}:1, longer than "
                        f"{_spd:.0f}px/frame could smear it "
                        f"({_allow:.1f}:1)")
                    continue
                if _gap > STREAK_ALIGN_DEG:
                    _no(f"{_ratio:.1f}:1 smear lying at {_ang:.0f}deg, "
                        f"{_gap:.0f}deg off the {_mang:.0f}deg it is "
                        f"travelling (limit {STREAK_ALIGN_DEG:.0f}deg)")
                    continue
                # FILL, ON THE ORIENTED BOX. The axis-aligned one is
                # what makes a diagonal streak fail while an identical
                # horizontal one passes.
                if _fill < MIN_FILL:
                    _no(f"fills {_fill:.2f} of its own {_L:.0f}x{_W:.0f} "
                        f"box, needs {MIN_FILL} — a smear is solid, an "
                        f"edge is not")
                    continue
                if shapes_out is not None:
                    shapes_out.append({
                        "x": float(cx) + x0, "y": float(cy) + y0,
                        "area": area, "streak": True,
                        "ratio": round(_ratio, 2),
                        "allowed": round(_allow, 2),
                        "angle": round(_ang, 1),
                        "travel": round(_mang, 1),
                        "off_by_deg": round(_gap, 1),
                        "long": round(_L, 1), "short": round(_W, 1),
                        "fill": round(_fill, 2),
                        "speed": round(_spd, 1),
                        "round_said": _why_round})
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
#
# "THE OBVIOUS THING IS THAT THE BALL IS GOING TO BE EVEN OR HIGHER
# THAN THE GREEN FOOT LINE" -- the operator, 8 Nov, and he is right:
# the ball is teed AHEAD of the stance, which from a camera behind
# the tee means at the foot line or above it. Never below. The old
# 0.15 downward allowance had nothing behind it and let a speck 44 px
# BELOW the golfer's feet be called a ball.
#
# Upward was 0.35, which admitted another at 0.24 of a golfer's
# height above the line -- well up the picture, nowhere near the tee.
# Measured against the real balls seen so far, which sit 0.14 of a
# height above the line at most.
BALL_BAND_ABOVE_FEET = 0.18
BALL_BAND_BELOW_FEET = 0.02


# ── WHERE A TEED BALL SITS ──────────────────────────────────────────
#
# THE OPERATOR DREW THIS BOX ON A REAL CLIP, and it is built from
# things that are measured rather than guessed:
#
#   left/right  from the FEET out to the edge of the crop, on the
#               side the ball is teed
#   top/bottom  the TRIGGER ZONE's own lines -- the magenta ones on
#               the strip, which is where a golfer may stand and so
#               where a ball may sit
#
# The previous version used fractions of the golfer's height for all
# four edges, which is a guess dressed as a measurement: the zone is
# drawn by the operator for this camera and this tee, and the crop
# edge is a fact. Nothing here is a body-height fraction except the
# small gap that keeps the shoes out.
# CLEAR OF THE INSTEP, in body heights. Was 0.06 -- seven pixels on a
# 125px golfer, which put the box's left edge practically on the shoe
# and let the stance's own highlights into a search deliberately run
# at a bar of 8. The operator drew the line they wanted further out.
BALL_BOX_FOOT_GAP = 0.20

# WHICH SIDE A RIGHT-HANDER'S BALL IS ON, IN IMAGE X.
#
# SET FROM THE OPERATOR'S OWN DRAWING on a real clip, where the ball
# sits to the RIGHT of the feet marker. Note that tracer.py's
# `_handedness_from_slopes` carries the opposite convention -- righty
# means clubhead LEFT of the hands -- and the two are not necessarily
# in conflict: that one is about a club shaft's slope at address seen
# from behind the line, this one about which way the ball is teed in
# the frames the judge is shown. They have never been checked against
# each other on one clip.
#
# One constant, so that when they are checked it is one edit.
RIGHTY_BALL_SIDE = "right"


def _flip(side):
    return "left" if side == "right" else "right"


def ball_region(*, feet_x, crop=None, zone_y=None, anchor=None,
                handed=None, ai_xy=None, frame=None):
    """The patch of grass to search, and the reasoning, as (box, why).

    THE WHOLE POINT IS THAT IT IS SMALL. Everywhere else the bars are
    tight because the search area is the whole crop: MIN_LOCAL_CONTRAST
    exists to stop the turf's own texture flooding the result, and
    below it the grass breaks into hundreds of single pixels. In a box
    this size there is almost nothing else to confuse, so the caller
    can afford a far lower bar -- which is the only thing that reaches
    a ball at 11 grey levels on sunlit grass.

    `ai_xy` is the swing judge's own guess at the ball, and it is used
    ONLY to pick the side. A vision model's pixel position is not good
    enough to centre a box on -- measured, on the clip that prompted
    this it landed on the golfer's legs -- but "left or right of the
    feet" is a far coarser question and it can answer that. When it
    does not clearly fall on one side, handedness decides; when that
    is unknown too, right-handed is assumed, as the operator asked.

    Returns (None, why) when the inputs cannot make a box, so the
    caller can say what was missing rather than merely finding
    nothing.
    """
    _why = []
    try:
        _fx = float(feet_x)
    except (TypeError, ValueError):
        return None, "no feet to measure from"

    # ── THE SIDE ────────────────────────────────────────────────────
    _side = None
    if ai_xy:
        try:
            _ax = float(ai_xy[0])
            # A POINT ON TOP OF THE FEET PICKS NOTHING. It has to be
            # clearly to one side to be evidence of a side.
            _gap = 0.0
            if anchor:
                _gap = BALL_BOX_FOOT_GAP * float(anchor[1] or 0)
            if abs(_ax - _fx) > max(4.0, _gap):
                _side = "right" if _ax > _fx else "left"
                _why.append("the judge put the ball %s of the feet"
                            % _side)
        except (TypeError, ValueError, IndexError):
            pass
    if _side is None and handed in ("right", "left"):
        _side = (RIGHTY_BALL_SIDE if handed == "right"
                 else _flip(RIGHTY_BALL_SIDE))
        _why.append("%s-handed, so the ball is teed to the %s"
                    % (handed, _side))
    if _side is None:
        _side = RIGHTY_BALL_SIDE
        _why.append("handedness unknown, assuming right-handed (%s)"
                    % _side)

    # ── TOP AND BOTTOM: THE TRIGGER ZONE'S OWN LINES ────────────────
    _y0 = _y1 = None
    if zone_y and len(zone_y) == 2:
        try:
            _y0, _y1 = float(zone_y[0]), float(zone_y[1])
            _why.append("down the trigger zone, y %d..%d"
                        % (int(_y0), int(_y1)))
        except (TypeError, ValueError):
            _y0 = _y1 = None
    if _y0 is None and anchor:
        # FALLBACK ONLY, for a camera with no zones drawn.
        try:
            _f, _h = float(anchor[0]), float(anchor[1])
            if _h > 0:
                _y0 = _f - BALL_BAND_ABOVE_FEET * _h
                _y1 = _f + BALL_BAND_BELOW_FEET * _h
                _why.append("no zones, so the foot band y %d..%d"
                            % (int(_y0), int(_y1)))
        except (TypeError, ValueError):
            _y0 = _y1 = None
    if _y0 is None:
        return None, "no trigger zone and no golfer to measure a band from"

    # ── LEFT AND RIGHT: THE FEET, OUT TO THE EDGE OF THE CROP ───────
    _gap = 4.0
    if anchor:
        try:
            _gap = max(4.0, BALL_BOX_FOOT_GAP * float(anchor[1] or 0))
        except (TypeError, ValueError):
            _gap = 4.0
    if crop and len(crop) == 4:
        _cx0, _cx1 = float(crop[0]), float(crop[2])
    elif frame:
        _cx0, _cx1 = 0.0, float(frame[0])
    else:
        return None, "no crop to run the box out to"
    if _side == "right":
        _x0, _x1 = _fx + _gap, _cx1
    else:
        _x0, _x1 = _cx0, _fx - _gap
    _why.append("from the feet out to the %s edge of the crop" % _side)

    if frame:
        _x0, _x1 = max(0.0, _x0), min(float(frame[0]), _x1)
        _y0, _y1 = max(0.0, _y0), min(float(frame[1]), _y1)
    if _x1 - _x0 < 4 or _y1 - _y0 < 4:
        return None, ("the box came out empty (%s) — the feet are at the "
                      "%s edge already" % (" · ".join(_why), _side))
    return (int(_x0), int(_y0), int(_x1), int(_y1)), " · ".join(_why)


def _on_the_ground(y, anchor) -> bool:
    """Is this height plausible for a ball at THIS golfer's feet?

    `anchor` is (feet_y, height) for the ONE golfer the crop was cut
    around. It used to be every detected person, with the band taken
    as the union -- and a playing partner bending over at a different
    depth widened that union until it covered turf 44 px below the
    picked golfer's own feet, which is how a speck down there came
    back as a ball.

    True when there is no anchor: absence of evidence is not a reason
    to refuse, and the on-a-person filter is the heavy lifting.
    """
    if not anchor:
        return True
    try:
        feet, h = float(anchor[0]), float(anchor[1])
    except Exception:  # noqa: BLE001
        return True
    if h <= 0:
        return True
    return (feet - BALL_BAND_ABOVE_FEET * h <= y
            <= feet + BALL_BAND_BELOW_FEET * h)


# HOW FAR OUTSIDE A PERSON'S BOX STILL COUNTS AS ON THEM. A
# detection box is drawn to the body it found and a white SHOE pokes
# out of the bottom of it -- which is how, on 8 Nov, a shoe was
# reported as a ball that "was NOT hit", in the right size, the right
# brightness, perfectly still, just outside the box. As fractions of
# the box, so it scales with how far away they are standing.
# SIDEWAYS IS NARROW ON PURPOSE. The shoe that started this poked
# out of the BOTTOM of the box; padding the sides as generously would
# swallow a ball teed a club-head's width from the stance, which is
# where a ball at address actually sits.
PERSON_PAD_W = 0.04
PERSON_PAD_H = 0.06

# HOW FAR INSIDE THE BOX A SHOE HAS TO BE, down at ground level, as a
# fraction of the box width taken off each side.
#
# AT ADDRESS THE BALL SITS AT THE EDGE OF THE STANCE -- teed off the
# lead heel -- and the box edge plus PERSON_PAD_W is exactly there, so
# the rule that keeps a shoe out was keeping the ball out with it.
# Reported from the field on a clip whose six tiles show the whole
# swing: "the ball isn't really close at all to the shoes."
#
# THE SHOES ARE IN THE MIDDLE, WHICH IS THE WAY OUT. On the 8 Nov
# crop -- box [80, 26, 94, 208], centre x=127 -- the two shoes the
# detector actually picked sat at x=120 and x=127, within 7 px of
# that centre line, while the real ball sat at x=201, well outside
# the box. Nothing real was ever seen out at the edge; the edge was
# guarded on principle, and the principle cost more than it saved.
#
# SO AT GROUND LEVEL THE SIDES COME IN INSTEAD OF GOING OUT. A speck
# has to be properly within the stance to be called a shoe, rather
# than merely level with its outside edge. Vertically nothing
# changes: the shoe that started all this poked out of the BOTTOM of
# the box, and PERSON_PAD_H still catches it.
#
# AND THE REAL BACKSTOP IS TIME, not geometry. Anything this now lets
# through still has to be in the same few pixels before the strike
# and GONE from every frame after it. A shoe does not go anywhere.
SHOE_INSET_FRAC = 0.08


def _on_a_person(x, y, people, at_ground: bool = False) -> bool:
    """Is this point inside somebody's detection box, or just off it?

    `at_ground` says this point is already known to be at the picked
    golfer's foot line -- see `_on_the_ground`, which is the only
    thing that can know it. Down there the sideways test measures a
    SHOE rather than a person: the box comes in by SHOE_INSET_FRAC a
    side and the sideways padding does not apply, because a ball at
    address sits exactly where that padding used to reach.
    """
    for p in people or []:
        b = p.get("box") if isinstance(p, dict) else p
        if not b:
            continue
        if at_ground:
            inset = SHOE_INSET_FRAC * b[2]
            x_lo, x_hi = b[0] + inset, b[0] + b[2] - inset
        else:
            px = PERSON_PAD_W * b[2]
            x_lo, x_hi = b[0] - px, b[0] + b[2] + px
        py = PERSON_PAD_H * b[3]
        if x_lo <= x <= x_hi and b[1] - py <= y <= b[1] + b[3] + py:
            return True
    return False


def classify_balls(good, tail_live, jitter_px=ADDRESS_JITTER_PX,
                   late=None):
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

    # WHICH FRAMES ANSWER IT, from the caller, which is the only
    # thing that knows the SLOTS. This used to be `tail_live[1:]` --
    # drop the first frame that could be read -- on the assumption
    # that it is the +0.00 strike frame. It is, right up until the
    # +0.00 frame is one of the ones that could not be read: then the
    # dropped frame is +0.20, the rule falls back to the single +0.60
    # frame, and "absent from every frame after the strike" quietly
    # becomes "absent from one frame" -- which an occlusion satisfies.
    # Nothing said so, because the count it reported was still 1 of 1.
    if late is None:
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
                    people=None, anchor=None,
                    before: int = 3, jitter_px: float = ADDRESS_JITTER_PX,
                    min_contrast: float = ADDRESS_MIN_CONTRAST,
                    max_area: int = ADDRESS_MAX_AREA,
                    feet_x=None, handed=None, zone_y=None, ai_xy=None,
                    region_contrast: float = None) -> dict:
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

    offs = list(offsets or ())
    if len(offs) < 4:
        return {"left": None, "spot": None, "seen_before": 0,
                "seen_after": 0, "balls": 0, "state": "INCONCLUSIVE",
                "why": "needs the six frames"}
    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return {"left": None, "spot": None, "seen_before": 0,
                    "seen_after": 0, "balls": 0, "state": "INCONCLUSIVE",
                    "why": "could not open the clip"}
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        shots = []
        for off in offs:
            f = int(round((float(at_sec) + float(off)) * fps))
            if f < 0:
                shots.append(None)
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            shots.append(fr if ok else None)
    except Exception as exc:  # noqa: BLE001
        log.debug("ball_at_address failed (%s)", exc)
        return {"left": None, "spot": None, "seen_before": 0,
                "seen_after": 0, "balls": 0, "state": "INCONCLUSIVE",
                "why": "ball check failed"}
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
    return ball_in_frames(shots, rect=rect, people=people, anchor=anchor,
                          before=before, jitter_px=jitter_px,
                          min_contrast=min_contrast, max_area=max_area,
                          feet_x=feet_x, handed=handed, zone_y=zone_y,
                          ai_xy=ai_xy, region_contrast=region_contrast,
                          # So the per-frame report can say -1.20s
                          # rather than "slot 0".
                          offsets=offs)


def ball_in_frames(shots, *, rect=None, people=None, anchor=None,
                   before: int = 3, jitter_px: float = ADDRESS_JITTER_PX,
                   min_contrast: float = ADDRESS_MIN_CONTRAST,
                   max_area: int = ADDRESS_MAX_AREA,
                   offsets=None, region=None,
                   region_contrast: float = None,
                   feet_x=None, handed=None, zone_y=None,
                   ai_xy=None) -> dict:
    """The same rule over frames already in hand. Never raises.

    `shots` are the six images in chronological order, None where a
    frame could not be had. The core of ball_at_address, split out so
    one rule serves a clip on disk and the JPEGs a rig posts up.
    """
    import cv2

    # `state` NAMES THE ANSWER, instead of leaving the caller to infer
    # it from two other fields. There have always been four, and
    # `left` can only say three things:
    #
    #   left=True            a ball was there and left
    #   left=False           a ball was there and stayed
    #   left=None, balls=0   nothing ball-like was found at all
    #   left=None, balls>0   a ball was there; what happened is unclear
    #
    # The last two are opposite situations -- one is "no evidence of a
    # ball", the other "a ball, and no evidence of what it did" -- and
    # every caller had to reconstruct the difference from `balls`,
    # which reads like a count rather than a verdict. `left` is
    # unchanged for the callers that already read it.
    #
    # A CHECK THAT COULD NOT RUN IS INCONCLUSIVE, never NO_BALL: too
    # few frames and a thrown exception are not a search that came
    # back empty, and must not read as one.
    out = {"left": None, "spot": None, "seen_before": 0, "seen_after": 0,
           "balls": 0, "why": "not checked", "counts": None, "limits": None,
           "state": "INCONCLUSIVE", "frames": None,
           "region": (list(region) if region else None),
           "found_by": None}
    try:
        _live = [f for f in (shots or []) if f is not None]
        if len(_live) < 4:
            out["why"] = "needs the six frames"
            return out
        H, W = _live[0].shape[:2]
        if rect:
            x0 = max(0, min(W - 1, int(rect[0])))
            y0 = max(0, min(H - 1, int(rect[1])))
            x1 = max(x0 + 1, min(W, int(rect[2])))
            y1 = max(y0 + 1, min(H, int(rect[3])))
        else:
            x0, y0, x1, y1 = 0, 0, W, H
        boxes = [(x0, y0, x1, y1)]
        # ── THE STANCE BOX, BUILT HERE AND CLAMPED HERE ─────────────
        #
        # The caller has the golfer; only this function has the frame.
        # Building it here means a caller cannot hand in a box that
        # runs off the edge, which `_detections` slices with and would
        # quietly return an empty or wrapped patch for.
        #
        # BOTH SIDES UNLESS TOLD OTHERWISE, and `handed` is left None
        # by every caller today on purpose. The side halves the box,
        # but a WRONG side is worse than none: it puts the window
        # where the ball is not and returns a confident "no ball".
        # Measured -- with the side inverted this finds nothing on a
        # clip it otherwise reads correctly. Both sides is still a
        # roughly twentyfold narrowing, which is all the lower bar
        # needs.
        if region is None and feet_x is not None:
            region, _rwhy = ball_region(
                feet_x=feet_x, crop=(x0, y0, x1, y1), zone_y=zone_y,
                anchor=anchor, handed=handed, ai_xy=ai_xy,
                frame=(W, H))
            out["region_why"] = _rwhy
        if region is not None:
            _rx0 = max(x0, min(x1 - 1, int(region[0])))
            _ry0 = max(y0, min(y1 - 1, int(region[1])))
            _rx1 = max(_rx0 + 1, min(x1, int(region[2])))
            _ry1 = max(_ry0 + 1, min(y1, int(region[3])))
            region = (_rx0, _ry0, _rx1, _ry1)
            out["region"] = list(region)
        # SCALED TO THE GOLFER WE CROPPED TO, when there is one.
        #
        # THE ANCHOR'S HEIGHT, NOT THE AVERAGE OF EVERYBODY'S. This
        # took the mean height of every detected person, which is the
        # same mistake `_on_the_ground` was already fixed for: the
        # band is measured against ONE golfer -- the one the crop was
        # cut around -- so measuring the ball's size limit against a
        # different number says the two describe the same picture when
        # they do not. On the clip that raised it the picked golfer
        # was 126px and a partner 117, giving a cap of 36px where the
        # golfer's own height gives 39; a figure further down the tee
        # drags it further, and the ball is "too big" in a frame where
        # nothing about the ball changed.
        #
        # Falls back to the average when there is no anchor, which is
        # the old behaviour and still better than nothing.
        cap_area = int(max_area)
        _ah = None
        try:
            if anchor and float(anchor[1]) > 0:
                _ah = float(anchor[1])
        except Exception:  # noqa: BLE001
            _ah = None
        if _ah is None:
            _hs = [p["box"][3] for p in (people or [])
                   if isinstance(p, dict) and p.get("box")]
            if _hs:
                _ah = sum(_hs) / float(len(_hs))
        if _ah:
            _d = BALL_MAX_DIAM_OVER_GOLFER * _ah
            cap_area = max(4, int(_d * _d))

        # WHICH FILTER KILLED IT. Four tests stand between a bright
        # speck and a ball, and when none survives the answer used to
        # be "no ball clearly in the same spot", which names none of
        # them. On a clip where the golfer, the turf and the swing are
        # all plainly visible that sentence leaves nothing to do but
        # guess at a constant. Counting is cheap; guessing is not.
        #
        # First reason only, in the order the tests ran, so the counts
        # add up to the number of specks rather than double-counting
        # one that failed two tests.
        _tally = {"seen": 0, "too big": 0, "too faint": 0,
                  "on the golfer": 0, "off the ground": 0, "kept": 0}
        # HOW CLOSE THE NEAR-MISSES CAME, which the counts cannot say.
        #
        # "102 too faint" reads the same whether the brightest of them
        # was 29 against a bar of 30 or 5 against it, and those want
        # opposite responses: one is a bar set a hair too high on this
        # light, the other is a clip with no ball visible in it at all.
        # The same hole the audio gate had before `below_gate_peaks`.
        #
        # Tracked only for specks that got as far as the contrast test
        # -- something already rejected for being the size of a shoe is
        # not a ball that was nearly bright enough.
        _near = {"best_contrast": None, "best_at": None,
                 "best_in_band": None, "best_in_band_at": None}
        # AND THE ONES THAT NEVER BECAME CANDIDATES AT ALL.
        #
        # THE BLIND SPOT UNDER EVERY COUNT ABOVE. `_tally` counts what
        # `_detections` HANDED BACK; a blob it threw out itself -- too
        # small, not round, a sliver, under the local-contrast floor --
        # leaves no trace, so a ball sitting down in grass that fails
        # MIN_FILL because blades cut into its outline is not "too
        # faint" or "too big", it is absent from the report entirely.
        # The sentence then says "looked at 133 bright spots" and the
        # ball is not one of them, with nothing to say why.
        #
        # `_detections` already works out the reason for every blob it
        # refuses and takes a list to put them in. Nothing was passing
        # one. Only the ones sitting where a teed ball sits are kept
        # here: the frame is full of rejected specks and the only ones
        # worth a sentence are the ones in the band.
        # THE BOX'S BAR, which is the whole reason the box exists.
        # Defaults to the detector's own floor: there is no point
        # narrowing the search if the narrowed search keeps the bar
        # that was set for the wide one.
        _rbar = None
        if region is not None:
            _rbar = (float(REGION_CONTRAST) if region_contrast is None
                     else max(1.0, float(region_contrast)))
        _rbusy = 0           # the worst frame's candidate count, if busy
        _shape_n = 0
        _shape_band = []
        # EVERYTHING NEAR WHERE A BALL WOULD BE, AND WHAT WAS DECIDED.
        #
        # The counts total the frame and `near` keeps the single best
        # reject. Between them they could not answer the only question
        # an operator actually asks, pointing at the ball: "what did
        # you see THERE?" -- because the ball may be the second-best
        # near miss, or refused for its shape, or three pixels under
        # the band, and all three read as silence.
        #
        # It cost a wrong diagnosis: the best reject scored 28 against
        # a bar of 30, I read that as the ball two levels short, and
        # the drawn picture then put that 28 eight pixels left of the
        # golfer's box with the real ball well to the right of it.
        # One number, confidently misattributed, because nothing said
        # where it was.
        #
        # So every blob in the band's NEIGHBOURHOOD is kept with its
        # verdict, and the picture draws them all.
        _looked = []
        per_frame = []
        for fr in shots:
            if fr is None:
                per_frame.append(None)
                continue
            gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            # ABSOLUTE, NOT RELATIVE. See _detections: a golfer in a
            # white shirt standing over the ball otherwise sets a bar
            # the ball cannot clear. The ADDRESS_MIN_CONTRAST filter
            # below is the real bar here.
            dets = []
            _rj = []
            # ── THE WIDE PASS, AND THEN THE BOX ─────────────────────
            #
            # A UNION, not a replacement. The stance box is a guess at
            # where the ball should be, and a guess that misses must
            # not take the honest wide search down with it -- so
            # everything the normal pass finds is still found, and the
            # box only ADDS candidates it would have refused.
            #
            # They are gated differently on purpose. The wide bar is
            # high because the wide area is full of grass; inside a
            # box this size there is almost nothing to confuse, so the
            # bar can come down to where a dim ball actually lives.
            # ── ONLY THE BOX, WHEN THERE IS ONE ────────────────────
            #
            # The wide pass over the whole crop is what has been
            # failing: it is the reason the bar has to be high, the
            # reason the turf floods the report, and the reason a
            # bright speck a hundred pixels from the stance can win
            # the vote. With a box built from the feet, the trigger
            # zone and the crop edge, there is no question the wide
            # pass answers better.
            #
            # It still RUNS -- the counts and the rejects are the
            # diagnosis when the box finds nothing, and they cost
            # only the arithmetic on frames already decoded -- but
            # its candidates no longer reach the verdict.
            _wide = _detections(gray, boxes, rel=0.0, rejects=_rj)
            _cand = ([] if region is not None
                     else [(d, False) for d in _wide])
            if region is not None:
                for d in _wide:
                    _tally["seen"] += 1
                    _looked.append({"x": float(d[0]), "y": float(d[1]),
                                    "area": int(d[2]),
                                    "score": round(float(d[3]), 1),
                                    "verdict": "outside the box"})
            if region is not None and _rbar is not None:
                try:
                    _rd = _detections(gray, [tuple(region)], rel=0.0,
                                      floor=_rbar,
                                      min_fill=REGION_MIN_FILL,
                                      max_aspect=REGION_MAX_ASPECT)
                    # THE PREMISE, CHECKED. See REGION_MAX_CANDIDATES:
                    # a box holding ninety specks is not a narrowed
                    # search, it is a lottery, and the 2-of-3-then-gone
                    # rule has no defence against that many tickets.
                    if len(_rd) > REGION_MAX_CANDIDATES:
                        _rbusy = max(_rbusy, len(_rd))
                        _rd = []
                    for d in _rd:
                        if any(abs(d[0] - o[0][0]) <= 3
                               and abs(d[1] - o[0][1]) <= 3
                               for o in _cand):
                            continue      # the wide pass had this one
                        _cand.append((d, True))
                except Exception as exc:  # noqa: BLE001
                    log.debug("ball: region pass failed (%s)", exc)
            for d, _in_box in _cand:
                _tally["seen"] += 1
                _verdict = None
                # THE BOX'S OWN BAR. A candidate the wide search could
                # never have produced must not then be judged by the
                # wide search's threshold.
                _bar = _rbar if _in_box else float(min_contrast)
                if d[2] > int(cap_area):
                    _tally["too big"] += 1
                    _verdict = "too big"
                elif d[3] < float(_bar):
                    _tally["too faint"] += 1
                    _verdict = "too faint"
                    _c = float(d[3])
                    if (_near["best_contrast"] is None
                            or _c > _near["best_contrast"]):
                        _near["best_contrast"] = _c
                        _near["best_at"] = [int(d[0]), int(d[1])]
                    # AND THE BEST ONE THAT WAS IN THE RIGHT PLACE.
                    # The brightest reject is often a highlight on a
                    # shoulder or a cart roof; the useful number is the
                    # brightest thing sitting where a teed ball sits,
                    # because that is the one the bar is keeping out.
                    if _on_the_ground(d[1], anchor) and (
                            _near["best_in_band"] is None
                            or _c > _near["best_in_band"]):
                        _near["best_in_band"] = _c
                        _near["best_in_band_at"] = [int(d[0]), int(d[1])]
                elif not (_in_box or _on_the_ground(d[1], anchor)):
                    # ASKED BEFORE the on-a-person test now, because
                    # that test needs the answer: at the foot line it
                    # is looking for a shoe, not a person.
                    #
                    # AND NOT ASKED AT ALL OF THE BOX. The stance box
                    # IS a ground test -- a stricter one, since it
                    # bounds x as well -- and it deliberately reaches
                    # further below the foot line than the band does,
                    # because a ball teed forward of the stance sits
                    # lower in frame than the feet. Applying both
                    # meant the box searched ground the band then
                    # refused, which is two rulers for one picture and
                    # exactly why the narrowed search found nothing on
                    # its first outing.
                    _tally["off the ground"] += 1
                    _verdict = "off the ground"
                elif _on_a_person(d[0], d[1], people, at_ground=True):
                    _tally["on the golfer"] += 1
                    _verdict = "on the golfer"
                else:
                    _tally["kept"] += 1
                    _verdict = "kept"
                    if _in_box and out["found_by"] is None:
                        out["found_by"] = "the stance box"
                    dets.append(d)
                _looked.append({"x": float(d[0]), "y": float(d[1]),
                                "area": int(d[2]),
                                "score": round(float(d[3]), 1),
                                "verdict": _verdict})
            _shape_n += len(_rj)
            for _r in _rj:
                if _on_the_ground(_r.get("y"), anchor):
                    _shape_band.append(_r)
                _looked.append({"x": float(_r["x"]), "y": float(_r["y"]),
                                "area": int(_r.get("area") or 0),
                                "score": None,
                                "verdict": "shape",
                                "why": str(_r.get("why") or "")})
            per_frame.append(dets)
        # THE BARS THEMSELVES, beside the counts. "9 too faint" is only
        # actionable next to the number they had to clear.
        _band = None
        if anchor:
            try:
                _f, _h = float(anchor[0]), float(anchor[1])
                if _h > 0:
                    _band = [int(round(_f - BALL_BAND_ABOVE_FEET * _h)),
                             int(round(_f + BALL_BAND_BELOW_FEET * _h))]
            except Exception:  # noqa: BLE001
                _band = None
        out["counts"] = dict(_tally)
        # BIGGEST FIRST: among blobs in the band, the largest is the
        # likeliest ball, and a one-pixel speck rejected for being
        # one pixel is not news.
        # ── AND IF NOTHING WAS SEEN AT ALL, LOOK LOWER ──────────────
        #
        # "Nothing at all" is the one answer the report above cannot
        # explain, because the floor is a BINARISATION THRESHOLD: a
        # ball dimmer than MIN_LOCAL_CONTRAST never becomes a blob, so
        # it is not refused, it is absent, and absent looks identical
        # to an empty patch of turf.
        #
        # Those need opposite fixes -- one is a floor set above this
        # hole's light, the other a ball that is not visible to this
        # method at all -- and the operator cannot tell them apart by
        # looking at a picture with no marks on it.
        #
        # So: one more pass at a lower floor, ONLY when the first
        # found nothing in the band, and only to report. Nothing it
        # finds is ever treated as a ball; the entries are marked
        # "under the floor" and carry the number a floor would have to
        # drop to in order to see them.
        _under = []
        if not any(e["verdict"] == "kept" for e in _looked):
            for fr in shots[:int(before)]:
                if fr is None:
                    continue
                try:
                    _g2 = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                    _rj2 = []
                    for d in _detections(_g2, boxes, rel=0.0,
                                         floor=UNDER_FLOOR_PROBE,
                                         rejects=_rj2):
                        if float(d[3]) >= float(MIN_LOCAL_CONTRAST):
                            continue        # the first pass had this
                        _under.append({"x": float(d[0]), "y": float(d[1]),
                                       "area": int(d[2]),
                                       "score": round(float(d[3]), 1),
                                       "verdict": "under the floor"})
                    # AND THE ONES IT REFUSED ON SHAPE DOWN HERE, which
                    # is most of them: a blob this close to the noise
                    # is ragged, so it fails MIN_FILL as well as the
                    # floor. Measured on a synthetic ball at a top-hat
                    # of 11 -- "fills 0.47 of its 5x6 box, needs 0.55".
                    # Collecting only what the detector RETURNED left
                    # exactly the case this probe exists for invisible,
                    # which is the same mistake twice in one function.
                    for _r in _rj2:
                        # BIG ENOUGH TO HAVE BEEN A BALL. Below the
                        # floor the turf's own texture breaks into
                        # single pixels by the hundred, all of them
                        # refused for area and all of them noise;
                        # they crowd out the one blob worth seeing,
                        # which on a synthetic ball was 14px against
                        # their 1px.
                        if int(_r.get("area") or 0) < BALL_MIN_AREA:
                            continue
                        _under.append({
                            "x": float(_r["x"]), "y": float(_r["y"]),
                            "area": int(_r.get("area") or 0),
                            "score": None,
                            "verdict": "under the floor",
                            "why": str(_r.get("why") or "")})
                except Exception as exc:  # noqa: BLE001
                    log.debug("ball: under-floor probe failed (%s)", exc)
                    break
            # ONLY WHAT THE FIRST PASS COULD NOT SEE AT ALL. A blob
            # the normal pass already classified -- too faint, off the
            # ground -- has a better verdict than "under the floor",
            # and adding a second entry for it would both duplicate
            # the marker and make `seen` count two different kinds of
            # look as one number.
            _under.sort(key=lambda e: -(e.get("area") or 0))
            _fresh = []
            for _u in _under:
                if any(((_u["x"] - _o["x"]) ** 2
                        + (_u["y"] - _o["y"]) ** 2) ** 0.5 <= 5.0
                       for _o in _looked):
                    continue
                _fresh.append(_u)
            _looked.extend(_fresh[:12])

        # ── WHAT TO SHOW, AND WHAT IS NOISE ─────────────────────────
        #
        # The whole frame is full of refused specks; the only ones
        # worth a marker are the ones near where a teed ball sits. So:
        # the band, widened by its own height each way, which catches
        # a ball just UNDER the line as well as one in it. That matters
        # because the margin below the feet is 0.02 of a golfer's
        # height -- two and a half pixels on a 126px golfer -- and a
        # ball teed forward of the stance sits lower in the frame than
        # the feet do.
        #
        # Deduplicated across the six frames: a ball that does not move
        # is found six times in the same place, and six stacked markers
        # say nothing the first one did not.
        _show = []
        if _band:
            _h = max(8.0, float(_band[1] - _band[0]))
            _lo, _hi2 = _band[0] - _h, _band[1] + _h
        else:
            _lo = _hi2 = None
        for _e in _looked:
            if _lo is not None and not (_lo <= _e["y"] <= _hi2):
                continue
            _dup = None
            for _o in _show:
                if ((_e["x"] - _o["x"]) ** 2
                        + (_e["y"] - _o["y"]) ** 2) ** 0.5 <= 5.0:
                    _dup = _o
                    break
            if _dup is None:
                _show.append(dict(_e, seen=1))
            else:
                _dup["seen"] += 1
                # KEEP THE BEST SIGHTING of a thing seen repeatedly:
                # the frame where it scored highest is the one that
                # says how close it came to the bar.
                if (_e.get("score") or -1) > (_dup.get("score") or -1):
                    _dup.update({k: _e[k] for k in
                                 ("score", "verdict", "area")})
        # RANKED BY PLACE, NOT BY BRIGHTNESS. Sorting by score and
        # capping was a trap of its own making: the entries most worth
        # seeing are the DIM ones near where the ball should be, and
        # they are exactly the ones a brightness ranking drops off the
        # end. The under-floor probe above would have been sorted last
        # and cut, which is the opposite of why it exists.
        #
        # So: inside the band beats near it, nearer beats further, and
        # brightness only breaks ties.
        if _band:
            _mid = (float(_band[0]) + float(_band[1])) / 2.0
        else:
            _mid = None

        def _rank(e):
            _in = 0 if (_band and _band[0] <= e["y"] <= _band[1]) else 1
            _d = 0.0 if _mid is None else abs(e["y"] - _mid)
            return (_in, _d, -(e.get("score") or 0))

        _show.sort(key=_rank)
        out["looked_at"] = [
            {k: v for k, v in dict(
                e, x=int(round(e["x"])), y=int(round(e["y"]))).items()
             if k != "why" or v}
            for e in _show[:14]
        ]
        _shape_band.sort(key=lambda r: -float(r.get("area") or 0))
        out["shape_rejects"] = {
            "n": int(_shape_n),
            "in_band": [{"x": int(round(float(r["x"]))),
                         "y": int(round(float(r["y"]))),
                         "area": int(r.get("area") or 0),
                         "why": str(r.get("why") or "")}
                        for r in _shape_band[:3]],
        }
        out["near"] = {k: (round(v, 1) if isinstance(v, float) else v)
                       for k, v in _near.items()}
        if _rbusy:
            out["region_busy"] = int(_rbusy)
        out["limits"] = {
            "max area": int(cap_area),
            "min contrast": round(float(min_contrast), 1),
            "ground band y": _band,
            "stance box": (list(region) if region else None),
            "stance box contrast": (None if _rbar is None
                                    else round(float(_rbar), 1)),
        }

        head = per_frame[:int(before)]
        tail = per_frame[int(before):]
        # THE FRAMES THEMSELVES, slot by slot. The counts say how many
        # specks died on each bar across the whole set; they cannot say
        # that the -1.20s frame was unreadable, which silently turns
        # "the same spot in 2 of the first 3" into "in 2 of the 2 that
        # could be read". Nor that the +0.00 frame was missing, which
        # is what `late` below turns on. A stage that reports nothing
        # is a stage nobody can check.
        #
        # `in_rule` is the honest version of "did this frame get a
        # vote": the strike frame never does (see classify_balls) and
        # neither does one that could not be read.
        _offs = list(offsets or ())
        _frames = []
        for _i, _d in enumerate(per_frame):
            if _i < int(before):
                _role = "before"
            elif _i == int(before):
                _role = "strike"
            else:
                _role = "after"
            _frames.append({
                "at": (round(float(_offs[_i]), 2)
                       if _i < len(_offs) else None),
                "role": _role,
                "read": _d is not None,
                "kept": (0 if _d is None else len(_d)),
                "spots": ([] if _d is None else
                          [[int(round(_x)), int(round(_y))]
                           for (_x, _y, _a, _c) in _d]),
                "in_rule": _d is not None and _role != "strike",
            })
        out["frames"] = _frames
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
            # THE COUNTS BELONG IN THE SENTENCE, not only in the dict:
            # this is the branch an operator actually reads, and the
            # whole question it raises is "why not".
            _rej = ", ".join(
                "%d %s" % (_tally[k], k) for k in
                ("too big", "too faint", "on the golfer", "off the ground")
                if _tally[k])
            # AND WHAT A LOWER BAR WOULD HAVE CAUGHT. See `_near`:
            # "102 too faint" is unactionable without knowing whether
            # the brightest of them missed by one grey level or by
            # twenty-five.
            _nb = _near.get("best_in_band")
            _na = _near.get("best_in_band_at")
            if _nb is None:
                _nb, _na = _near.get("best_contrast"), _near.get("best_at")
                _where = "the brightest reject anywhere"
            else:
                _where = "the brightest reject sitting where a teed ball sits"
            # AND THE FLOOR UNDER THE WHOLE COUNT, because the range
            # these numbers can take is not 0..30.
            #
            # `_detections` applies MIN_LOCAL_CONTRAST before any of
            # this, so "102 too faint" counts specks that cleared 18
            # and missed 30 -- a ball dimmer than 18 over its
            # surroundings never becomes a candidate and reads here as
            # "no bright spots", not as a near miss. Saying so stops
            # the reader concluding from a count whose floor they
            # cannot see.
            #
            # A "nowhere near the bar" warning was written here and
            # removed: with the floor at 18 and the bar at 30, nothing
            # can score under half the bar, so the branch could never
            # fire. A test that cannot be failed is worse than none --
            # it implies a distinction this cannot actually make.
            # AND THE ONES THAT NEVER GOT AS FAR AS A BAR. Without
            # this the reader concludes from "133 bright spots" that
            # the ball was one of the 133 and lost on a number, when
            # it may never have been counted.
            _shape = ""
            if _shape_band:
                _b0 = _shape_band[0]
                _shape = (
                    " %d more never became candidates (size or shape); the "
                    "biggest where a teed ball sits was %dpx at (%d, %d) — "
                    "%s."
                    % (_shape_n, int(_b0.get("area") or 0),
                       int(round(float(_b0["x"]))),
                       int(round(float(_b0["y"]))),
                       _b0.get("why") or "no reason recorded"))
            elif _shape_n:
                _shape = (" %d more never became candidates (size or "
                          "shape), none of them in the band." % _shape_n)
            # AND THE ANSWER TO "NOTHING AT ALL". When the report
            # above is empty the operator is looking at a clip with a
            # ball plainly in it and a picture with no marks on it.
            # This is the only line that separates "the floor is above
            # this hole's light" from "the ball is not visible to this
            # method", and those need opposite responses.
            _deep = ""
            _uf = [e for e in (out.get("looked_at") or [])
                   if e.get("verdict") == "under the floor"]
            if _uf:
                _u0 = max(_uf, key=lambda e: e.get("area") or 0)
                _deep = (
                    " Under the detector's floor of %.0f there was a %dpx "
                    "blob at (%d, %d)%s — too dim to become a candidate at "
                    "all, so no bar above ever saw it."
                    % (MIN_LOCAL_CONTRAST, _u0.get("area") or 0,
                       _u0["x"], _u0["y"],
                       (": %s" % _u0["why"]) if _u0.get("why") else ""))
            # AND WHEN THE BOX GAVE UP, which is not the same as
            # the box finding nothing: one says the grass beside the
            # golfer held no ball, the other that it held so much
            # that nothing could be concluded. The second is a
            # statement about the turf and the bar, not the ball.
            _busy = ""
            if out.get("region_busy"):
                _busy = (
                    " The stance box was abandoned: %d specks cleared its "
                    "bar of %.0f in one frame (limit %d), which is turf "
                    "texture rather than a ball, and with that many "
                    "candidates one lands in the same spot twice by chance."
                    % (out["region_busy"], float(_rbar or 0),
                       REGION_MAX_CANDIDATES))
            _close = ""
            if _nb is not None:
                _close = (
                    " %s was %.0f at (%d, %d) — a contrast bar under that "
                    "would have kept it (nothing under %.0f is counted at "
                    "all)."
                    % (_where, _nb, (_na or [0, 0])[0], (_na or [0, 0])[1],
                       MIN_LOCAL_CONTRAST))
            out["why"] = (
                "no ball clearly in the same spot in 2 of the first %d "
                "frames — no judgement from the ball. Looked at %d bright "
                "spot(s) across the six: %s%s. Bars: area ≤%d, contrast "
                "≥%.0f%s."
                % (len(head), _tally["seen"],
                   _rej or "none rejected",
                   ("; %d survived but never twice in the same place"
                    % _tally["kept"]) if _tally["kept"] else "",
                   int(cap_area), float(min_contrast),
                   "" if _band is None
                   else ", ball must sit y %d..%d" % (_band[0], _band[1]))
                + _close + _shape + _deep + _busy)
            # THE SEARCH RAN AND CAME BACK EMPTY, which is a different
            # answer from "could not look" -- the branches that could
            # not look leave the initialiser's INCONCLUSIVE alone.
            # AND THE BOX IT SEARCHED, IN WORDS. "No ball" from a
            # search of the whole crop and "no ball" from a patch of
            # grass beside the stance are different claims, and the
            # second is only worth anything if the operator can see
            # where it looked and why there.
            if out.get("region"):
                out["why"] += (
                    " Searched only the stance box %s at a bar of %.0f: %s."
                    % (tuple(out["region"]), float(_rbar or 0),
                       out.get("region_why") or "no reasoning recorded"))
            elif out.get("region_why"):
                out["why"] += (" No stance box: %s."
                               % out["region_why"])
            out["state"] = "NO_BALL_DETECTED"
            return out
        # EVERY BALL, NOT THE BEST ONE: the one that matters is
        # whichever LEFT. See classify_balls.
        tail_live = [d for d in tail if d is not None]
        # BY SLOT, not by "the first one that could be read" -- the
        # strike frame is slot `before`, whether or not it was read.
        _late = [d for d in tail[1:] if d is not None]
        gone, stayed = classify_balls(good, tail_live, jitter_px, late=_late)
        out["balls"] = len(good)
        if gone:
            best = max(gone, key=lambda c: (c["frames"], c["con"]))
            out["spot"] = [int(round(best["x"])), int(round(best["y"]))]
            out["seen_before"] = int(best["frames"])
            out["seen_after"] = int(best["still"])
            out["left"] = True
            if out["found_by"] is None:
                out["found_by"] = "the full band"
            out["state"] = "BALL_PRESENT_DISAPPEARED"
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
            if out["found_by"] is None:
                out["found_by"] = "the full band"
            out["state"] = "BALL_PRESENT_REMAINED"
            out["why"] = ("the ball is still at (%d, %d) in %d frames "
                          "after the strike and no ball on the turf left "
                          "its spot" % (out["spot"][0], out["spot"][1],
                                        best["still"]))
            return out
        best = max(good, key=lambda c: (c["frames"], c["con"]))
        out["spot"] = [int(round(best["x"])), int(round(best["y"]))]
        out["seen_before"] = int(best["frames"])
        out["seen_after"] = int(best.get("still") or 0)
        out["state"] = "INCONCLUSIVE"
        out["why"] = ("a ball sat at (%d, %d) before the strike but what "
                      "happened to it is not clear"
                      % (out["spot"][0], out["spot"][1]))
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("ball_in_frames failed (%s)", exc)
        out["why"] = "ball check failed"
        return out


# ---- nailing the impact frame ---------------------------------------

# How far either side of the audio peak to look for the frame the ball
# actually leaves. The trigger LAGS -- the operator's own observation,
# and the reason the +0.00 frame is excluded from the hit/not-hit
# rule -- so the search runs mostly after the peak, with a little
# before it in case a rig ever runs the other way.
DEPART_BEFORE_SEC = 0.20
DEPART_AFTER_SEC = 0.50


def ball_departure(video_path, at_sec: float, *, rect=None, people=None,
                   anchor=None, spot=None,
                   before_sec: float = DEPART_BEFORE_SEC,
                   after_sec: float = DEPART_AFTER_SEC,
                   jitter_px: float = ADDRESS_JITTER_PX) -> dict:
    """Where the ball sat, and the frame it left. Never raises.

    THE AUDIO PEAK IS NOT THE IMPACT. It is the impact plus whatever
    the microphone, the encoder and the ring buffer add, and that lag
    is the reason everything downstream of it has to be given a
    window rather than a frame. But the BALL knows: it sits
    motionless on the turf for as long as anybody looks at it, and
    then in one frame it is gone. That frame is the impact, to the
    frame, measured rather than assumed.

    `spot` is the resting position if the six-frame check already
    found one -- it is the same ball and there is no sense looking
    twice. Without it this finds the ball itself, from the frames
    BEFORE the peak, where it is by definition still there.

    Returns {spot, impact_frame, impact_sec, checked, last_seen,
    why}. `impact_frame` is None when the ball could not be found or
    never left, which are different answers and `why` says which.
    """
    import cv2

    out = {"spot": list(spot) if spot else None, "impact_frame": None,
           "impact_sec": None, "checked": 0, "last_seen": None,
           "why": "not checked"}
    cap = None
    try:
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
        cap_area = ADDRESS_MAX_AREA
        _hs = [p["box"][3] for p in (people or [])
               if isinstance(p, dict) and p.get("box")]
        if _hs:
            _d = BALL_MAX_DIAM_OVER_GOLFER * (sum(_hs) / float(len(_hs)))
            cap_area = max(4, int(_d * _d))

        f_lo = max(0, int(round((float(at_sec) - float(before_sec)) * fps)))
        f_hi = int(round((float(at_sec) + float(after_sec)) * fps))

        def _balls(f):
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(f))
            ok, fr = cap.read()
            if not ok or fr is None:
                return None
            gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            # SAME ORDER AS ball_in_frames, and for the same reason:
            # `_on_a_person` judges a shoe rather than a person once
            # the point is known to be at the foot line.
            return [d for d in _detections(gray, boxes, rel=0.0)
                    if d[2] <= cap_area and d[3] >= ADDRESS_MIN_CONTRAST
                    and _on_the_ground(d[1], anchor)
                    and not _on_a_person(d[0], d[1], people,
                                         at_ground=True)]

        # WHERE IT SAT, if nobody has already said. Taken from the
        # frames BEFORE the peak, where a ball that is about to be
        # struck is by definition still there.
        if out["spot"] is None:
            seen = {}
            for f in range(f_lo, min(f_hi, int(round(at_sec * fps))) + 1, 2):
                for (bx, by, _a, _c) in (_balls(f) or []):
                    key = None
                    for k in seen:
                        if ((bx - k[0]) ** 2 + (by - k[1]) ** 2) ** 0.5 \
                                <= float(jitter_px):
                            key = k
                            break
                    if key is None:
                        seen[(bx, by)] = 1
                    else:
                        seen[key] += 1
            if not seen:
                out["why"] = "no ball at rest before the strike"
                return out
            best = max(seen, key=lambda k: seen[k])
            out["spot"] = [int(round(best[0])), int(round(best[1]))]

        sx, sy = float(out["spot"][0]), float(out["spot"][1])

        # AND THE FRAME IT LEFT: the first one, scanned forward, with
        # nothing at that spot and nothing at it after either. One
        # missed detection is not a departure -- the ADDRESS check
        # lost a ball in 1 frame of 3 when a club head passed it --
        # so a frame only counts as gone if the NEXT one agrees.
        gone_at = None
        for f in range(f_lo, f_hi + 1):
            dets = _balls(f)
            if dets is None:
                continue
            out["checked"] += 1
            here = any(((bx - sx) ** 2 + (by - sy) ** 2) ** 0.5
                       <= float(jitter_px) for (bx, by, _a, _c) in dets)
            if here:
                out["last_seen"] = int(f)
                gone_at = None
                continue
            if gone_at is None:
                gone_at = int(f)
            elif f - gone_at >= 1:
                out["impact_frame"] = gone_at
                out["impact_sec"] = round(gone_at / fps, 3)
                out["why"] = (
                    "the ball sat at (%d, %d) and is gone from f%d onward; "
                    "last seen f%s" % (out["spot"][0], out["spot"][1],
                                       gone_at, out["last_seen"]))
                return out
        if out["last_seen"] is not None:
            out["why"] = ("the ball sat at (%d, %d) and is still there at "
                          "the end of the window (f%d)"
                          % (out["spot"][0], out["spot"][1],
                             out["last_seen"]))
        else:
            out["why"] = ("nothing was at (%d, %d) in any frame checked"
                          % (out["spot"][0], out["spot"][1]))
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("ball_departure failed (%s)", exc)
        out["why"] = "the departure search failed"
        return out
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass


# HOW MANY FRAMES OF THE DEPARTURE WINDOW GET DRAWN. Enough to see
# the ball sitting, the frame it goes, and that it stays gone; few
# enough that the strip is a picture rather than a flipbook.
DEPART_STRIP_FRAMES = 10


def departure_strip(video_path, out_path, *, rect=None, spot=None,
                    impact_frame=None, last_seen=None, fps=None,
                    at_sec=None, before_sec: float = DEPART_BEFORE_SEC,
                    after_sec: float = DEPART_AFTER_SEC,
                    jitter_px: float = ADDRESS_JITTER_PX):
    """The frames the departure search judged, with what it saw on them.

    WHY THIS EXISTS. `ball_departure` returns a sentence -- "the ball
    sat at (870, 612) and is gone from f1536 onward" -- and a sentence
    is not something anybody can check. The two ways it goes wrong
    look identical in words: it can lose a ball that is still there
    (a club head passes it, the sun goes in) and it can keep seeing
    something that is not the ball (a shoe, a tee marker, a daisy).
    One picture of the window settles both, and neither can be
    settled without one.

    Read in a second pass rather than inside the search, so a produce
    that does not ask for the picture pays nothing and the hot loop
    stays the shape it was measured in.

    Returns the path written, or None. Never raises.
    """
    import cv2
    import numpy as np

    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        _fps = float(fps or cap.get(cv2.CAP_PROP_FPS) or 30.0)
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if rect:
            x0 = max(0, min(W - 1, int(rect[0])))
            y0 = max(0, min(H - 1, int(rect[1])))
            x1 = max(x0 + 1, min(W, int(rect[2])))
            y1 = max(y0 + 1, min(H, int(rect[3])))
        else:
            x0, y0, x1, y1 = 0, 0, W, H
        f_lo = max(0, int(round((float(at_sec) - float(before_sec)) * _fps)))
        f_hi = int(round((float(at_sec) + float(after_sec)) * _fps))
        # EVENLY SPREAD, PLUS THE TWO THAT DECIDE IT. The frame the
        # ball was last seen on and the frame it was called gone are
        # the only two the answer rests on; a strip that happens to
        # skip them is a picture of everything except the decision.
        _want = set(
            int(round(v)) for v in
            np.linspace(f_lo, f_hi, DEPART_STRIP_FRAMES))
        for _k in (last_seen, impact_frame):
            if _k is not None:
                _want.add(int(_k))
        tiles = []
        for f in sorted(_want):
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(f))
            ok, fr = cap.read()
            if not ok or fr is None:
                continue
            crop = fr[y0:y1, x0:x1].copy()
            ch, cw = crop.shape[:2]
            if ch < 8 or cw < 8:
                continue
            _sc = min(4.0, 300.0 / float(max(cw, ch)))
            if _sc > 1.02:
                crop = cv2.resize(crop, (int(cw * _sc), int(ch * _sc)),
                                  interpolation=cv2.INTER_CUBIC)
            else:
                _sc = 1.0
            u = max(1, int(round(max(crop.shape[:2]) / 220.0)))
            _here = False
            if spot:
                g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                for (bx, by, _a, _c) in _detections(
                        g, [(x0, y0, x1, y1)], rel=0.0):
                    if ((bx - spot[0]) ** 2
                            + (by - spot[1]) ** 2) ** 0.5 <= jitter_px:
                        _here = True
                        break
                cv2.circle(crop,
                           (int((spot[0] - x0) * _sc),
                            int((spot[1] - y0) * _sc)),
                           int(9 * u),
                           (60, 230, 90) if _here else (90, 110, 230),
                           u + 1, cv2.LINE_AA)
            _lbl = "f%d %s" % (f, "ball" if _here else "gone")
            if impact_frame is not None and f == int(impact_frame):
                _lbl += "  <- IMPACT"
            cv2.rectangle(crop, (0, 0),
                          (crop.shape[1] - 1, int(16 * u)), (20, 20, 20), -1)
            cv2.putText(crop, _lbl, (4 * u, int(12 * u)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36 * u,
                        (235, 235, 235), u, cv2.LINE_AA)
            tiles.append(crop)
        if not tiles:
            return None
        _h = max(t.shape[0] for t in tiles)
        _w = max(t.shape[1] for t in tiles)
        cols = min(5, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        sheet = np.zeros((rows * (_h + 4), cols * (_w + 4), 3), np.uint8)
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            sheet[r * (_h + 4):r * (_h + 4) + t.shape[0],
                  c * (_w + 4):c * (_w + 4) + t.shape[1]] = t
        if not cv2.imwrite(str(out_path), sheet,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
            return None
        return str(out_path)
    except Exception as exc:  # noqa: BLE001
        log.debug("departure strip failed (%s)", exc)
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
