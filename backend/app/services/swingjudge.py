"""What the judge is looking at, measured — and the rules around it.

ONE FILE, TWO COPIES, HELD BYTE-IDENTICAL by tools/swing_gate_test.py.
It is installed as `agent/swingjudge.py` on the rig and
`app/services/swingjudge.py` on the server, because the rig cannot
import from the backend and the backend cannot import from the rig.
The codebase already carries one such pair -- swingheat.build against
tracer.swing_heat_check -- and the lesson from it is written at the
top of swingheat.py: when the two drift, the failure looks like the
MODEL getting worse rather than the renderer changing. So this one is
not re-implemented twice. It is copied, and the test fails on a
difference of one byte.

WHY ANY OF THIS EXISTS. On 5 Nov the judge accepted this composite
from hole 8:

    the only human-shaped heat in frame   58 x 110 px, h/w 1.90
    the thing it called a golfer         184 x 201 px, h/w 1.09

Nearly square, three times the area, and described back to us as "a
compact hot figure with a cooler plume extending upward consistent
with a club arc". The judge was not hallucinating -- a 184x201 orange
mass with blue edges genuinely satisfies that sentence. The sentence
is not discriminating, and three attempts at rewriting it in one
morning made things worse each time.

So the answer is not better words. It is to stop handing the model
questions that a tape measure can answer, and to measure what we hand
it. Nothing here asks a model anything; it is arithmetic over the heat
map the renderer already built, and over the person detector's own
sightings, both of which were being thrown away.

EVERY THRESHOLD HERE IS A GUESS UNTIL IT IS MEASURED. tools/
judge_bench.py runs a labelled corpus through the real renderer and
the real judge and prints what each gate keeps and kills. Numbers that
have not been through it are marked UNMEASURED and default to
permissive -- a gate that drops a real swing costs a shot that never
existed, which is strictly worse than a false positive nobody watches.
"""
from __future__ import annotations

import math

# ---- measuring the heat map ------------------------------------------

# Where "hot" starts, on the 0-255 log-scaled heat map the renderers
# build (NOT on the colormapped picture: that has the photo blended
# through it and its saturation says as much about the grass as the
# motion).
#
# THE MAP IS log1p(frames_moved) / log1p(peak). Over a 60-frame window
# a leaf that flickers for two frames lands near 68; something moving
# through most of the window lands near 255. 150 is about six frames
# of motion -- above wind and sensor speckle, far below a body. Set
# too low, the closing below bridges the speckle and reports the whole
# scene as one blob: measured on the 5 Nov composite, level 96 gave a
# single 1741x213 "object" spanning the frame.
HOT_LEVEL = 150

# Blobs smaller than this fraction of the frame are speckle -- wind in
# foliage, a bird, sensor noise. The renderer's own warmup drops most
# of it; this drops the rest so the "biggest blob" is not a leaf.
MIN_BLOB_FRAC = 0.00015


def measure_heat(heat8, hot_level: int = HOT_LEVEL,
                 min_blob_frac: float = MIN_BLOB_FRAC,
                 prefer_xy=None, prefer_span=None) -> dict:
    """Describe the hot regions of a heat map. Never raises.

    `prefer_xy` is where the golfer is believed to be -- the person
    detector's stance point, or the pose stage's feet -- and
    `prefer_span` an (x0, x1) range they must be standing in, normally
    the camera's trigger zones. Either one picks WHICH blob is `main`.

    WITHOUT ONE IT IS THE LARGEST, AND THAT IS WRONG OFTEN ENOUGH TO
    MATTER. Clip 1734, 6 Nov, is a swing the operator confirmed: the
    golfer measured 74x134 at x=1813, a textbook 1.81 aspect, and
    LOST to a bystander rummaging in a bag beside a cart at x=26 --
    5677 pixels against 4108, a 27% margin. A second of someone
    reaching into a golf bag is simply more sustained motion than a
    fast swing. Everything downstream then pointed at the wrong
    person: the crop, the frames the judge was shown, and the stance
    gate. The judge looked at a man at a cart and correctly said no.

    `heat8` is the single-channel 0-255 map, at whatever scale the
    caller has it in; everything returned is in those pixels, plus
    fractions of the frame so a caller can compare across scales.

    Returns {ok, frame_w, frame_h, n_blobs, main: {...}, others: [...]}
    where `main` is the largest blob and carries:

        x, y, w, h      bounding box
        area            hot pixels in it (not w*h)
        aspect          h / w -- the number that caught 5 Nov
        fill            area / (w*h): how solid it is
        height_frac     h / frame_h
        base            (x + w/2, y + h) -- where its feet would be

    `ok` false means there was nothing to measure, which is NOT a
    verdict: an empty heat map means the renderer found no motion, and
    what to do about that belongs to the caller.
    """
    out = {"ok": False, "frame_w": 0, "frame_h": 0, "n_blobs": 0,
           "main": None, "others": []}
    try:
        import cv2
        import numpy as np

        h, w = heat8.shape[:2]
        out["frame_w"], out["frame_h"] = int(w), int(h)
        mask = (heat8 >= int(hot_level)).astype("uint8") * 255
        if not mask.any():
            return out
        # OPEN BEFORE CLOSE, AND THE ORDER IS THE WHOLE TRICK.
        #
        # Closing first bridges the speckle: a golf hole in wind is
        # dusted with isolated hot pixels, and a kernel big enough to
        # join a club to its golfer is big enough to join a leaf to a
        # leaf all the way across the frame. On the 5 Nov composite
        # that reported one 1741x213 object -- which the shape gate
        # below duly rejected, for entirely the wrong reason, and
        # would have rejected a real swing for the same one.
        #
        # Opening with a 3x3 first removes anything thinner than a
        # club. Then the close joins what is left: the club is a
        # dotted plume and without it the body and the club come back
        # as two objects, making a real swing look short and square.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                np.ones((3, 3), "uint8"))
        if not mask.any():
            return out
        k = max(3, int(round(min(w, h) * 0.012)) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                np.ones((k, k), "uint8"))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        floor = max(24.0, float(w) * float(h) * float(min_blob_frac))
        blobs = []
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < floor:
                continue
            bx = int(stats[i, cv2.CC_STAT_LEFT])
            by = int(stats[i, cv2.CC_STAT_TOP])
            bw = max(1, int(stats[i, cv2.CC_STAT_WIDTH]))
            bh = max(1, int(stats[i, cv2.CC_STAT_HEIGHT]))
            # WHERE THE FEET ARE. Two things this must survive, and
            # they pull in opposite directions:
            #
            #   A SIDEWAYS SMEAR. A shadow across the grass, a club
            #   lying down, a streak of wind-moved turf -- all join
            #   the blob and drag the bounding box's BOTTOM below the
            #   golfer. That bottom was the first attempt.
            #
            #   A GAP DOWN THE MIDDLE. Two golfers standing close
            #   come back as one wide blob with very little between
            #   them, so the lowest row of the middle half of the
            #   width sits at CHEST height. That was the second
            #   attempt, and on the 8 Nov screenshot it put the foot
            #   marker 135 px above the stance -- worse than what it
            #   replaced.
            #
            # Neither extreme of the box works, so use the
            # DISTRIBUTION: the row by which 97% of the blob's pixels
            # are above. A thin smear is few pixels and barely moves
            # it; a gap in the middle does not move it at all,
            # because every pixel counts wherever it sits. `base` is
            # kept as it was for the callers that display it.
            _sub = _lab[by:by + bh, bx:bx + bw] == i
            _cum = np.cumsum(_sub.sum(axis=1))
            _tot = float(_cum[-1]) if len(_cum) else 0.0
            _fy = by + (min(bh - 1,
                            int(np.searchsorted(_cum, _tot * 0.97)))
                        if _tot > 0 else bh - 1)
            blobs.append({
                "x": bx, "y": by, "w": bw, "h": bh, "area": area,
                "aspect": round(bh / float(bw), 3),
                "fill": round(area / float(bw * bh), 3),
                "height_frac": round(bh / float(h), 4),
                "base": (bx + bw // 2, by + bh),
                "foot": (bx + bw // 2, _fy),
            })
        if not blobs:
            return out
        # NEAREST THE GOLFER FIRST, then largest. A blob the anchor
        # actually falls inside beats one it does not, however big.
        def _rank(b):
            cx = b["x"] + b["w"] / 2.0
            inside = 0
            dist = 0.0
            if prefer_span is not None:
                try:
                    lo, hi = float(prefer_span[0]), float(prefer_span[1])
                    inside = 0 if (lo <= cx <= hi) else 1
                    dist = 0.0 if not inside else min(abs(cx - lo),
                                                      abs(cx - hi))
                except Exception:  # noqa: BLE001
                    inside, dist = 0, 0.0
            if prefer_xy is not None:
                try:
                    px = float(prefer_xy[0])
                    inside = 0 if (b["x"] <= px <= b["x"] + b["w"]) else 1
                    dist = 0.0 if not inside else min(abs(px - b["x"]),
                                                      abs(px - b["x"] - b["w"]))
                except Exception:  # noqa: BLE001
                    inside, dist = 0, 0.0
            return (inside, dist, -b["area"])

        blobs.sort(key=_rank)
        out["ok"] = True
        out["picked_by"] = ("the golfer's own position"
                            if (prefer_xy is not None
                                or prefer_span is not None)
                            else "size alone — no golfer position given")
        out["n_blobs"] = len(blobs)
        out["main"] = blobs[0]
        out["others"] = blobs[1:6]
        return out
    except Exception:  # noqa: BLE001 - measuring must never cost a swing
        return out


def grow_span(lo, hi, want, limit):
    """Widen (lo, hi) to at least `want`, staying inside 0..limit.

    FOR CROPS THAT COME OUT TOO SMALL TO READ. The alternative --
    skipping the crop when it is under some floor -- throws the zoom
    away for exactly the small, distant golfers that need it most: a
    60 px golfer makes a 150 px crop, fails a 160 px floor, and gets
    handed the whole 1920 px hole instead.

    Grows both ways from the centre, then slides rather than spills
    when it meets an edge, so a golfer at the frame boundary still
    gets a full-width crop.
    """
    lo, hi, want, limit = int(lo), int(hi), int(want), int(limit)
    if limit <= 0:
        return lo, hi
    want = min(want, limit)
    if hi - lo >= want:
        return max(0, lo), min(limit, hi)
    pad = (want - (hi - lo)) / 2.0
    lo -= int(pad)
    hi += int(pad + 0.5)
    if lo < 0:
        hi -= lo
        lo = 0
    if hi > limit:
        lo -= (hi - limit)
        hi = limit
    return max(0, lo), min(limit, hi)


# ---- the shape gate ---------------------------------------------------

# OFF, AND THE HARNESS IS WHY. Measured on the 5 Nov composite, with
# the speckle opened away first:
#
#     the real person, by the carts      45 x  94 px, h/w 2.09
#     the thing judged a golfer         150 x 199 px, h/w 1.33
#
# WHICH THIS GATE DOES NOT CATCH, and the number is left slack rather
# than tuned down to 1.4 to fit one example. A follow-through swung
# across frame squares a blob up, so a threshold between 1.33 and 2.09
# would quietly eat real swings -- and a gate that drops a shot that
# never existed is strictly worse than a false positive nobody
# watches. 0.95 rejects only blobs actually WIDER than they are tall,
# which no standing golfer is: a backstop against a pan, a camera
# exposure step or a cloud shadow, not a golfer detector.
#
# AND THEN judge_bench KILLED THE IDEA. Run over a swing whose heat
# is dominated by the CLUB rather than the body -- a golfer who turns
# in place barely moves their torso, while the clubhead sweeps right
# across the frame -- the main blob came back 75x35, h/w 0.47. WIDER
# THAN TALL, on a real swing. A gate at 0.95 would have dropped it,
# and the whole reason for having the harness is to find that out
# here instead of on the course.
#
# So aspect is MEASURED AND REPORTED and gates nothing: 0.0 passes
# everything. The number is still worth carrying -- it is in the
# contact sheet and in the event detail, and it is the first thing to
# look at when a verdict is argued with -- but it is not evidence
# strong enough to throw a shot away on. Raise it only with a
# judge_bench run that shows what it costs.
MIN_ASPECT = 0.0

# Nothing in the tee box is four times a golfer's height. Expressed
# against the frame because that is the only scale always available.
# UNMEASURED: 0.45 is a backstop against a whole-frame wash (a camera
# exposure step, a cloud shadow crossing), not a tuned number.
MAX_HEIGHT_FRAC = 0.45


def shape_verdict(geo: dict, *, min_aspect: float = MIN_ASPECT,
                  max_height_frac: float = MAX_HEIGHT_FRAC) -> dict:
    """Is the hottest thing in this picture the shape of a golfer?

    Returns {ok, why}. `ok` true means "not obviously impossible",
    NOT "this is a swing" -- that is still the judge's question. This
    only answers the one a tape measure can.
    """
    m = (geo or {}).get("main")
    if not geo or not geo.get("ok") or not m:
        # NOT A REJECTION. No measurement is not evidence of anything,
        # and a renderer that produced nothing has already failed in a
        # way the caller handles.
        return {"ok": True, "why": "nothing measured"}
    if m["aspect"] < float(min_aspect):
        return {"ok": False, "why": (
            "the moving thing is %dx%d px — wider than it is tall "
            "(h/w %.2f, floor %.2f); a golfer is taller than wide"
            % (m["w"], m["h"], m["aspect"], min_aspect))}
    if m["height_frac"] > float(max_height_frac):
        return {"ok": False, "why": (
            "the moving thing is %.0f%% of the frame high — that is a "
            "whole-picture change, not a golfer"
            % (m["height_frac"] * 100.0))}
    return {"ok": True, "why": "h/w %.2f, %.0f%% of frame high"
            % (m["aspect"], m["height_frac"] * 100.0)}


# ---- the stance gate --------------------------------------------------

# HOW FAR SIDEWAYS the hot blob may sit from where the person detector
# last saw somebody's feet, as a multiple of the blob's own WIDTH.
#
# SIDEWAYS, NOT AS-THE-CROW-FLIES, and judge_bench is why. The first
# version measured centre-to-feet distance against the blob's HEIGHT,
# and refused a real swing at 2.4 "blob-heights": the blob was the
# club arc, 35 px tall, floating above a golfer whose feet were 85 px
# below it. Normalising by the height of a thing that is mostly club
# makes the allowance shrink exactly when the club is most visible.
#
# Horizontally there is no such trap. Whatever a swing's heat is made
# of -- body, club, divot, or all three -- it is ABOVE the golfer's
# feet, so the feet fall within the blob's horizontal span or just
# outside it. The 5 Nov false positive sat 912 px to the side of the
# only person in frame, which is nine of its own widths away.
MAX_STANCE_GAP_FRAC = 1.0

# AND AN ABSOLUTE CEILING, as a fraction of the frame width, because
# an allowance that scales with the blob lets a big blob excuse
# itself. Measured on clip 1768 through Test Cut's renderer, whose
# 4.5s window accumulates far more motion than the rig's 1.0s one:
# the blob came back 727 px wide, so a golfer 900 px away was only
# 0.74 of its own widths off and sailed through. "Anywhere within a
# third of the frame" is not a gate.
#
# 0.20 of the frame is ~384 px at 1920 -- generous against the
# run-back of a wide follow-through and against a blob that includes
# a playing partner, and still far short of a different person on a
# different tee. UNMEASURED as a number; what IS measured is that
# without any ceiling the gate stops working on exactly the wide
# blobs it was written for.
MAX_STANCE_GAP_FRAME_FRAC = 0.20

# A golfer's feet are BELOW their motion, never above it, and not a
# great distance below. This catches the other half: heat high in the
# sky above someone standing on the tee is a bird, not their swing.
MAX_STANCE_BELOW_FRAC = 3.0

# How stale a sighting may be and still count. The audio trigger's own
# confirmation gate uses 6s for "was anybody there at all"; this asks
# the sharper question "is this blob that person", so it wants a
# closer one.
STANCE_MAX_AGE_SEC = 2.5


def stance_verdict(geo: dict, stance_xy, *,
                   max_gap_frac: float = MAX_STANCE_GAP_FRAC,
                   max_gap_frame_frac: float = MAX_STANCE_GAP_FRAME_FRAC,
                   max_below_frac: float = MAX_STANCE_BELOW_FRAC) -> dict:
    """Is the hot blob standing where a person was actually detected?

    `stance_xy` is the detector's feet point in the SAME pixel space as
    the heat map, or None when no sighting was close enough in time.

    THE FREE SIGNAL NOBODY WAS USING. The rig runs a person detector
    continuously and records every sighting; the audio trigger consults
    it only for "was somebody in a zone". Whether the thing that MOVED
    is the person who was seen is a different and much sharper
    question, and the answer was already in memory.

    None means no sighting, which is not a rejection -- see the
    docstring on shape_verdict for why absence of evidence is never
    grounds for dropping a shot.
    """
    m = (geo or {}).get("main")
    if not geo or not geo.get("ok") or not m:
        return {"ok": True, "why": "nothing measured"}
    if stance_xy is None:
        return {"ok": True, "why": "no sighting near the impact"}
    try:
        sx, sy = float(stance_xy[0]), float(stance_xy[1])
    except Exception:  # noqa: BLE001
        return {"ok": True, "why": "unreadable sighting"}
    # Sideways: zero when the feet are under the blob at all.
    left, right = float(m["x"]), float(m["x"] + m["w"])
    gap_x = 0.0 if left <= sx <= right else min(abs(sx - left),
                                                abs(sx - right))
    allow_x = float(max_gap_frac) * float(m["w"])
    _fw = float(geo.get("frame_w") or 0.0)
    if _fw > 0:
        allow_x = min(allow_x, float(max_gap_frame_frac) * _fw)
    if gap_x > allow_x:
        return {"ok": False, "why": (
            "the moving thing is %.0f px to the side of the golfer the "
            "detector saw (allowed %.0f); it is not that person"
            % (gap_x, allow_x))}
    # Vertically: the feet must be below the motion, within reason.
    top, bottom = float(m["y"]), float(m["y"] + m["h"])
    if sy < top:
        return {"ok": False, "why": (
            "the moving thing is %.0f px ABOVE the golfer's feet with "
            "nothing between; that is not a swing" % (top - sy))}
    below = max(0.0, sy - bottom)
    if below > float(max_below_frac) * float(m["h"]):
        return {"ok": False, "why": (
            "the moving thing floats %.0f px above the golfer's feet "
            "(%.1f of its own heights)" % (below, below / float(m["h"])))}
    return {"ok": True, "why": (
        "over the detected golfer (%.0f px sideways, %.0f px up)"
        % (gap_x, below))}


def pick_stance(sightings, at_sec: float, *,
                max_age_sec: float = STANCE_MAX_AGE_SEC):
    """The detected feet closest in time to `at_sec`, or None.

    TWO SHAPES, because the two callers keep different records and
    neither should have to reshape its own history to ask:

        (time, x, y)            the server, from pose
        (time, zone, x, y)      the rig's `_person_seen_at`, which
                                carried (time, zone) for the audio
                                trigger's zone gate before the point
                                was added on the end for this

    Anything shorter is a sighting from before the point existed --
    a rig mid-upgrade has both in one deque -- and is skipped rather
    than guessed at: a sighting that cannot locate anything is not
    evidence about where the motion was.
    """
    best, best_dt = None, None
    for s in sightings or []:
        try:
            if len(s) >= 4:
                t, x, y = float(s[0]), float(s[2]), float(s[3])
            elif len(s) == 3:
                t, x, y = float(s[0]), float(s[1]), float(s[2])
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        dt = abs(t - float(at_sec))
        if dt > float(max_age_sec):
            continue
        if best_dt is None or dt < best_dt:
            best, best_dt = (x, y), dt
    return best


# ---- weighing the judge's answer against the microphone ---------------

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

# A MEDIUM VERDICT IS THE JUDGE SAYING IT IS NOT SURE, and on 5 Nov a
# medium "yes" on a x4.7 strike -- barely over a x4.0 gate -- was
# treated as the same evidence as a high verdict on a x40 crack. Two
# weak signals are not one strong one.
#
# So a verdict below high has to be carried by the sound: the strike
# must be this many times the floor the operator set.
#
# MEASURED, on the 29 audio-triggered clips the tee rig got ACCEPTED
# on 5-6 Nov -- the half of the corpus that can show a false positive,
# because the refused ones never reach a card. Sorted by how far over
# the x4.0 gate each strike was:
#
#   read as real swings            read as not swings
#     1.02x  high                    1.02x  medium
#     1.12x  high                    1.05x  medium
#     1.23x  high                    1.05x  medium
#     1.90x  high                    1.15x  medium
#     2.62x  high                    1.18x  medium
#     3.65x  high                    1.25x  medium
#     4.95x  MEDIUM                  1.65x  medium
#     6.83x  high
#     7.67x  high
#    10.20x  high
#
# EVERY false positive is a MEDIUM verdict at 1.65x or less. The only
# medium verdict among the swings is at 4.95x, and the quiet genuine
# swings -- x4.1, x4.5, x4.9 -- are all HIGH. The judge's confidence
# was informative the whole time and nothing was reading it.
#
# 2.0 sits in the middle of the empty gap between 1.65 and 4.95
# rather than against either edge, so it is not fitted to a boundary
# one clip either side would move. On that corpus it refuses 11 of
# 29, catches all 7 of the false positives and loses none of the 10
# swings. 2.5 and 3.0 refuse 12 and 13 and still lose none, so there
# is room to tighten if the field disagrees. Set to 0 to switch off.
MEDIUM_NEEDS_RATIO_OVER_GATE = 2.0

# A LOW VERDICT IS NOT EVIDENCE AT ALL, whichever way it points. It
# costs nothing to require the sound to carry those on its own.
LOW_NEEDS_RATIO_OVER_GATE = 2.5


def confidence_verdict(is_swing, confidence, ratio, gate_ratio, *,
                       medium_over=MEDIUM_NEEDS_RATIO_OVER_GATE,
                       low_over=LOW_NEEDS_RATIO_OVER_GATE) -> dict:
    """Does a hedged yes have a loud enough strike behind it?

    Only ever narrows a TRUE. A false verdict is the judge's to make
    and a null is "could not tell", which has always meant upload.
    """
    if is_swing is not True:
        return {"ok": True, "why": "not a yes; nothing to weigh"}
    try:
        r = float(ratio or 0.0)
        g = float(gate_ratio or 0.0)
    except Exception:  # noqa: BLE001
        return {"ok": True, "why": "no ratio to weigh"}
    if g <= 0 or r <= 0:
        return {"ok": True, "why": "no gate to measure against"}
    band = str(confidence or "").strip().lower()
    need = {"medium": float(medium_over), "low": float(low_over)}.get(band)
    if not need:
        return {"ok": True, "why": "%s confidence stands on its own"
                % (band or "unrated")}
    over = r / g
    if over < need:
        return {"ok": False, "why": (
            "a %s yes on a x%.1f strike — only %.2f times the x%.1f gate, "
            "and a hedged verdict needs %.2f" % (band, r, over, g, need))}
    return {"ok": True, "why": "a %s yes, but x%.1f is %.2f times the gate"
            % (band, r, over)}


# ---- which golfer is hitting ---------------------------------------

# THE OPERATOR'S RULE, 8 Nov, for the "audio 2" process: "crop around
# the golfer who's feet are in one of our trigger zones and the
# furthest up."
#
# WHY IT WORKS. The camera stands behind the hitting area looking down
# the hole, so further UP the picture is further AWAY. On a tee the
# player hitting has walked forward to the markers and the others are
# standing back waiting -- so the golfer whose feet sit highest in the
# frame, among those standing in a zone at all, is the one about to
# hit. Nothing in the heat map says this; it is knowledge about how
# people stand on a tee.

# How much vertical slack a foot point gets against the zone it is
# supposed to be standing in, as a multiple of that zone's own height.
# The "feet" here are the BOTTOM of a heat blob, and heat is motion:
# on a golfer whose legs barely move it stops around the waist, well
# above the real stance. One zone height of slack costs nothing --
# zones are small -- and without it audio 2 would fall back to the
# whole-zone crop on exactly the quiet swings it was written for.
FEET_ZONE_SLACK = 1.0

# AND ALMOST NONE WHEN THE FEET ARE REAL. The slack above exists for
# exactly one reason: a motion blob's bottom is the waist of a golfer
# whose legs did not move, so it sits a long way above the stance and
# needs room. A person detector's box bottom IS the stance, so the
# room is not needed -- and it is actively harmful, because one zone
# height of reach above a zone is enough to admit things standing
# nowhere near the tee. On 8 Nov it admitted a tree: the detector
# called it a person, its "feet" sat well above the zone, and the
# slack waved it through to win the pick.
#
# 0.15 of a zone's height is a cushion for the box edge jitter
# between frames, not a reach into the next part of the hole.
FEET_ZONE_SLACK_DETECTED = 0.15

# WHEN IT IS TOO CLOSE TO CALL. Two golfers level on a tee is the
# normal case while one addresses the ball and the other watches, and
# picking the wrong one by three pixels is worse than not picking: the
# operator asked for the original crop in that case.
#
# As a fraction of the leading golfer's own blob HEIGHT, so it scales
# with distance -- the same step forward is fewer pixels on a far tee
# than a near one, and a constant would be wrong at one end or the
# other.
#
# UNMEASURED. 0.25 of a golfer's height is roughly knee to ankle,
# which is more than the gap between two people standing side by side
# and less than a step toward the markers. It wants checking against
# real clips once there are some.
FRONT_GOLFER_MARGIN_FRAC = 0.25

# AND A MUCH TIGHTER ONE WHEN THE FEET ARE REAL. 0.25 of a body
# height was sized for the noise in a motion blob's bottom, which
# could be a shadow, a club, or the waist of a golfer whose legs did
# not move -- tens of pixels of slop in any direction. A person
# detector's foot point is the bottom edge of its box, which is
# steady to a few pixels frame to frame. Keeping the blob's margin
# would throw away the precision that was the whole reason for
# detecting people.
#
# LOWERED 0.08 -> 0.02 ON 8 NOV, on the operator's instruction and
# against a case that measured it. Two players on a tee, the hitter
# 8 px further up at tile scale (~24 px in the frame) against a 27 px
# bar: it declined, fell back to the whole-tee crop, and the zoom was
# lost on a clip where the rule had the right answer.
#
# What makes a small margin safe here is the operator's own reading
# of the hole: "almost always the golfer whose feet are higher up in
# the frame is the golfer" -- the player hitting has walked forward
# to the markers and the rest stand back. If that is nearly always
# true then declining is the expensive mistake, not picking.
#
# 0.02 -> 0.06 -> 0.15, and the last step is an admission that this
# number cannot do the job alone. Two clips, measured:
#
#   1406: the hitter is mid-swing and the man watching him stands
#         3 px higher in the frame. Feet 18 px apart, golfer 266 px
#         -> 0.067, and it MUST decline, because feet pick the wrong
#         man
#   the step-forward clip: the hitter has walked to the markers,
#         24 px apart, golfer 340 px -> 0.071, and it MUST separate
#
# 0.067 against 0.071. Five percent apart, opposite answers. There is
# no value of this constant that gets both right, and chasing one
# breaks the other -- which is what the first two steps were.
#
# So it is set well ABOVE both, and anything inside a seventh of a
# body height is handed to break_tie_on_motion instead. Feet keep
# the cases they are actually good at -- a golfer at the markers
# against one standing ten yards back -- and the heat takes the
# cases they were never going to settle: one golfer swung a club and
# the other did not, which is a question about motion, not position.
FRONT_GOLFER_MARGIN_DETECTED = 0.15


def break_tie_on_motion(people, blobs, offset=(0, 0)) -> dict:
    """Of these golfers, which one MOVED? {"pick": person|None, "why"}.

    THE OPERATOR'S SUGGESTION, 8 Nov, for the case the feet cannot
    settle: "maybe if the feet are close, we do the heat test with
    just the ... trigger zone for the cropping and then zoom in if
    there's a swing candidate from the heat map".

    Two players level on a tee look identical to a rule about feet,
    and on one clip that cost a legitimate swing -- the partner was
    standing well back, so the stance rule had nothing to separate
    them and declined, and the whole-tee crop went to the wrong man.
    But they do not look identical to the HEAT: one of them swung a
    club and the other stood still, and that is the entire difference
    this system exists to find.

    So the heat blobs, measured on the zone crop that is already
    built, are scored against each person's box and the person
    holding the most motion wins. `blobs` are in CROP pixels and
    `offset` is where that crop sits in the frame; `people` are in
    frame pixels.

    None when nothing overlaps anybody, which is honest: a tee with
    two still golfers on it and the motion somewhere else has not
    told us who was hitting.
    """
    out = {"pick": None, "why": "no motion to separate them"}
    try:
        ox, oy = float(offset[0]), float(offset[1])
        best, best_a = None, 0.0
        for p in people or []:
            b = p.get("box") if isinstance(p, dict) else p
            if not b:
                continue
            bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
            tot = 0.0
            for g in blobs or []:
                if not g:
                    continue
                gx0, gy0 = g["x"] + ox, g["y"] + oy
                gx1, gy1 = gx0 + g["w"], gy0 + g["h"]
                iw = max(0.0, min(bx1, gx1) - max(bx0, gx0))
                ih = max(0.0, min(by1, gy1) - max(by0, gy0))
                tot += iw * ih
            if tot > best_a:
                best, best_a = p, tot
        if best is None or best_a <= 0:
            return out
        out["pick"] = best
        out["why"] = ("the feet were level, so the heat decided: this "
                      "golfer holds %d px of the motion" % int(best_a))
        return out
    except Exception:  # noqa: BLE001
        return out


def pick_front_golfer(blobs, zones, *, offset=(0, 0),
                      slack=None,
                      margin_frac=None,
                      people=None) -> dict:
    """The golfer standing in a zone whose feet are furthest up.

    `people` are a person detector's boxes -- {"box": [x,y,w,h],
    "feet": [x,y]} in FRAME pixels -- and when any are given they are
    used INSTEAD of the blobs. That is not a preference, it is the
    only thing that works: the blob is MOTION, and a golfer at
    address or at the top of a backswing is not moving their legs, so
    the heat stops at the waist and there are no pixels down there to
    measure. Three different statistics were tried on those pixels --
    the box's bottom, the bottom of its middle half, the 97th
    percentile -- and all three reported a stance at chest height,
    because that is genuinely where the motion ended.

    `blobs` are measure_heat's person-shaped blobs, in CROP pixels,
    and remain the fallback for a backend with no model on disk.
    `offset` is where that crop sits in the original frame, because
    `zones` are (x, y, w, h) in frame pixels as the operator drew
    them.

    Returns {"pick": blob|None, "floor": int|None, "close": bool,
    "why": str}. `pick` is None when no blob's feet are in any zone,
    and `close` is true when the leader and the runner-up are too near
    to separate -- both mean "use the ordinary crop", and the caller
    is told which so the page can say why.

    `floor` is the BOTTOM EDGE of the zone that golfer is standing in,
    in frame pixels, and it is the ground line a crop should stop at.
    NOT the bottom of their blob: the blob is motion, and on a golfer
    whose legs barely move the motion stops around the waist -- which
    is exactly where the first version of this cropped, cutting them
    off at the belt. The zone is drawn round feet and does not move.
    Of the zone they are IN, not the union of all of them, because a
    second zone further down the frame would put the ground line
    somewhere they are not standing.
    """
    if margin_frac is None:
        margin_frac = (FRONT_GOLFER_MARGIN_DETECTED if people
                       else FRONT_GOLFER_MARGIN_FRAC)
    if slack is None:
        slack = (FEET_ZONE_SLACK_DETECTED if people
                 else FEET_ZONE_SLACK)
    out = {"pick": None, "floor": None, "close": False, "from": None,
           # EVERY BLOB IT LOOKED AT AND WHAT IT MEASURED. The rule
           # reads a FOOT POINT off each blob -- the bottom-centre of
           # its bounding box -- and when it picks the wrong golfer
           # the question is always "what did it think their feet
           # were", which until now could only be guessed at from the
           # crop that came out. Each entry is
           # {feet: [x, y], box: [x, y, w, h], zone: bool,
           #  floor: y|None, picked: bool} in FRAME pixels.
           "considered": [],
           "why": "no zones drawn"}
    if not zones:
        return out
    try:
        ox, oy = float(offset[0]), float(offset[1])
        # THE DETECTOR'S PEOPLE WHEN THERE ARE ANY, in frame pixels
        # already; the heat blobs otherwise, which need the crop
        # offset adding. Everything downstream works on (fx, fy) and
        # a box, so the two sources meet here and nowhere else.
        if people:
            _src = [(float(p["feet"][0]), float(p["feet"][1]),
                     dict(p, x=p["box"][0], y=p["box"][1],
                          w=p["box"][2], h=p["box"][3],
                          area=p["box"][2] * p["box"][3]))
                    for p in people if p and p.get("feet")]
            out["from"] = "the person detector"
        else:
            _src = []
            for b in blobs or []:
                if not b:
                    continue
                _ft = b.get("foot") or b.get("base") or (0, 0)
                _src.append((float(_ft[0]) + ox, float(_ft[1]) + oy, b))
            out["from"] = "the motion heat (no person detector)"
        standing = []
        for fx, fy, b in _src:
            # THE ZONE THEY ARE ACTUALLY IN, not the first one in the
            # list that the slack happens to reach. Taking the first
            # match put a golfer standing in the upper box on the
            # LOWER box's ground line, because one zone height of
            # slack above a near zone easily reaches a far one, and
            # the crop then started a long way below their feet.
            #
            # Scored by how far the foot point is OUTSIDE each zone:
            # zero when it is genuinely inside, so a real containment
            # always beats a slack reach, and the nearest zone wins
            # among several that only reach.
            best_d, best_floor = None, None
            for z in zones:
                zx, zy, zw, zh = (float(z[0]), float(z[1]),
                                  float(z[2]), float(z[3]))
                if not (zx <= fx <= zx + zw):
                    continue
                if fy < zy:
                    d = zy - fy
                elif fy > zy + zh:
                    d = fy - (zy + zh)
                else:
                    d = 0.0
                if d > float(slack) * zh:
                    continue
                if best_d is None or d < best_d:
                    best_d, best_floor = d, zy + zh
            _bo = 0 if people else int(ox)
            _byo = 0 if people else int(oy)
            out["considered"].append({
                "feet": [int(fx), int(fy)],
                "box": [int(b["x"]) + _bo, int(b["y"]) + _byo,
                        int(b["w"]), int(b["h"])],
                "area": int(b.get("area") or 0),
                "zone": best_floor is not None,
                "floor": None if best_floor is None else int(best_floor),
                # WHICH MOMENT THIS PERSON WAS SEEN AT. Detection runs
                # at six offsets and a golfer hidden at the impact is
                # found half a second away; a stance read from there
                # should be recognisable as such.
                "seen_at": b.get("seen_at"),
                "picked": False,
            })
            if best_floor is not None:
                standing.append((fy, b, best_floor))
        if not standing:
            out["why"] = ("nobody's feet are in a trigger zone (%d blob(s) "
                          "looked at)" % len(out["considered"]))
            return out
        standing.sort(key=lambda t: t[0])
        lead_y, lead, lead_floor = standing[0]
        if len(standing) > 1:
            gap = standing[1][0] - lead_y
            need = float(margin_frac) * float(lead.get("h") or 1)
            if gap < need:
                out["close"] = True
                out["pick"] = None
                out["why"] = (
                    "two golfers level in the zones (%.0f px apart, needs "
                    "%.0f) — too close to call which is hitting"
                    % (gap, need))
                return out
        out["pick"] = lead
        out["floor"] = int(lead_floor)
        for _c in out["considered"]:
            if _c["feet"][1] == int(lead_y) and _c["box"][2] == lead["w"]:
                _c["picked"] = True
                break
        out["why"] = ("the golfer standing furthest up the tee (%d in a "
                      "zone; ground line y=%d, from the zone they are "
                      "standing in)" % (len(standing), int(lead_floor)))
        return out
    except Exception:  # noqa: BLE001
        out["why"] = "could not read the blobs"
        return out


# ---- when the two judges disagree ----------------------------------

# THE OPERATOR'S TIE-BREAK, 7 Nov. The heat map and the four frames
# answer different questions about the same moment, and when they
# contradict each other somebody has to choose. Until now the frames
# always won -- a reasonable default, never measured, and it buried
# the disagreement rather than resolving it.
#
# The microphone is the third opinion, and it is the one piece of
# evidence neither judge can see. A strike loud enough is a strike:
# "if it's over 10, call it a swing. if not, no swing."
#
# ABSOLUTE, not a multiple of the rig's gate. The gate is a trigger
# threshold that moves per rig and per hole; this is a statement about
# how loud a struck ball is, and it should not drift when somebody
# retunes a microphone.
TIEBREAK_RATIO = 10.0


def tiebreak(heat_is_swing, frames_is_swing, ratio, *,
             over=TIEBREAK_RATIO) -> dict:
    """Settle a contradiction between the heat and the frames.

    Returns {"split": bool, "is_swing": bool|None, "why": str}. When
    the two agree, or either could not answer, there is nothing to
    settle and `split` is false -- the caller keeps whatever it was
    going to use. A null from one of them is NOT a contradiction: it
    is "could not tell", and the one that did answer stands.
    """
    if heat_is_swing is None or frames_is_swing is None:
        return {"split": False, "is_swing": None,
                "why": "one judge had no opinion; nothing to settle"}
    if bool(heat_is_swing) == bool(frames_is_swing):
        return {"split": False, "is_swing": bool(heat_is_swing),
                "why": "both judges agree"}
    try:
        r = float(ratio or 0.0)
    except Exception:  # noqa: BLE001
        r = 0.0
    if r <= 0:
        # NO SOUND TO ASK. Falls back to the frames, which is what
        # this did before the tie-break went in: the frames at least
        # show a club and a body through time.
        return {"split": True, "is_swing": bool(frames_is_swing),
                "why": ("heat says %s and the frames say %s, and there "
                        "is no x-ratio to break the tie — the frames "
                        "stand" % ("swing" if heat_is_swing else "not",
                                   "swing" if frames_is_swing else "not"))}
    _win = r > float(over)
    return {"split": True, "is_swing": _win,
            "why": ("heat says %s and the frames say %s; the strike is "
                    "x%.1f, %s the x%.1f tie-break, so: %s"
                    % ("swing" if heat_is_swing else "not",
                       "swing" if frames_is_swing else "not",
                       r, "over" if _win else "under", float(over),
                       "swing" if _win else "not a swing"))}


def decide_verdict(heat_is_swing, frames_is_swing, ball_left, ratio, *,
                   over=TIEBREAK_RATIO) -> dict:
    """The verdict from all three tests. Never raises.

    THE OPERATOR'S RULE, 8 Nov: "we've got 3 good tests - let's just
    go with the majority. if ball test can't happen because of no
    zoom or obstruction, then use the xaudio as tie break."
    
    So the ball stops being a trump card. It was overruling two
    judges that agreed, on the strength of a few bright pixels, and
    on one clip it called a shoe a ball and said so out loud. As one
    vote of three it still decides every case where the judges split
    -- which is the case it was brought in for -- and cannot overturn
    them when they agree.

    A test that could not answer does not vote. With the ball absent
    the other two either agree, which settles it, or they do not,
    which is where the x-ratio comes back in. An even split with no
    ratio to weigh is left as None: could not tell.

    Returns {"is_swing", "confidence", "why", "how"}.
    """
    votes = [("the heat map", heat_is_swing),
             ("the frames", frames_is_swing),
             ("the ball", ball_left)]
    live = [(n, v) for n, v in votes if v is not None]
    yes = [n for n, v in live if v is True]
    no = [n for n, v in live if v is False]
    if len(live) < 2:
        if len(live) == 1:
            return {"is_swing": live[0][1], "confidence": "sole",
                    "how": "one test", "why": "only %s could answer"
                    % live[0][0]}
        return {"is_swing": None, "confidence": None, "how": "nothing",
                "why": "no test could answer"}
    if len(yes) != len(no):
        win = len(yes) > len(no)
        return {"is_swing": win, "confidence": "majority",
                "how": "majority",
                "why": "%d of %d say %s (%s)"
                       % (max(len(yes), len(no)), len(live),
                          "swing" if win else "not a swing",
                          ", ".join(yes if win else no))}
    # DEAD EVEN, which with three tests means the ball abstained.
    tb = tiebreak(heat_is_swing, frames_is_swing, ratio, over=over)
    if tb["split"]:
        return {"is_swing": tb["is_swing"], "confidence": "tiebreak",
                "how": "x-ratio", "why": tb["why"]}
    return {"is_swing": None, "confidence": None, "how": "even",
            "why": "the tests are evenly split and nothing breaks it"}


# ---- where the ball left from --------------------------------------

# How far from the golfer who swung a chain's launch point may sit,
# as a fraction of the frame width.
#
# THE EXISTING `outside` GATE ALREADY RUNS A CHAIN'S HEADING BACK to
# the tee line and refuses it if it lands outside the hole's tee box.
# That is the right idea against the wrong box: the zones on hole 8
# are two wide flat strips spanning most of the frame, so a chain
# landing anywhere along them passes. On the 6 Nov sweep the accepted
# chain ran back to x~1018 while the golfer who swung stood at
# x~1790 -- 772 px apart, 40% of the frame -- and it was accepted
# because 1018 is inside the tee box too. It is nearer the man
# standing with his club at rest.
#
# A ball is struck from where the golfer is standing. 0.12 of the
# frame is ~230 px at 1920: wide enough for the run-back
# extrapolation's own error and for a golfer who has followed through
# across their stance, far short of a different person.
#
# UNMEASURED against a corpus. It fires only where a caller supplies
# an anchor, so a pipeline that has no idea which golfer swung is
# unaffected.
AIM_MAX_DX_FRAC = 0.12


def aim_verdict(from_x, aim_x, frame_w, *,
                max_dx_frac: float = AIM_MAX_DX_FRAC) -> dict:
    """Did this ball leave from in front of the golfer who swung?

    `from_x` is where the chain's own heading, run back to the tee
    line, says it started. `aim_x` is where the golfer who swung was
    -- the ball at rest, the stance point, or the base of the swing's
    heat, whichever the caller has.

    None for either is not a rejection. Most of the pipeline does not
    know which golfer swung, and a gate that fires on absent evidence
    would refuse every chain on every clip that never asked.
    """
    if from_x is None or aim_x is None or not frame_w:
        return {"ok": True, "why": "no golfer to aim at"}
    try:
        dx = abs(float(from_x) - float(aim_x))
        allow = float(max_dx_frac) * float(frame_w)
    except Exception:  # noqa: BLE001
        return {"ok": True, "why": "unreadable aim"}
    if dx > allow:
        return {"ok": False, "why": (
            "runs back to x=%.0f, but the golfer who swung was at "
            "x=%.0f — %.0f px away, %.0f%% of the frame"
            % (from_x, aim_x, dx, dx / float(frame_w) * 100.0))}
    return {"ok": True, "why": "leaves from the golfer who swung (%.0f px)"
            % dx}


# ---- the whole decision, in one place ---------------------------------

def prejudge(geo: dict, stance_xy, **kw) -> dict:
    """Everything decidable BEFORE spending a judge call.

    Returns {ask, why, checks}. `ask` false means do not call the
    model: the picture cannot be a golfer and the ~13 seconds a cut
    costs on a cellular link is better not spent.
    """
    checks = {
        "shape": shape_verdict(geo, **{k: v for k, v in kw.items()
                                       if k in ("min_aspect",
                                                "max_height_frac")}),
        "stance": stance_verdict(geo, stance_xy,
                                 **{k: v for k, v in kw.items()
                                    if k in ("max_gap_frac",
                                             "max_gap_frame_frac",
                                             "max_below_frac")}),
    }
    bad = [v["why"] for v in checks.values() if not v["ok"]]
    return {"ask": not bad, "why": "; ".join(bad) or "nothing rules it out",
            "checks": checks}


def postjudge(is_swing, confidence, ratio, gate_ratio, **kw) -> dict:
    """Everything decidable AFTER the judge has answered."""
    c = confidence_verdict(is_swing, confidence, ratio, gate_ratio, **kw)
    return {"keep": c["ok"], "why": c["why"], "checks": {"confidence": c}}
