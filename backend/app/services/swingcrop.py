"""WHERE TO POINT THE CAMERA: one copy of the crop rule.

THIS FILE EXISTS BECAUSE THERE WERE TWO. `swinganalysis.analyse`
serves /verify-swing, which is the live rig path; `tracer.
swing_heat_check` serves the Test cut page, which is where the
operator checks that path. They computed the same crop two ways, and
the divergence was invisible in exactly the place it mattered most --
a fix to one improved the rig while the page used to verify the rig
stayed broken, and the same bug got reported twice.

They were not even the same rule. Both pad a golfer by 0.69 of a body
width, 0.08 of a height below the feet and 0.10 above the head, but
one wrote the numbers as named constants and the other as literals,
and the HEAD LINE differed outright:

    tracer           any person-shaped blob whose CENTRE falls inside
                     the crop's x-span is in the picture, so their
                     head has to be in it too
    swinganalysis    any person box whose LEFT EDGE is within 40 px
                     of the golfer's box

Tracer's is kept. It is scale-free, where 40 px means one thing on a
golfer 90 px tall and another on one 400 px tall, and it asks the
question the crop actually needs answered.

COORDINATES. Everything here is in CROP pixels -- the crop being the
region the heat was measured in -- except `zones`, which are frame
pixels as the operator drew them, and are shifted by the caller's
offset where they are read. Callers that want frame pixels add their
own offset to what comes back; both do, and differently, which is why
that is not done here.

THE TWO RECTANGLES ARE DIFFERENT ON PURPOSE. The heat picture runs to
the TOP of the frame, because a club at the top of a backswing and a
ball leaving both go up out of any box drawn round a stance, and the
heat is a drawing of where motion was -- that sky is where the club
went. The frames stop just above the heads: they are ordinary video,
the same sky is just sky, and it costs the golfers their size in a
640 px tile.
"""
from __future__ import annotations

# The pads that shape the crop round one golfer. Named rather than
# buried: a bit over two thirds of a body width either side, a twelfth
# of a body height below the feet, a tenth above the head. Measured
# for swing_heat_check, 8 Nov.
SIDE_PAD_FRAC = 0.69
BELOW_FEET_FRAC = 0.08
ABOVE_HEAD_FRAC = 0.10

# THE SMALLEST CROP WORTH JUDGING, in pixels. Below this there is
# nothing for a model to look at and nothing for a detector to find,
# and at zero `cv2.imwrite` raises rather than returning False.
MIN_CROP_PX = 8

# How much of the zones' own width to add either side of a zone crop.
ZONE_PAD_FRAC = 0.25


def usable(x0, y0, x1, y1, W, H):
    """A rect that can actually be cropped with, or None.

    THE INTERSECTION WITH THE PICTURE IS THE WHOLE TEST, and it is the
    clamp as well. Judging a rect on its own size instead let a ten
    pixel box sitting entirely off the corner survive -- ten is more
    than the minimum -- and it then clamped to an eight pixel sliver
    of the corner, which crops without raising and shows nobody
    anything.

    RETURNS None RATHER THAN THE WHOLE PICTURE, and the difference
    cost a day of audio triggers. Falling back to the frame stopped a
    crash and quietly changed the ANSWER: the judge was handed a whole
    640x480 frame while the result still said it was a golfer crop,
    rated it not-a-swing -- correctly, there is no swing to see at
    that scale -- and `verify_drops` on the rig deleted the clip.
    Callers decide what None means for them; for a golfer it means
    "could not tell", which is what that input did before any of this
    existed.
    """
    ix0, iy0 = max(0, int(x0)), max(0, int(y0))
    ix1, iy1 = min(int(W), int(x1)), min(int(H), int(y1))
    if ix1 - ix0 < MIN_CROP_PX or iy1 - iy0 < MIN_CROP_PX:
        return None
    return (ix0, iy0, ix1, iy1)


def head_line(heads, x0, x1, fallback_y):
    """The y to stop the frames crop at, just above every head in it.

    ABOVE EVERY HEAD IN THE CROP, not just the picked golfer's. The
    blob is MOTION, and on a swing the moving part can be the arms,
    the club and the legs with the torsos standing still -- so the
    picked blob's top is sometimes mid-thigh, and taking it cut the
    golfers' heads off.

    CENTRE INSIDE THE SPAN is the test. Anybody whose middle is in the
    picture is IN the picture and their head has to be too. It was
    once a flat 40 px from the golfer's box in one of the two copies,
    which means one thing on a golfer 90 px tall and another on one
    400 px tall.

    `heads` are dicts with x, y, w in crop pixels. Returns
    `fallback_y` when none of them is in the span.
    """
    def _in(h):
        # A HEAD WE CANNOT PLACE COUNTS AS INSIDE. Erring upward
        # includes some sky; erring downward cuts somebody's head off,
        # and the picture's whole job is showing a club move above
        # one. So a dict without x/w is kept, not dropped.
        if "x" not in h or "w" not in h:
            return True
        try:
            mid = float(h["x"]) + float(h["w"]) / 2.0
        except (TypeError, ValueError):
            return True
        return float(x0) <= mid <= float(x1)

    inside = []
    for h in (heads or []):
        if not isinstance(h, dict) or "y" not in h:
            continue
        if _in(h):
            try:
                inside.append(float(h["y"]))
            except (TypeError, ValueError):
                continue
    if not inside:
        return float(fallback_y)
    return min(inside)


def crop_rect(golfer, floor_y, heads, bounds):
    """(heat_rect, frames_rect) around ONE golfer, in crop px.

    `golfer` is {x, y, w, h}; `floor_y` the bottom edge of the zone
    they are standing in, or None.

    THE FEET ARE THE ZONE'S BOTTOM EDGE, NOT THE BLOB'S. The blob is
    motion: on a golfer whose legs barely move it stops around the
    waist, and the first version of this cropped exactly there,
    cutting the player off at the belt in both pictures. The zone is
    drawn round feet and does not move. Still max()'d with the blob,
    so a follow-through reaching below the zone is not clipped.

    EITHER RECT MAY BE None -- see `usable`. A caller must not judge a
    picture it could not crop.
    """
    W, H = bounds
    pad = max(12, int(float(golfer["w"]) * SIDE_PAD_FRAC))
    x0 = float(golfer["x"]) - pad
    x1 = float(golfer["x"]) + float(golfer["w"]) + pad
    ground = float(golfer["y"]) + float(golfer["h"])
    if floor_y is not None:
        ground = max(ground, float(floor_y))
    bottom = ground + max(6, int(float(golfer["h"]) * BELOW_FEET_FRAC))
    top = head_line(heads, x0, x1, golfer["y"])
    top = top - max(8, int(float(golfer["h"]) * ABOVE_HEAD_FRAC))
    return (usable(x0, 0, x1, bottom, W, H),
            usable(x0, top, x1, bottom, W, H))


def zone_rect(zones, ox, oy, W, H, heads=None):
    """(heat_rect, frames_rect) around the trigger zones, or (None, None).

    THE LAST RUNG BEFORE THE WHOLE FRAME. A zone is where somebody has
    to be standing for this camera to fire at all, so even with nobody
    picked it is a far better guess at where the swing is than the
    whole picture.

    NOT a synthetic golfer handed to `crop_rect`. That measures head
    room against the box it is given, and a zone is drawn around a
    patch of TURF a few tens of pixels tall; treating it as a person
    crops the golfer off at the knees. A zone says where the FEET are
    and nothing about how tall anybody is.

    So the width comes from the zones and the head line comes from
    whoever is actually standing in them. With no `heads` to ask, the
    frames rect runs to the top of the picture like the heat -- safe,
    and mostly sky, which is why the heads are worth passing.
    """
    if not zones:
        return (None, None)
    try:
        x0 = min(float(z[0]) for z in zones) - ox
        x1 = max(float(z[0]) + float(z[2]) for z in zones) - ox
        y1 = max(float(z[1]) + float(z[3]) for z in zones) - oy
    except (TypeError, ValueError, IndexError):
        return (None, None)
    pad = max(24.0, (x1 - x0) * ZONE_PAD_FRAC)
    x0, x1 = x0 - pad, x1 + pad
    bottom = y1 + pad
    top = 0.0
    if heads:
        _t = head_line(heads, x0, x1, 0.0)
        # A twelfth of what the crop covers, rather than of a body
        # height nobody measured here.
        top = max(0.0, _t - max(8, int((bottom - _t) * BELOW_FEET_FRAC)))
    return (usable(x0, 0, x1, bottom, W, H),
            usable(x0, top, x1, bottom, W, H))


def zoned_person(considered):
    """A person the picker saw IN a zone but did not pick, or None.

    NEVER SOMEBODY OUTSIDE THE ZONES. An earlier version took the
    person NEAREST a zone, and "nearest" with no limit means anyone
    anywhere: on a clip where the tee was empty it zoomed hard onto a
    man walking past a golf cart on the far side of the hole and
    handed the judge a crisp picture of somebody not swinging. That is
    worse than the whole frame, not better -- the frame at least looks
    like a frame, while this looks like an answer.

    THE PICKER HAS ALREADY ANSWERED THIS, in `considered`: every
    person it looked at, with `zone` saying whether their feet were in
    one. Asking it rather than re-deriving containment is the point --
    two notions of "in a zone" that disagree is a camera cropping
    where its own rule says nobody is standing.

    Lowest feet first, the same ordering the picker uses.
    """
    best = None
    for c in considered or []:
        if not isinstance(c, dict) or not c.get("zone"):
            continue
        if not c.get("box") or not c.get("feet"):
            continue
        if best is None or c["feet"][1] > best["feet"][1]:
            best = c
    return best
