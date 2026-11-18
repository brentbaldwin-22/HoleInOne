"""WHAT COLOUR SHIRT IS THAT, ROUGHLY.

The registration selfie and the swing are photographed by different
cameras, minutes or hours apart, under whatever the sky was doing. A
black polo indoors and the same polo in full sun do not share an RGB
value, do not share a hue, and are not within any sane deltaE of each
other. So nothing here tries to match a COLOUR.

What survives that gap is the FAMILY: dark stays dark, white stays
light, a red shirt stays red. Eight families, matched loosely, is a
blunt instrument -- and blunt is the point, because the cost of being
too clever is hiding the clip the operator is looking for.

Pure: takes lists of (r, g, b). The caller samples the torso and
downsamples; a few hundred pixels decides this as well as a million.
No numpy, so it can be tested without the vision stack installed.
"""
from __future__ import annotations

from collections import Counter

# Below this lightness nothing has a reliable hue: a navy polo in shade
# and a black one are the same handful of dark pixels.
#
# MEASURED, NOT PICKED. 0.22 was the first guess and it fell between
# the two readings it most needed to keep together: a black polo is
# v=0.125 indoors and v=0.235 in full sun, so the same shirt came back
# "dark" at registration and "grey" on the tee. Navy in shade (v=0.267)
# landed on the wrong side too. 0.30 clears both and still leaves grey
# marl (v=0.553) and white in shadow (v=0.604) well clear.
DARK_V = 0.30
# Above this, with little colour in it, it is white / cream / silver.
LIGHT_V = 0.78
# Under this saturation the hue is noise rather than colour.
GREY_S = 0.18

# Hue buckets, in degrees. Wide, because hue shifts with the light and
# a narrow bucket is how a red shirt becomes "orange" at sunset.
_HUES = (
    (345, 360, "red"), (0, 15, "red"),
    (15, 45, "orange"),
    (45, 70, "yellow"),
    (70, 165, "green"),
    (165, 195, "cyan"),
    (195, 255, "blue"),
    (255, 285, "purple"),
    (285, 345, "pink"),
)

# FAMILIES THAT ARE NOT REALLY DIFFERENT. Each pair is a confusion the
# cameras actually produce, so treating them as a match costs a few
# extra tiles and treating them as a mismatch costs the whole shot:
#
#   dark/blue    navy. Reads blue in sun, dark in shade. The single
#                commonest golf shirt and the single worst case.
#   light/grey   white in shadow is grey; silver in sun is white.
#   red/pink     a faded red, or a red under a warm low sun.
#   red/orange   same, the other way.
#   blue/cyan    a mid blue against a bright sky.
#   dark/purple  a very dark maroon or aubergine.
_ALSO = {
    frozenset({"dark", "blue"}),
    frozenset({"light", "grey"}),
    frozenset({"red", "pink"}),
    frozenset({"red", "orange"}),
    frozenset({"blue", "cyan"}),
    frozenset({"dark", "purple"}),
}


def to_hsv(r, g, b):
    """(hue 0-360, saturation 0-1, value 0-1) from 0-255 RGB."""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    hi, lo = max(r, g, b), min(r, g, b)
    v = hi
    span = hi - lo
    s = 0.0 if hi <= 0 else span / hi
    if span <= 0:
        h = 0.0
    elif hi == r:
        h = (60 * ((g - b) / span)) % 360
    elif hi == g:
        h = 60 * ((b - r) / span) + 120
    else:
        h = 60 * ((r - g) / span) + 240
    return h % 360, s, v


def family(r, g, b) -> str:
    """One of: dark, light, grey, red, orange, yellow, green, cyan,
    blue, purple, pink."""
    h, s, v = to_hsv(r, g, b)
    # Lightness first. A hue read off a near-black or near-white pixel
    # is arithmetic, not observation.
    if v < DARK_V:
        return "dark"
    if s < GREY_S:
        return "light" if v >= LIGHT_V else "grey"
    # PINK IS A RED THAT GAVE UP. Salmon sits at hue 353 -- squarely in
    # the red bucket -- and is told apart from red only by being light
    # and washed out (v=0.89, s=0.43, against a true red's v=0.70,
    # s=0.77). Without this a pink polo and a red one are the same
    # family, which is a false match rather than a lost clip, but it is
    # free to get right.
    if (h >= 330 or h < 20) and v >= 0.75 and s < 0.55:
        return "pink"
    for lo, hi, name in _HUES:
        if lo <= h < hi:
            return name
    return "grey"


def dominant(pixels) -> tuple[str | None, float]:
    """The family most of these pixels belong to, and how much of them.

    The confidence is the plain share of the winning family, and it is
    the number that decides whether to act: a torso sample that is half
    shirt and half fairway splits, scores low, and must not be allowed
    to hide anything. Returns (None, 0.0) for an empty sample.
    """
    if not pixels:
        return None, 0.0
    counts = Counter(family(*p) for p in pixels)
    name, n = counts.most_common(1)[0]
    return name, n / float(len(pixels))


def compatible(a: str | None, b: str | None) -> bool:
    """Could these two be the same shirt?

    UNKNOWN MATCHES EVERYTHING. A missing reading is not evidence of a
    difference, and the caller uses this to DROP clips -- so every way
    of failing to know has to come back True.
    """
    if not a or not b:
        return True
    if a == b:
        return True
    return frozenset({a, b}) in _ALSO
