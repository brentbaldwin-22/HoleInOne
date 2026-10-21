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
import time
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


def _window_frames(cap, centre: int, fps: float, n: int = PLATE_FRAMES,
                   span_sec: float = PLATE_SPAN_SEC):
    """`n` frames spread across `span_sec` around `centre`, read ONCE.

    SEEKING PER FRAME IS WHAT MADE THIS UNUSABLE. The first version
    called cap.set(POS_FRAMES) for every sample -- 31 per plate, twice,
    and then seven more per candidate for the persistence check, which
    rewound to the start of the window each time. On a two-minute
    1080p60 clip that is a few hundred seeks, most of them backwards,
    and each backward seek makes the decoder restart from the preceding
    keyframe. It ran for ten minutes and Render's gateway gave up
    first.

    So: one seek to the start of the window, then walk forward. Frames
    we do not want are grab()ed rather than read() -- the decoder still
    has to run but the colour conversion and the copy are skipped,
    which is most of the cost of a frame we are going to discard.

    The frames come back as a LIST rather than a median, because the
    persistence check wants the same frames and fetching them twice is
    what this function exists to stop.
    """
    total = _frame_count(cap)
    half = max(1, int(round(span_sec * max(1.0, fps) / 2.0)))
    lo, hi = max(0, centre - half), min(total - 1, centre + half)
    if hi <= lo:
        return []
    wanted = set(
        np.linspace(lo, hi, num=max(3, n)).round().astype(int).tolist())
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(lo))
    frames = []
    for idx in range(lo, hi + 1):
        if idx in wanted:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        elif not cap.grab():
            break
    return frames


def _median(frames):
    """The plate. None when there were not enough frames to be honest."""
    if len(frames) < 3:
        return None
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def scaled_polygon(polygon, shape, drawn_at=None):
    """[[x,y], ...] in THIS frame's pixels, or None for no polygon.

    Rescaled when it was drawn on a differently-sized still, which it
    usually was: the editor works on whatever the live view is serving
    and the capture may be another resolution entirely.
    """
    h, w = shape[:2]
    if not polygon:
        return None
    pts = np.array(polygon, dtype=np.float64)
    if drawn_at:
        dw, dh = float(drawn_at[0] or w), float(drawn_at[1] or h)
        if dw > 0 and dh > 0:
            pts[:, 0] *= w / dw
            pts[:, 1] *= h / dh
    return pts.round().astype(np.int32)


def polygon_mask(polygon, shape, drawn_at=None):
    """A filled mask from [[x,y], ...]. None polygon means everything."""
    h, w = shape[:2]
    pts = scaled_polygon(polygon, shape, drawn_at)
    if pts is None:
        return np.full((h, w), 255, np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask


# WHERE THE AFTER PLATE GOES WHEN NOBODY SAYS, in seconds from the
# start of the clip.
#
# IT USED TO BE "NEAR THE END", which is only sensible on a short clip.
# Rivertowne 8, event 1164: a two-minute capture, so the after plate
# landed at 118 seconds — by which time the group was standing ON the
# green and the shadow edge had crawled several feet. Twenty-two
# arrivals, nineteen departures, every one of them a real difference
# between two pictures taken two minutes apart. The detector was right
# and the question was wrong.
#
# The ball is down and still within a few seconds of the shot. Waiting
# any longer buys nothing and risks everything that walks, mows or
# moves with the sun. Clipped to what the clip can give, so a 27-second
# capture still works.
DEFAULT_AFTER_SEC = 30.0

# ...AND HOW FAR APART IS TOO FAR to pass without comment. On a green,
# a minute is long enough for the group to arrive and for shade to
# move, and both look exactly like things arriving on the putting
# surface — because that is what they are.
PLATE_GAP_WARN_SEC = 60.0

# HOW BIG A GLOBAL SHIFT IS WORTH MENTIONING, in grey levels. Below
# this the match is housekeeping; above it the operator should be told,
# because an exposure change is the one failure here that produces
# hundreds of confident, wrong answers rather than none.
EXPOSURE_NOTE_LEVELS = 4.0

# HOW FAR THE MATCH MAY STRETCH THE PLATE. Beyond this the two plates
# are not the same scene under different light, they are a camera that
# re-aimed or an iris that slammed, and silently rescaling one onto the
# other would invent a comparison rather than correct one.
MATCH_GAIN_LIMIT = (0.5, 2.0)

# HOW MANY RINGS ARE WORTH DRAWING. 932 of them painted the before
# panel solid grey and told the operator nothing at all.
MAX_DRAWN = 12


def _match_exposure(plate_b, plate_a, mask):
    """Put the before plate on the after plate's exposure.

    THE FAILURE THIS EXISTS FOR, seen on Rivertowne 8: plates two
    seconds and twenty-five seconds in, auto-exposure moved between
    them, and the whole frame arrived dimmer. Every textured patch on
    the green was then "darker than before" by more than the threshold,
    and the run reported NINE HUNDRED AND THIRTY-TWO things leaving the
    green. Not a near miss -- hundreds of confident wrong answers, and
    the arrivals were no more trustworthy.

    Matched on the median and the MAD rather than the mean and the
    standard deviation, so that a ball, a golfer, or a hundred of them
    cannot drag the correction: they are a tiny minority of a green's
    pixels and robust statistics ignore minorities. Returns the
    rescaled plate and the shift it took out, which the caller reports.
    """
    m = mask > 0
    if int(m.sum()) < 100:
        return plate_b, 0.0, 1.0
    b = plate_b[m].astype(np.float64)
    a = plate_a[m].astype(np.float64)
    mb, ma = float(np.median(b)), float(np.median(a))
    sb = float(np.median(np.abs(b - mb))) or 1.0
    sa = float(np.median(np.abs(a - ma))) or 1.0
    gain = min(max(sa / sb, MATCH_GAIN_LIMIT[0]), MATCH_GAIN_LIMIT[1])
    out = (plate_b.astype(np.float64) - mb) * gain + ma
    return np.clip(out, 0, 255).astype(np.uint8), ma - mb, gain


def _render(plate_b, plate_a, poly, arrivals, departures, out_path,
            caption=""):
    """The two plates side by side, with what was found ringed.

    COORDINATES ARE NOT AN ANSWER. "1247, 638" tells an operator
    nothing about whether the thing found is a ball, and the only way
    to judge a run of this is to look at the same picture it looked at.
    Before on the left, after on the right, so the comparison the
    detector made is the comparison the eye makes.
    """
    h, w = plate_a.shape[:2]
    pair = np.zeros((h, w * 2, 3), np.uint8)
    pair[:, :w] = cv2.cvtColor(plate_b, cv2.COLOR_GRAY2BGR)
    pair[:, w:] = cv2.cvtColor(plate_a, cv2.COLOR_GRAY2BGR)
    u = max(1, int(round(max(w, h) / 500.0)))

    if poly is not None:
        for dx in (0, w):
            shifted = poly.copy()
            shifted[:, 0] += dx
            cv2.polylines(pair, [shifted], True, (120, 230, 170), u)

    # Rings go on the AFTER panel, which is where the thing is.
    for i, a in enumerate(arrivals):
        c = (60, 170, 250) if i == 0 else (90, 200, 255)
        cv2.circle(pair, (a["x"] + w, a["y"]), 11 * u, c, u + 1)
        cv2.putText(pair, "at rest" if i == 0 else f"#{i + 1}",
                    (a["x"] + w + 14 * u, a["y"] - 10 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * u, c, u)
    # ...and departures on the BEFORE panel, because that is the only
    # panel they are in. Ringing them on the right would point at bare
    # grass and look like a miss.
    for d in departures[:MAX_DRAWN]:
        cv2.circle(pair, (d["x"], d["y"]), 9 * u, (170, 170, 170), u)
    if len(departures) > MAX_DRAWN:
        cv2.putText(pair, f"+{len(departures) - MAX_DRAWN} more",
                    (10 * u, 46 * u), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5 * u, (170, 170, 170), u)

    cv2.line(pair, (w, 0), (w, h), (40, 40, 40), max(1, u))
    for label, x in (("BEFORE", 10 * u), ("AFTER", w + 10 * u)):
        cv2.putText(pair, label, (x, 24 * u), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6 * u, (255, 255, 255), u + 1)
    if caption:
        cv2.putText(pair, caption, (10 * u, h - 12 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * u, (255, 255, 255), u)
    # imwrite SIGNALS FAILURE BY RETURNING FALSE, and does not raise.
    # An unwritable path would otherwise come back as a reported image
    # that is not there, which is worse than no image: the caller links
    # to it and the operator gets a broken tile.
    if not cv2.imwrite(str(out_path), pair):
        raise OSError(f"could not write {out_path}")
    return out_path


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


def _persistence(frames, blob, reference, sign: int = 1):
    """In how many of the after frames is this blob actually there?

    The median's blind spot: something that sat still for half the
    window shows up in it. A ball that has come to rest is in every
    frame. Measured on the frames the plate was already built from --
    they are the after window, and re-reading them was what made the
    first version of this take minutes.
    """
    if not frames:
        return 0.0
    x, y = blob["x"], blob["y"]
    r = 2
    h, w = frames[0].shape[:2]
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    base = float(reference[y0:y1, x0:x1].mean())
    step = max(1, len(frames) // PERSIST_SAMPLES)
    sampled = frames[::step] or frames
    hits = sum(
        1 for g in sampled
        if sign * (float(g[y0:y1, x0:x1].mean()) - base)
        >= DIFF_THRESHOLD * PERSIST_REL
    )
    return hits / len(sampled)


def find_rest_spot(input_path, polygon=None, drawn_at=None,
                   before_frame: int | None = None,
                   after_frame: int | None = None,
                   before_sec: float | None = None,
                   after_sec: float | None = None,
                   debug_path=None) -> dict:
    """What arrived on the green, and what left it.

    The two plates are centred on `before_frame` / `after_frame`, or on
    `before_sec` / `after_sec` if you would rather say when than which
    — converted here, against the clip's OWN frame rate, because a
    caller that assumes 60 is wrong the first time a camera is
    recaptured and wrong silently.

    Unset, they default to near each end of the clip: the question
    "where did it end up" asked of the whole capture. That default is
    worth overriding when the group reached the green before the clip
    stopped, because the ball is in their pocket by then.
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
        if before_frame is None and before_sec is not None:
            before_frame = int(round(float(before_sec) * fps))
        if after_frame is None and after_sec is not None:
            after_frame = int(round(float(after_sec) * fps))
        b = int(before_frame if before_frame is not None else pad)
        if after_frame is None:
            after_frame = min(int(round(DEFAULT_AFTER_SEC * fps)),
                              total - 1 - pad)
        a = int(after_frame)
        # A PLATE NEEDS ROOM EITHER SIDE OF ITS CENTRE. Asked for one at
        # the very first or very last frame, clamp rather than refuse:
        # "2 seconds in" is a reasonable thing to ask for and the
        # operator should not have to know the span to say it.
        b = max(pad, min(b, total - 1 - pad))
        a = max(pad, min(a, total - 1 - pad))
        if a <= b:
            return {"ok": False,
                    "error": "the after plate must come after the before one"}

        # IN CLIP ORDER, so the decoder only ever walks forwards. The
        # before window is earlier than the after one by construction
        # (checked above), so reading them in this order means one
        # forward seek each instead of a rewind between them.
        _t = time.monotonic()
        frames_b = _window_frames(cap, b, fps)
        frames_a = _window_frames(cap, a, fps)
        read_sec = time.monotonic() - _t
        _t = time.monotonic()
        plate_b, plate_a = _median(frames_b), _median(frames_a)
        if plate_b is None or plate_a is None:
            return {"ok": False, "error": "not enough frames to build a plate"}

        poly = scaled_polygon(polygon, plate_a.shape, drawn_at)
        mask = polygon_mask(polygon, plate_a.shape, drawn_at)
        # MATCHED FIRST. Without this a shift in auto-exposure between
        # the two plates makes every textured patch on the green either
        # an arrival or a departure. See `_match_exposure`.
        matched_b, shift, gain = _match_exposure(plate_b, plate_a, mask)
        diff = plate_a.astype(np.int16) - matched_b.astype(np.int16)
        pos = (diff >= DIFF_THRESHOLD).astype(np.uint8) * 255
        neg = (diff <= -DIFF_THRESHOLD).astype(np.uint8) * 255

        arrivals = _blobs(pos, diff, mask)
        departures = _blobs(neg, diff, mask)

        for blob in arrivals:
            blob["persist"] = round(
                _persistence(frames_a, blob, matched_b), 2)
        arrivals = [x for x in arrivals if x["persist"] >= PERSIST_FRAC]
        # A DEPARTURE HAS TO BE GONE, not merely dimmer in the median.
        # Same discipline as an arrival and for the same reason: the
        # median can be swayed by something that sat still for part of
        # the window, and a flicker is not a ball being picked up.
        for blob in departures:
            blob["persist"] = round(
                _persistence(frames_a, blob, matched_b, sign=-1), 2)
        departures = [x for x in departures if x["persist"] >= PERSIST_FRAC]
        # Steadiest first, then brightest. Not biggest: at the back of a
        # green the ball is a handful of pixels and the biggest thing
        # that survives the filters is more likely to be a shadow.
        arrivals.sort(key=lambda z: (-z["persist"], -z["strength"]))

        notes = []
        _gap = (a - b) / fps
        if _gap > PLATE_GAP_WARN_SEC:
            notes.append(
                f"The two plates are {_gap:.0f} seconds apart. On a green "
                "that is long enough for the group to walk on and for the "
                "shade to move, and both read as things arriving — because "
                "that is what they are. Pull the after plate back to just "
                "after the ball settled.")
        if abs(shift) >= EXPOSURE_NOTE_LEVELS or abs(gain - 1.0) >= 0.1:
            notes.append(
                f"The camera's exposure moved between the two plates "
                f"({shift:+.0f} grey levels, gain {gain:.2f}). Corrected "
                "before comparing — without that, every textured patch "
                "reads as something that arrived or left.")
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

        detect_sec = time.monotonic() - _t
        _t = time.monotonic()
        image = None
        if debug_path is not None:
            try:
                cap_txt = (
                    f"{len(arrivals)} arrival(s), {len(departures)} "
                    f"departure(s) - plates {b / fps:.1f}s and {a / fps:.1f}s")
                image = str(_render(matched_b, plate_a, poly, arrivals,
                                    departures, Path(debug_path), cap_txt))
            except Exception as exc:  # noqa: BLE001
                log.warning("rest-spot debug render failed: %s", exc)

        # TIMED, AND REPORTED. The first version of this ran for ten
        # minutes on the real machine and under nine seconds here, so
        # there was no way to tell from a failure which part was slow
        # or whether a change had helped. A number measured where the
        # work actually happens beats an estimate made where it does
        # not.
        return {
            "ok": True,
            "exposure_shift": round(shift, 1),
            "exposure_gain": round(gain, 3),
            "timing": {
                "read_sec": round(read_sec, 2),
                "detect_sec": round(detect_sec, 2),
                "render_sec": round(time.monotonic() - _t, 2),
                "frames_read": len(frames_b) + len(frames_a),
                "clip_frames": total,
            },
            "image_path": image,
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
