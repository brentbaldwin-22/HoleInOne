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


def crop_box(frame_w: int, frame_h: int, focus_x: float) -> tuple:
    """The 9:16 window to cut, as (x, y, w, h) in pixels.

    Full frame height, a 9:16 slice of the width, centred on the golfer
    and then slid back inside the frame if that would hang over an edge.
    Sliding rather than squeezing: a crop that keeps its shape and misses
    slightly still shows a person, where one that changes aspect to stay
    centred shows a stretched one.
    """
    w = int(frame_w)
    h = int(frame_h)
    if w <= 0 or h <= 0:
        raise ValueError("frame has no size")
    cw = int(round(h * ASPECT_W / ASPECT_H))
    if cw >= w:
        # Already as tall-and-narrow as we want (an existing vertical, or
        # a very square source): take the whole thing rather than
        # inventing a crop.
        return (0, 0, w, h)
    cx = clamp_focus(focus_x) * w
    x = int(round(cx - cw / 2))
    x = max(0, min(w - cw, x))
    return (x, 0, cw, h)


def still_name(stem: str) -> str:
    """Cache filename. One per source file, beside the clip it came from."""
    return f"{stem}_shot_still.jpg"
