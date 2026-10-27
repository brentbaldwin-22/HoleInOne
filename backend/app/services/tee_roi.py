"""THE TRIGGER ZONES: where a person has to be for the tee camera to fire.

COPIED VERBATIM TO pi-agent/agent/tee_roi.py, below the header. The
agent ships as its own tree with no path into this package, so the file
is duplicated rather than imported — and the two ends have to agree
exactly about what a zone means, because the server decides whether a
box may be saved and the Pi decides whether a golfer is standing in it.
A disagreement is a camera that never fires.

They drifted once already: `which`, `seen_times` and `seen_in_zones`
were added on the Pi for the audio trigger and never arrived here.
tools/tee_roi_sync_test.py compares the two function by function so the
next divergence is a failing test rather than a quiet one.

ONE BOX WAS NEVER ENOUGH FOR A REAL TEE. A par 3 is played from a back
tee and a middle tee, and often a forward tee as well — separate patches
of turf, tens of feet apart in the frame. A single rectangle covering
both also covers the cart path, the bench and the walkway between them,
and a person crossing any of those is a recording nobody asked for and a
clip somebody has to delete.

So a camera's zones are a LIST of boxes. A golfer in any one of them is
on the tee.

STORED IN THE CAMERA'S NATIVE PIXELS, with the frame size they were
drawn against. The agent scales them if its own frames are a different
size — a box drawn at 1920x1080 is nonsense at 1280x720, and silently
searching the wrong third of the picture is worse than refusing.

Two shapes are accepted on the way in, because the field predates this:

    {"x": .., "y": .., "w": .., "h": ..}              one box, legacy
    {"boxes": [{"x":..,"y":..,"w":..,"h":..,"label":".."}, ...],
     "frame": {"w": 1920, "h": 1080}}                 what we write now

Everything downstream asks this module rather than reading the column,
so neither shape leaks past here.
"""
from __future__ import annotations

MAX_BOXES = 6


def boxes(value) -> list[dict]:
    """Every zone, as a list of {x, y, w, h, label}. [] when unset.

    A box that is not four finite numbers is dropped rather than
    returned half-formed: a caller testing `x <= px <= x + w` against a
    None is a crash in the capture loop, and this is read on the Pi.
    """
    if not isinstance(value, dict):
        return []
    raw = value.get("boxes") if "boxes" in value else [value]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:MAX_BOXES]:
        if not isinstance(item, dict):
            continue
        try:
            x, y = float(item["x"]), float(item["y"])
            w, h = float(item["w"]), float(item["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        box = {"x": int(round(x)), "y": int(round(y)),
               "w": int(round(w)), "h": int(round(h))}
        label = item.get("label")
        if isinstance(label, str) and label.strip():
            box["label"] = label.strip()[:24]
        out.append(box)
    return out


def frame_size(value) -> tuple[int, int] | None:
    """The frame the boxes were drawn against, when it was recorded.

    None for a legacy single box, which carries no frame — those were
    drawn at 1920x1080 by every rig that has ever run, but saying so
    here would be a guess dressed as a fact, so callers decide.
    """
    if not isinstance(value, dict):
        return None
    fr = value.get("frame")
    if not isinstance(fr, dict):
        return None
    try:
        w, h = int(fr["w"]), int(fr["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return (w, h) if w > 0 and h > 0 else None


def union(value) -> dict | None:
    """One box containing every zone, or None when there are none.

    For the places that genuinely want a single rectangle — the ball
    search on the produce side, which has to cover whichever tee was
    actually played from, and the focus meter, which only needs a patch
    of the right grass to measure.
    """
    bs = boxes(value)
    if not bs:
        return None
    x0 = min(b["x"] for b in bs)
    y0 = min(b["y"] for b in bs)
    x1 = max(b["x"] + b["w"] for b in bs)
    y1 = max(b["y"] + b["h"] for b in bs)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def which(point, value):
    """The index of the FIRST zone holding this point, or None.

    `contains` answers "is somebody on a tee", which is the right
    question for deciding to record. It is the wrong question for
    confirming a swing: a sound can only have come from the tee the
    player is standing on, and a camera watching a back tee and a
    forward tee sees both. Returning the index lets a caller ask about
    one of them without re-deriving the geometry.

    Index, not label: labels are operator text and may be blank,
    duplicated or renamed between one capture and the next, while the
    order of the list is what both ends already agree on.
    """
    px, py = point
    for i, b in enumerate(boxes(value)):
        if b["x"] <= px <= b["x"] + b["w"] and b["y"] <= py <= b["y"] + b["h"]:
            return i
    return None


def contains(point, value) -> bool:
    """Is this point inside ANY zone? False when none are set — an
    unconfigured camera should not trigger on everything that walks
    past, and the caller that wants "anywhere" can say so itself."""
    return which(point, value) is not None


def scaled(value, to_w: int, to_h: int, assume=(1920, 1080)) -> dict:
    """The same zones against a frame of a different size.

    `assume` is the basis for a legacy box that recorded no frame of its
    own — every rig that has run one captured at 1080p. Returns the new
    payload in the current shape, frame included, so what comes out can
    be stored or sent as is.
    """
    bs = boxes(value)
    src = frame_size(value) or assume
    if not bs or not to_w or not to_h:
        return {"boxes": bs, "frame": {"w": to_w, "h": to_h}}
    sx, sy = to_w / float(src[0]), to_h / float(src[1])
    if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6:
        return {"boxes": bs, "frame": {"w": to_w, "h": to_h}}
    out = []
    for b in bs:
        nb = {"x": int(round(b["x"] * sx)), "y": int(round(b["y"] * sy)),
              "w": int(round(b["w"] * sx)), "h": int(round(b["h"] * sy))}
        if b.get("label"):
            nb["label"] = b["label"]
        out.append(nb)
    return {"boxes": out, "frame": {"w": to_w, "h": to_h}}


def store(raw_boxes, frame_w: int, frame_h: int) -> dict:
    """Validate what an operator drew, into the shape we store.

    Raises ValueError with something an operator can act on — this is
    the only place a bad zone can enter the system, and a zone that is
    off the frame or inverted would quietly never fire.
    """
    if not isinstance(raw_boxes, list):
        raise ValueError("zones must be a list of boxes")
    if len(raw_boxes) > MAX_BOXES:
        raise ValueError(f"at most {MAX_BOXES} zones")
    if not frame_w or not frame_h:
        raise ValueError("the frame size the zones were drawn on is required")
    cleaned = boxes({"boxes": raw_boxes})
    if len(cleaned) != len(raw_boxes):
        raise ValueError("every zone needs numeric x, y, w and h, w/h above 0")
    for b in cleaned:
        if (b["x"] < 0 or b["y"] < 0
                or b["x"] + b["w"] > frame_w + 1
                or b["y"] + b["h"] > frame_h + 1):
            raise ValueError(
                f"a zone falls outside the {frame_w}x{frame_h} frame")
    return {"boxes": cleaned, "frame": {"w": int(frame_w), "h": int(frame_h)}}


def seen_times(sightings):
    """Just the clock, from a list of (time, zone) or bare times.

    `_person_seen_at` grew a zone index when the audio trigger needed
    to ask WHICH tee somebody was on. Every older reader wants the
    float it used to hold, and a rig mid-upgrade can have a list that
    started one shape and finished the other, so accept both rather
    than make the callers guess.
    """
    out = []
    for s in sightings or []:
        out.append(float(s[0]) if isinstance(s, (tuple, list)) else float(s))
    return out


def seen_in_zones(sightings, zones, at, tolerance_sec: float):
    """Was anybody in one of `zones` within `tolerance_sec` of `at`?

    `zones` is a list of indices, or None for "any zone". None is NOT
    the same as an empty list: None means the caller is not asking
    about geography, [] would mean "these zones" and name none, which
    can never be true and would reject every swing.

    THE AUTHORITY ON WHETHER ANYTHING MATCHED. `zone_seen_in` below
    answers which zone it was, and cannot answer this question too:
    a sighting recorded before the zone index existed matches on time
    alone and has no zone to name, so "no index" and "no match" are
    different answers that a single return value would merge.
    """
    for s in sightings or []:
        if isinstance(s, (tuple, list)):
            t, z = float(s[0]), s[1]
        else:
            t, z = float(s), None
        if abs(t - at) > tolerance_sec:
            continue
        if zones is None or (z is not None and z in zones):
            return True
    return False


def zone_seen_in(sightings, zones, at, tolerance_sec: float):
    """WHICH zone somebody was in near `at`, or None.

    For saying so afterwards -- "movement in the back zone at 10:42"
    is what an operator can check against their memory of the hole,
    while "confirmed" is only the machine agreeing with itself. The
    nearest sighting in time wins, because a golfer walking between
    two tees is in both within the tolerance and the closer one is the
    better account of where the sound came from.

    NOT the gate. `seen_in_zones` decides; this only describes, and
    returns None whenever it cannot describe -- an older sighting list
    with no zones in it, nobody near enough, no sightings at all.
    """
    best_t, best_z = None, None
    for s in sightings or []:
        if not isinstance(s, (tuple, list)):
            continue
        t, z = float(s[0]), s[1]
        if z is None or abs(t - at) > tolerance_sec:
            continue
        if zones is not None and z not in zones:
            continue
        if best_t is None or abs(t - at) < abs(best_t - at):
            best_t, best_z = t, z
    return best_z
