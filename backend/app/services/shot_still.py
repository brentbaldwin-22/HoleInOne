"""The frozen frame behind each tile on the Player ID grid.

A WIDE SHOT OF A PAR-3 IS USELESS AT TILE SIZE. The operator is being
asked "which of these is Alex" while looking at eleven postage stamps of
the same stretch of fairway, with the golfer perhaps sixty pixels tall
somewhere in the middle of it. Shrinking a 1920-wide frame into a 132px
column throws away the only thing they are trying to read -- what the
person is wearing.

So the tile is cropped to the same 9:16 window the vertical render uses,
around the same anchor: `_tracer_start_x`, which finds where the tracer
begins by looking at the pixels. The tracer starts at the ball at impact,
and the ball at impact is at the golfer's feet, so that fraction is the
golfer's position in the frame -- and because it is read off the burned-in
stroke rather than metadata, it works on clips whose ball track never
survived into the render. Same function, same clamp, same aspect: a tile
and its 9:16 should not disagree about where the golfer was standing.

It is a STILL, taken at the moment the tracer starts. Eleven autoplaying
videos on one page is eleven decoders running to show the operator a
second of sky; the frame they actually need is the one at impact, and a
frozen frame can be looked at for as long as it takes to decide.

Only the geometry lives here, because only the geometry can be tested
without a video file. The grab itself is in the router, next to the
tracer scan it depends on.
"""

from __future__ import annotations

# The vertical render clamps its focus to this band, so a crop never
# runs off the edge of the frame and leave half the window empty. Matched
# here on purpose: the tile and the 9:16 must frame the same shot.
FOCUS_MIN = 0.15
FOCUS_MAX = 0.85
FOCUS_CENTRE = 0.5

# 9:16, the shape of the vertical deliverable and of the tile.
ASPECT_W = 9
ASPECT_H = 16


def clamp_focus(x) -> float:
    """A tracer reading as a usable focus fraction.

    None, a NaN out of a failed scan, or anything unparseable all land on
    dead centre -- which is what the vertical render falls back to, and
    is wrong in the same way rather than wrong in a new one.
    """
    try:
        if x is None:
            return FOCUS_CENTRE
        v = float(x)
    except (TypeError, ValueError):
        return FOCUS_CENTRE
    if v != v:  # NaN
        return FOCUS_CENTRE
    return max(FOCUS_MIN, min(FOCUS_MAX, v))


# How much tighter than the full frame to cut. A 9:16 slice of 1080p is
# still 1080 tall, so a golfer a hundred pixels high stays a tenth of the
# tile -- a zoom in name only. This takes a window a fraction of the
# frame's height and blows it up, which is what makes a tile readable.
ZOOM = 2.2

# Where the golfer's feet sit inside the cut. The tracer's foot is the
# ball, at the golfer's feet, so the window hangs mostly ABOVE it: centre
# on the feet and the crop is half grass with the head chopped off.
FEET_FROM_BOTTOM = 0.18


def crop_box(frame_w: int, frame_h: int, focus_x: float, focus_y=None) -> tuple:
    """The window to cut, as (x, y, w, h) in pixels.

    9:16, like the vertical deliverable. With a `focus_y` -- the height of
    the tracer's foot, which is the ball, which is where the golfer is
    standing -- it cuts a ZOOMed window around that point and hangs it
    above the feet. Without one there is no vertical anchor worth
    trusting, so it falls back to the render's own framing: full height,
    a 9:16 slice of the width. Guessing a height would put the crop on
    the sky as often as on the golfer.

    Either way the window keeps its shape and slides back inside the
    frame rather than being squeezed: a crop that misses slightly still
    shows a person, where one that changes aspect shows a stretched one.
    """
    w = int(frame_w)
    h = int(frame_h)
    if w <= 0 or h <= 0:
        raise ValueError("frame has no size")

    fx = clamp_focus(focus_x)

    if focus_y is None:
        ch = h
        cw = int(round(ch * ASPECT_W / ASPECT_H))
        if cw >= w:
            return (0, 0, w, h)
        x = int(round(fx * w - cw / 2))
        return (max(0, min(w - cw, x)), 0, cw, ch)

    fy = clamp_focus(focus_y)
    ch = max(1, int(round(h / ZOOM)))
    cw = max(1, int(round(ch * ASPECT_W / ASPECT_H)))
    # A frame too small to take the zoomed window: fall back rather than
    # cut something larger than the picture.
    if cw >= w or ch >= h:
        return crop_box(w, h, focus_x, None)

    x = int(round(fx * w - cw / 2))
    # The feet sit FEET_FROM_BOTTOM up from the bottom edge, so the body
    # and the swing are in the frame and the ground is not.
    y = int(round(fy * h - ch * (1.0 - FEET_FROM_BOTTOM)))
    x = max(0, min(w - cw, x))
    y = max(0, min(h - ch, y))
    return (x, y, cw, ch)


# How far BEFORE the tracer starts to take the frame.
#
# Frame zero was the obvious choice and it is wrong often enough to show:
# a clip can open before the golfer has walked onto the tee, and the tile
# comes back as an empty box of grass. The tracer's start is the ball
# leaving, so a second or so earlier is the golfer stood over it -- in
# frame by definition, because they are about to hit the shot that made
# the tracer, and still, because they have not swung yet.
ADDRESS_LEAD_SEC = 1.2


def frame_index(fps: float, tracer_t, total_frames: int) -> int:
    """Which frame of the clip to freeze.

    With a tracer: ADDRESS_LEAD_SEC before it starts. Without one there
    is no moment worth naming, so take the middle of the clip -- a golfer
    is more likely to be somewhere in the middle of their own clip than
    in its opening frame, which is where the empty tiles came from.
    """
    try:
        n = int(total_frames)
    except (TypeError, ValueError):
        n = 0
    if n <= 1:
        return 0
    try:
        f = float(fps)
    except (TypeError, ValueError):
        f = 0.0
    if f <= 0:
        f = 30.0
    if tracer_t is None:
        return n // 2
    try:
        t = float(tracer_t)
    except (TypeError, ValueError):
        return n // 2
    if t != t:  # NaN
        return n // 2
    idx = int(round((t - ADDRESS_LEAD_SEC) * f))
    return max(0, min(n - 1, idx))


def still_name(stem: str) -> str:
    """Cache filename. One per source file, beside the clip it came from."""
    return f"{stem}_shot_still3.jpg"
