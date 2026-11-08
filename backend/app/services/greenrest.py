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


def render_marks(plate, poly, marks, out_path, caption=""):
    """One picture of the green with named points on it.

    PIXELS ARE NOT AN ANSWER TO A PERSON. "1213, 717" tells an operator
    nothing about whether the ball is on the green, let alone where on
    it, and the two numbers that matter most -- where it pitched and
    where it finished -- only mean something next to each other.

    `marks` is [{"xy": [x, y], "label": str, "colour": (b,g,r)}, ...],
    drawn in order with a dashed line between consecutive pairs, so the
    run-out from pitch mark to resting place reads as a run-out rather
    than as two unrelated rings.
    """
    h, w = plate.shape[:2]
    img = cv2.cvtColor(plate, cv2.COLOR_GRAY2BGR)
    u = max(1, int(round(max(w, h) / 500.0)))
    if poly is not None:
        cv2.polylines(img, [poly], True, (120, 230, 170), u)

    pts = [m for m in marks if m.get("xy")]
    # The run-out, dashed so it reads as a path and not as a chord of
    # something. Drawn under the rings.
    for a, b in zip(pts, pts[1:]):
        x0, y0 = int(a["xy"][0]), int(a["xy"][1])
        x1, y1 = int(b["xy"][0]), int(b["xy"][1])
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) / (6 * u)))
        for k in range(0, n, 2):
            t0, t1 = k / n, min(1.0, (k + 1) / n)
            cv2.line(img,
                     (int(x0 + (x1 - x0) * t0), int(y0 + (y1 - y0) * t0)),
                     (int(x0 + (x1 - x0) * t1), int(y0 + (y1 - y0) * t1)),
                     (235, 235, 235), max(1, u))
    for m in pts:
        x, y = int(m["xy"][0]), int(m["xy"][1])
        c = tuple(m.get("colour") or (60, 170, 250))
        cv2.circle(img, (x, y), 10 * u, c, u + 1)
        cv2.line(img, (x - 15 * u, y), (x - 11 * u, y), c, u)
        cv2.line(img, (x + 11 * u, y), (x + 15 * u, y), c, u)
        cv2.putText(img, str(m.get("label") or ""),
                    (x + 14 * u, y - 10 * u), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5 * u, c, u)
    if caption:
        cv2.putText(img, caption, (10 * u, h - 12 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * u, (255, 255, 255), u)
    if not cv2.imwrite(str(out_path), img):
        raise OSError(f"could not write {out_path}")
    return out_path


def _outline(img, poly, colour=(120, 230, 170), u=1):
    """The green's outline on a BGR copy, or the copy unchanged."""
    out = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    out = out.copy()
    if poly is not None:
        cv2.polylines(out, [poly], True, colour, u)
    return out


def _caption(img, text, u=1):
    """One line along the bottom, on a strip dark enough to read on.

    SHRUNK UNTIL IT FITS. A caption written off the right-hand edge of
    the picture is worse than none: it reads as a complete sentence
    that happens to say the wrong thing. OpenCV will measure the
    string, so measure it rather than guess a length that works on one
    frame size.
    """
    if not text:
        return img
    h, w = img.shape[:2]
    _sc = 0.45 * u
    (_tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, _sc, u)
    if _tw > w - 16 * u:
        _sc *= max(0.35, (w - 16 * u) / float(max(1, _tw)))
    cv2.rectangle(img, (0, h - 22 * u), (w, h), (20, 20, 20), -1)
    cv2.putText(img, text, (8 * u, h - 7 * u),
                cv2.FONT_HERSHEY_SIMPLEX, _sc, (235, 235, 235),
                max(1, u if _sc >= 0.4 * u else 1))
    return img


def _stage_images(plate_b, plate_a, diff, pos, mask, poly, arrivals,
                  rejects, out_dir, stem):
    """Every picture the comparison actually looked at, in order.

    ONE SIDE-BY-SIDE AT THE END IS A VERDICT, NOT A METHOD. It shows
    the two plates and a ring, which answers "is the thing it found a
    ball" and nothing else. The four questions it cannot answer are
    the four places this goes wrong:

      * is the BEFORE plate clean, or did the median catch a person
        standing still?
      * is the AFTER plate from before the group walked on?
      * did the exposure match work, or is the whole green glowing?
      * what crossed the threshold, and what shape was it?

    So each stage is written as its own picture. They cost four JPEG
    encodes of frames already in memory -- no decoding, no seeking --
    and they are what turns "it found nothing" from an opinion into
    something that can be looked at.

    Returns [{name, title, why, path}], newest stage last. Never
    raises: a picture that fails to write loses a picture, not a run.
    """
    out = []
    try:
        h, w = plate_a.shape[:2]
        u = max(1, int(round(max(w, h) / 500.0)))
        _dir = Path(out_dir)

        # 1 + 2. THE TWO PLATES, as the comparison sees them: the
        # before one already matched onto the after one's exposure,
        # because that is the picture actually subtracted. Showing the
        # raw before plate instead would hide the one correction that
        # most often decides the answer.
        _b = _caption(_outline(plate_b, poly, u=u),
                      "BEFORE plate - the median, exposure-matched onto "
                      "the after plate", u)
        _pb = _dir / f"{stem}-plate-before.jpg"
        cv2.imwrite(str(_pb), _b, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        out.append({
            "name": "plate-before", "path": str(_pb),
            "title": "1 · The before plate",
            "why": ("The median of the frames around the before time, "
                    "already scaled and shifted onto the after plate's "
                    "exposure -- this is the picture that gets "
                    "subtracted, not the raw frames. A person standing "
                    "still here is a person the median failed to "
                    "remove, and they will cancel a real ball sitting "
                    "behind them.")})

        _a = _caption(_outline(plate_a, poly, u=u),
                      "AFTER plate - the median of the frames around the "
                      "after time", u)
        _pa = _dir / f"{stem}-plate-after.jpg"
        cv2.imwrite(str(_pa), _a, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        out.append({
            "name": "plate-after", "path": str(_pa),
            "title": "2 · The after plate",
            "why": ("The same median around the after time. If the "
                    "group is on the green in this picture, the ball is "
                    "in somebody's pocket and the run cannot find it -- "
                    "pull the after time back until this shows an empty "
                    "green with one ball on it.")})

        # 3. THE SUBTRACTION, SIGNED. Red is brighter than before --
        # something arrived; blue is dimmer -- something left. A green
        # that glows red all over is an exposure match that did not
        # take, and that is invisible in every other picture here.
        _d = np.clip(diff, -60, 60).astype(np.float32)
        _col = np.zeros((h, w, 3), np.uint8)
        _col[:, :, 2] = np.clip(_d, 0, None) * (255.0 / 60.0)
        _col[:, :, 0] = np.clip(-_d, 0, None) * (255.0 / 60.0)
        _col[:, :, 1] = (plate_a.astype(np.float32) * 0.5).astype(np.uint8)
        _col = _caption(_outline(_col, poly, u=u),
                        "AFTER minus BEFORE - red arrived, blue left, "
                        "green channel is the plate underneath", u)
        _pd = _dir / f"{stem}-diff.jpg"
        cv2.imwrite(str(_pd), _col, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        out.append({
            "name": "diff", "path": str(_pd),
            "title": "3 · After minus before",
            "why": ("Red is brighter than it was -- something arrived. "
                    "Blue is dimmer -- something left. The green "
                    "channel is the plate itself, so the turf is "
                    "visible underneath. A green that glows red all "
                    "over is an exposure match that did not take, and "
                    "that failure is invisible in every other picture "
                    "here.")})

        # 4. WHAT CROSSED THE THRESHOLD, AND WHAT HAPPENED TO IT.
        # The binary mask is the honest answer to "was the ball even a
        # candidate" -- everything after this point is filtering, and
        # a ball that is not white in this picture was never in the
        # running.
        _m = cv2.bitwise_and(pos, mask)
        _bin = cv2.cvtColor(_m, cv2.COLOR_GRAY2BGR)
        _bin[:, :, 1] = np.maximum(
            _bin[:, :, 1], (plate_a.astype(np.float32) * 0.22).astype(np.uint8))
        _bin = _outline(_bin, poly, u=u)
        for r in (rejects or [])[:MAX_DRAWN]:
            cv2.rectangle(_bin, (r["x"] - 7 * u, r["y"] - 7 * u),
                          (r["x"] + 7 * u, r["y"] + 7 * u),
                          (120, 120, 120), u)
        for a_ in arrivals:
            cv2.circle(_bin, (a_["x"], a_["y"]), 11 * u, (60, 170, 250), u)
        _bin = _caption(
            _bin, "At least %d levels brighter, inside the outline - "
                  "%d kept, %d turned down"
                  % (DIFF_THRESHOLD, len(arrivals), len(rejects or [])), u)
        _pm = _dir / f"{stem}-threshold.jpg"
        cv2.imwrite(str(_pm), _bin, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        out.append({
            "name": "threshold", "path": str(_pm),
            "title": "4 · What crossed the threshold",
            "why": ("White is every pixel at least %d grey levels "
                    "brighter than the before plate, inside the "
                    "outline. Everything after this point only "
                    "filters: blue rings are kept, grey squares are "
                    "turned down. A ball that is not white HERE was "
                    "never a candidate, and no gate is to blame for "
                    "it." % DIFF_THRESHOLD)})
    except Exception as exc:  # noqa: BLE001
        log.warning("rest-spot stage images failed: %s", exc)
    return out


def _render(plate_b, plate_a, poly, arrivals, departures, out_path,
            caption="", rejects=None):
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

    # ── WHAT IT LOOKED AT AND TURNED DOWN ───────────────────────────
    #
    # UNDER the rings, so an accepted ball is never buried by a reject
    # drawn over it. These are the components that crossed the
    # brightness threshold and then failed a gate -- too small, too
    # big, not round, or not there in enough frames. A picture with a
    # grey square exactly where the ball is says "the gate is wrong";
    # a picture with no square there at all says "the ball was never
    # bright enough to be a candidate", and those need opposite fixes.
    for r in (rejects or [])[:MAX_DRAWN]:
        cv2.rectangle(pair,
                      (r["x"] + w - 7 * u, r["y"] - 7 * u),
                      (r["x"] + w + 7 * u, r["y"] + 7 * u),
                      (120, 120, 120), u)
        # THE LABEL GOES WHERE IT FITS. A candidate near the right edge
        # of the green had its reason written off the end of the
        # picture, and the reason is the only part of a reject worth
        # drawing -- a grey square on its own says a gate fired without
        # saying which. Measured, not assumed: ask OpenCV how wide the
        # text is and put it on the other side when it would not fit.
        _why = str(r.get("why") or "")
        _sc = 0.34 * u
        (_tw, _th), _ = cv2.getTextSize(
            _why, cv2.FONT_HERSHEY_SIMPLEX, _sc, u)
        _lx = r["x"] + w + 10 * u
        if _lx + _tw > pair.shape[1] - 4 * u:
            _lx = max(w + 4 * u, r["x"] + w - 10 * u - _tw)
        _ly = min(h - 4 * u, max(_th + 4 * u, r["y"] + 4 * u))
        cv2.putText(pair, _why, (_lx, _ly),
                    cv2.FONT_HERSHEY_SIMPLEX, _sc, (150, 150, 150), u)

    # Rings go on the AFTER panel, which is where the thing is.
    #
    # NUMBERED FROM ONE, ALWAYS, when there is more than one. The same
    # picture is what the ball picker is shown, and it is asked which
    # RING is the ball -- so "at rest" on the first and "#2" on the
    # second would be asking about a numbering that does not exist.
    # One candidate keeps the words, because there is nothing to pick
    # between and a bare "1" beside a lone ring says less.
    _numbered = len(arrivals) > 1
    for i, a in enumerate(arrivals):
        c = (60, 170, 250) if i == 0 else (90, 200, 255)
        cv2.circle(pair, (a["x"] + w, a["y"]), 11 * u, c, u + 1)
        cv2.putText(pair, f"{i + 1}" if _numbered else "at rest",
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


def _blobs(diff_mask, diff, mask, rejects=None, counts=None):
    """Ball-shaped components of a thresholded difference.

    EVERY ONE IT SAW, AND THE GATE EACH DIED ON. This used to `continue`
    past a component that failed the area or the roundness test, which
    meant a run that found nothing had exactly one thing to say about
    why -- "nothing new is on the green" -- covering four completely
    different failures that need opposite fixes:

      * nothing was bright enough to cross DIFF_THRESHOLD at all (the
        exposure match is off, or the ball is in shade);
      * something was, but it was one pixel (the ball is at the back
        of the green and MIN_AREA is cutting it);
      * something was, and it was a 60x4 streak (a mowing stripe or a
        shadow edge -- the roundness gate doing its job);
      * something round and ball-sized was found and then failed the
        persistence check further up (it was not there in every frame,
        so it was a person or a flicker).

    `rejects` collects the first three with their numbers; `counts`
    tallies the funnel. Both optional, so the produce path that only
    wants the answer pays nothing for them.
    """
    n, _lbl, stats, cent = cv2.connectedComponentsWithStats(
        cv2.bitwise_and(diff_mask, mask))
    out = []
    if counts is not None:
        counts["components"] = counts.get("components", 0) + max(0, n - 1)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        x, y = int(round(cent[i][0])), int(round(cent[i][1]))
        _str = (int(abs(int(diff[y, x])))
                if (0 <= y < diff.shape[0] and 0 <= x < diff.shape[1])
                else 0)
        long_, short_ = max(bw, bh), max(1, min(bw, bh))
        _row = {"x": x, "y": y, "area": area, "strength": _str,
                "w": bw, "h": bh, "aspect": round(long_ / short_, 2)}
        if area < MIN_AREA:
            _row["why"] = f"{area}px, smaller than {MIN_AREA}px"
        elif area > MAX_AREA:
            _row["why"] = f"{area}px, bigger than {MAX_AREA}px"
        elif long_ / short_ > MAX_ASPECT:
            _row["why"] = (f"{bw}x{bh} — {round(long_ / short_, 1)}:1, "
                           f"not round (limit {MAX_ASPECT}:1)")
        else:
            if counts is not None:
                counts["ball_shaped"] = counts.get("ball_shaped", 0) + 1
            out.append({"x": x, "y": y, "area": area, "strength": _str,
                        "w": bw, "h": bh,
                        "aspect": round(long_ / short_, 2)})
            continue
        if counts is not None:
            _k = ("too_small" if area < MIN_AREA
                  else "too_big" if area > MAX_AREA else "not_round")
            counts[_k] = counts.get(_k, 0) + 1
        if rejects is not None:
            rejects.append(_row)
    return out


# HOW MANY REJECTED CANDIDATES COME BACK. A green in low sun can throw
# hundreds of one-pixel specks, and a list that long is not a report,
# it is a second problem. The ones worth reading are the BIGGEST -- a
# near miss is a component that nearly passed -- so they are sorted and
# cut here rather than at the point they are found.
MAX_REJECTS_REPORTED = 24


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
                   debug_path=None, marks_path=None, marks=None,
                   stages_dir=None, stages_stem=None) -> dict:
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

        # THE FUNNEL, COUNTED AT EVERY STAGE. "0 arrivals" is one
        # sentence covering four different failures with four opposite
        # fixes; which stage the count fell to nothing at is the thing
        # that says which one happened. See `_blobs`.
        _counts: dict = {}
        _rejects: list = []
        _npos = int((pos > 0).sum())
        _nneg = int((neg > 0).sum())
        _nmask = int((mask > 0).sum()) if mask is not None else 0
        arrivals = _blobs(pos, diff, mask, _rejects, _counts)
        departures = _blobs(neg, diff, mask)

        for blob in arrivals:
            blob["persist"] = round(
                _persistence(frames_a, blob, matched_b), 2)
        # AND THE LAST GATE RECORDS ITS DEAD TOO. A ball-shaped,
        # ball-sized, ball-bright thing that fails persistence is the
        # single most interesting rejection this whole file produces:
        # it is either a person standing still for part of the window
        # (working as intended) or the ball itself in shade for a few
        # frames (PERSIST_FRAC too high), and nothing in the old output
        # could tell those apart.
        for blob in arrivals:
            if blob["persist"] < PERSIST_FRAC:
                _rejects.append(dict(blob, why=(
                    "in %d%% of the after frames, needs %d%%"
                    % (round(blob["persist"] * 100),
                       round(PERSIST_FRAC * 100)))))
                _counts["not_persistent"] = _counts.get(
                    "not_persistent", 0) + 1
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
        # BIGGEST FIRST, because a near miss is a component that nearly
        # passed, and a green in low sun throws hundreds of one-pixel
        # specks that are not worth a row each.
        _rejects.sort(key=lambda z: (-z["area"], -z["strength"]))
        _n_rejects = len(_rejects)
        _rejects = _rejects[:MAX_REJECTS_REPORTED]
        _counts["kept"] = len(arrivals)
        _counts["bright_px"] = _npos
        _counts["dim_px"] = _nneg
        _counts["searched_px"] = _nmask

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
            # WHICH STAGE IT FELL AT, not just that it fell. Four quite
            # different failures used to share this one sentence, and
            # they need opposite fixes: a threshold that nothing
            # crossed is an exposure problem, a candidate that was
            # ball-shaped and failed persistence is a gate set too
            # tight, and only the case where nothing was found at any
            # stage is actually "the shot missed".
            if not _counts.get("components"):
                notes.append(
                    "Nothing on the green was %d grey levels brighter "
                    "than before, anywhere inside the outline. That is "
                    "not a ball that was rejected — it is a ball that "
                    "was never a candidate. Either the shot missed the "
                    "green, the ball is in shade, or the two plates are "
                    "so far apart that the exposure match could not "
                    "bring them together."
                    % DIFF_THRESHOLD)
            elif not _counts.get("ball_shaped"):
                notes.append(
                    "%d thing(s) were bright enough, and none of them "
                    "were ball-shaped — %d too small, %d too big, %d "
                    "not round. A ball at the back of a green can be "
                    "under %dpx; mowing stripes and shadow edges are "
                    "what the roundness test is there to remove."
                    % (_counts.get("components", 0),
                       _counts.get("too_small", 0),
                       _counts.get("too_big", 0),
                       _counts.get("not_round", 0), MIN_AREA))
            elif _counts.get("not_persistent"):
                notes.append(
                    "%d ball-shaped candidate(s) were found and then "
                    "failed the persistence check — they were not in "
                    "at least %d%% of the after frames. That is either "
                    "a person who stood still for part of the window, "
                    "or the ball itself passing through shade. The "
                    "turned-down list below has the percentage each "
                    "one reached."
                    % (_counts.get("not_persistent", 0),
                       round(PERSIST_FRAC * 100)))
            else:
                notes.append(
                    "Nothing new is on the green. Either the shot "
                    "missed it, or the ball came to rest outside the "
                    "outline.")

        detect_sec = time.monotonic() - _t
        _t = time.monotonic()
        # ONE PICTURE OF THE GREEN with named points on it, when the
        # caller has others to show beside the resting place -- the
        # pitch mark, typically, which only means anything next to it.
        #
        # Drawn here rather than by handing the plate back, because the
        # plate is a numpy array and these results are returned as JSON
        # by two endpoints. A caller that forgot to strip it would get
        # a 500 rather than a picture.
        marked = None
        if marks_path is not None:
            try:
                _m = list(marks or [])
                if arrivals:
                    _m.append({"xy": [arrivals[0]["x"], arrivals[0]["y"]],
                               "label": "at rest",
                               "colour": (60, 170, 250)})
                if _m:
                    marked = str(render_marks(
                        plate_a, poly, _m, Path(marks_path),
                        f"plates {b / fps:.1f}s and {a / fps:.1f}s"))
            except Exception as exc:  # noqa: BLE001
                log.warning("rest-spot mark render failed: %s", exc)
        # ── EVERY PICTURE IT LOOKED AT, IN ORDER ────────────────────
        #
        # Four JPEG encodes of frames already in memory. The caller
        # asks for them (the operator's button does; the produce path
        # does not), so a run that only wants the answer is unchanged.
        stages = []
        if stages_dir is not None:
            stages = _stage_images(matched_b, plate_a, diff, pos, mask,
                                   poly, arrivals, _rejects, stages_dir,
                                   stages_stem or "greenrest")
        image = None
        if debug_path is not None:
            try:
                cap_txt = (
                    f"{len(arrivals)} arrival(s), {len(departures)} "
                    f"departure(s) - plates {b / fps:.1f}s and {a / fps:.1f}s")
                image = str(_render(matched_b, plate_a, poly, arrivals,
                                    departures, Path(debug_path), cap_txt,
                                    rejects=_rejects))
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
            "stages": stages,
            "marks_path": marked,
            "best": arrivals[0] if arrivals else None,
            "arrivals": arrivals,
            "departures": departures,
            # ── THE WORK, NOT ONLY THE ANSWER ────────────────────────
            #
            # `steps` is the funnel: how many pixels were searched, how
            # many were bright enough, how many of those grouped into
            # components, and how many survived each gate. `rejected`
            # is the biggest two dozen that did not, each with the gate
            # it died on. `dials` is every constant the gates were
            # measured against, so the operator reading a rejection can
            # see which number to change without opening the file.
            "steps": _counts,
            "n_rejected": _n_rejects,
            "rejected": _rejects,
            "dials": {
                "diff_threshold": DIFF_THRESHOLD,
                "min_area": MIN_AREA,
                "max_area": MAX_AREA,
                "max_aspect": MAX_ASPECT,
                "persist_frac": PERSIST_FRAC,
                "persist_rel": PERSIST_REL,
                "persist_samples": PERSIST_SAMPLES,
                "plate_frames": PLATE_FRAMES,
                "plate_span_sec": PLATE_SPAN_SEC,
            },
            "plates": {
                "before_sec": round(b / fps, 2),
                "after_sec": round(a / fps, 2),
                "span_sec": PLATE_SPAN_SEC,
                "frames_each": len(frames_b),
                "gap_sec": round((a - b) / fps, 1),
            },
            "before_frame": b,
            "after_frame": a,
            "frame_size": [int(plate_a.shape[1]), int(plate_a.shape[0])],
            "fps": round(fps, 3),
            "notes": notes,
        }
    finally:
        cap.release()
