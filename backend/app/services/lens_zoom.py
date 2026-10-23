"""WHERE THE ZOOM IS, on a lens that will not say.

THE CAMERA CANNOT BE ASKED. The XNV-6080R's own attributes.cgi declares
ZoomAdjust and FocusAdjust True and Absolute/Query False: the lens takes
RELATIVE nudges, in three step sizes, and has no endpoint that reports
where it currently sits. That is the whole reason the admin card has
said "these are nudges, the camera cannot report its position" since the
controls were built, and it is why there was no number to show.

So the position here is COUNTED, not read. Every nudge the backend
queues is added to a running total, and the total is meaningful once it
has been tied to something physical: the lens has hard stops at each end
of its travel, and an operator who drives to one and says "this is the
wide end" has given us an origin. Drive to the other end, say so, and
the span between them is the travel — after which any count in between
is a position on a known scale, and that scale can be labelled in the
magnification the lens is actually built to deliver.

WHAT THIS IS NOT: a reading. It is our record of what we have sent, so
it drifts — a nudge the agent never applied (camera asleep, network
gone, a 604 refused by the lens) is counted here and not there, and
somebody at the pole turning the ring through the camera's own web UI is
invisible to it entirely. The ends are the cure: marking one re-zeros
the count against the only thing on this lens that is unambiguous.

The magnification is likewise an ESTIMATE. Step count is linear in motor
steps, not in focal length, so the figure is right at the ends and
approximate in the middle. It is shown as "about", because 2.4x that
means 2.2x is useful and 2.4x that pretends to be measured is not.

If a camera ever does report a position — another model, a firmware that
grows the endpoint — the agent's reading lands in `reported` and the
card prefers it over all of the above.
"""
from __future__ import annotations

# The only magnitudes the lens accepts, biggest first, because a move is
# planned by taking as many coarse steps as fit before the fine ones.
STEPS = (100, 10, 1)

# A plan longer than this is refused rather than queued. A full-travel
# drag should be a handful of commands; a hundred means the travel was
# mis-measured, and the lens should stall at a stop rather than take
# ten minutes of SUNAPI calls to get there.
MAX_PLAN_COMMANDS = 24

# THE OPTICAL RANGE, as the maker states it, keyed by a substring of the
# model the operator wrote on the camera row. Only used for the label:
# a lens we do not know the range of still gets a slider and a position,
# just expressed as travel rather than as a magnification, because
# inventing "3x" for an unknown lens is worse than admitting ignorance.
ZOOM_RANGE_X = {
    # 2.8-12 mm varifocal.
    "xnv-6080r": 12.0 / 2.8,
    "xnv-6080": 12.0 / 2.8,
}


def range_x(model: str | None) -> float | None:
    """How many times its widest this lens can zoom, or None if unknown."""
    m = (model or "").strip().lower()
    if not m:
        return None
    for key, val in ZOOM_RANGE_X.items():
        if key in m:
            return val
    return None


def state(value) -> dict:
    """The stored lens position, normalised. Safe on None and on junk."""
    v = value if isinstance(value, dict) else {}
    try:
        pos = int(v.get("pos") or 0)
    except (TypeError, ValueError):
        pos = 0
    try:
        travel = int(v.get("travel") or 0)
    except (TypeError, ValueError):
        travel = 0
    reported = v.get("reported")
    return {
        "pos": pos,
        "travel": travel if travel > 0 else 0,
        "wide_at": v.get("wide_at") or None,
        "tele_at": v.get("tele_at") or None,
        "reported": reported if isinstance(reported, dict) else None,
    }


def calibrated(value) -> bool:
    """Is there a scale to put a position on?"""
    s = state(value)
    return bool(s["wide_at"] and s["travel"] > 0)


def nudged(value, amount: int) -> dict:
    """The stored position after a zoom nudge of `amount` fine units.

    Clamped at the ends ONCE THEY ARE KNOWN, because that is where the
    lens itself stops: counting past a stop the motor is already sitting
    against is how a count stops matching a lens.
    """
    s = state(value)
    pos = s["pos"] + int(amount)
    if s["wide_at"]:
        pos = max(0, pos)
        if s["travel"] > 0:
            pos = min(s["travel"], pos)
    s["pos"] = pos
    return s


def mark_end(value, end: str, when: str) -> dict:
    """Record that the lens is now sitting against one of its stops.

    Wide first: it is the origin, so marking it starts a fresh scale and
    any span measured against an older one is discarded rather than
    quietly reused. Tele then closes the scale at whatever has been
    counted since.
    """
    s = state(value)
    e = (end or "").strip().lower()
    if e == "wide":
        s["pos"] = 0
        s["wide_at"] = when
        # A span measured from a different origin is not this one's.
        s["travel"] = 0
        s["tele_at"] = None
        return s
    if e == "tele":
        if not s["wide_at"]:
            raise ValueError(
                "mark the wide end first — the travel is measured from it")
        if s["pos"] <= 0:
            raise ValueError(
                "no zoom has been driven since the wide end was marked, so "
                "there is no travel to record")
        s["travel"] = s["pos"]
        s["tele_at"] = when
        return s
    raise ValueError("end must be 'wide' or 'tele'")


def plan(delta: int) -> list[tuple[int, int]]:
    """A move of `delta` fine units, as (step size, repeat) commands.

    Greedy over the three legal sizes, which is also the fewest commands
    — each one is a separate round trip to the camera from a Pi on a
    cellular link, so the difference between 4 and 40 is the difference
    between a drag that lands and one that is still arriving.
    """
    n = abs(int(delta))
    if not n:
        return []
    sign = 1 if delta > 0 else -1
    out: list[tuple[int, int]] = []
    for step in STEPS:
        count, n = divmod(n, step)
        if count:
            out.append((sign * step, count))
    return out


def magnification(value, model: str | None) -> float | None:
    """About how many times its widest the lens is, or None.

    None whenever the honest answer is "no idea": no scale yet, or a
    lens whose optical range nobody has written down.
    """
    s = state(value)
    rng = range_x(model)
    if not rng or not calibrated(value):
        return None
    frac = min(1.0, max(0.0, s["pos"] / float(s["travel"])))
    return round(1.0 + frac * (rng - 1.0), 1)


def describe(value, model: str | None) -> dict:
    """The lens position as the admin card wants it."""
    s = state(value)
    return {
        "pos": s["pos"],
        "travel": s["travel"],
        "calibrated": calibrated(value),
        "wide_at": s["wide_at"],
        "tele_at": s["tele_at"],
        # How far along its travel, 0..1, for the slider. Null until
        # there is a scale to be along.
        "fraction": (round(s["pos"] / float(s["travel"]), 4)
                     if calibrated(value) else None),
        "x": magnification(value, model),
        "range_x": (round(r, 1) if (r := range_x(model)) else None),
        # Anything the camera itself said, which beats all of the above.
        "reported": s["reported"],
    }
