"""Which of a golfer's shots make their highlight reel, and in what order.

CLOSEST TO THE PIN, which is the only thing here that is measured rather
than guessed. Carry used to rank these once and was dropped for exactly
that reason -- ranking on a number nobody could measure put arbitrary
shots at the top of a profile. Distance from the pin comes off the green
camera, and a golfer knows whether 4ft is better than 30ft without being
told.

A holed shot leads regardless. It is zero feet by definition, but it is
also the thing the whole product exists for, so it is ranked above the
arithmetic rather than inside it.

The reel is ten, not three, and plays back to back: ten is about as many
as anyone watches in a sitting, and the tenth-best shot of a season is
still a good shot.

A golfer whose clips carry no distance at all -- nothing measured, or
every green camera down that day -- still gets a reel. Recency is a poor
ranking and an empty profile is worse, so the fallback stands, but only
when there is nothing measured to rank.
"""

from __future__ import annotations

REEL_SIZE = 10


def rank(clips: list) -> list:
    """Best first. `clips` are (clip, course) pairs; the return is the same
    pairs with the tag to print under each.

    Holed shots first, then ascending distance from the pin. Only when
    NOTHING carries a distance does it fall back to most recent, so one
    measured shot is never buried under nine unmeasured ones.
    """
    holed = [(c, co) for (c, co) in clips if c.ball_in_cup]
    measured = [
        (c, co) for (c, co) in clips
        if not c.ball_in_cup and c.distance_from_pin_feet is not None
    ]
    measured.sort(key=lambda pair: pair[0].distance_from_pin_feet)

    out = [(c, co, "ACE") for (c, co) in holed]
    out += [
        (c, co, f"{int(c.distance_from_pin_feet)} ft from the pin")
        for (c, co) in measured
    ]

    if not out:
        # Nothing measured anywhere. Better than an empty profile, and
        # explicitly unranked rather than pretending recency is merit.
        rest = sorted(clips, key=lambda pair: pair[0].captured_at, reverse=True)
        out = [(c, co, None) for (c, co) in rest]

    return out[:REEL_SIZE]
