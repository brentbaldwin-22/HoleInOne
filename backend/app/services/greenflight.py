"""The flight, found forwards and anchored on the pitch mark.

PHASE B. The reverse walk (`greendescent`) starts from a ball that has
been measured at rest and walks backwards through the roll. It is good
at that and it stops dead at the bounce, every time, on every clip --
not for want of a better detector but because a bounce is an IMPULSE.
The velocity reverses in y and jumps in magnitude over a single frame,
and no smooth motion model crosses that in either direction. Measured
on the smeared fixture: with the elongation gate and without it, both
walks stop at the frame before the bounce and neither ever reaches a
frame fast enough to smear.

So the flight is not walked back to. It is searched FORWARD, as its
own thing, and the thing that makes that easy is what the walk already
hands over: the pitch mark. This is not a hunt for an unknown object
in a clip. It is one question --

    WHICH CHAIN IN THIS WINDOW LANDS ON THAT PIXEL AT THAT FRAME?

-- and everything that does not land there is discarded without ever
being judged on how straight it is or how fast it fell.

THAT IS THE SAME MOVE THAT FIXED THE TEE. The ascent search spent five
rounds being wrong in five different ways while it tried to find a
flight from scratch beside a sweep that already had; it works now
because the measured ball picks among chains instead of running its
own search. The old green descent search had the identical shape -- it
hunted the whole frame for anything falling, and the operator scrapped
it as too inconsistent. The gates it needed to tell a ball from a
branch (a fall-rate band, a drop fraction, a straightness bar set
between two populations of five) are all proxies for "is this the
shot", and the pitch mark answers that question directly.

WHAT IS REUSED. The detectors are debug3's, unchanged and already
measured on this exact footage: `detect_movers_by_diff` ("on a green
view the one that works"), `detect_ball_blobs` (MOG2) and
`detect_movers_by_plate`. The linking is debug3's `build_tracks` and
`build_descent_chains`. Nothing here is a new way of seeing a ball;
what is new is only which chain gets taken, and that the answer is
drawn rather than asserted.

Never raises.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import cv2
import numpy as np

from .debug3 import (
    DESCENT_CORRIDOR_PX,
    DESCENT_DIFF_PER_FRAME,
    DESCENT_MAX_GAP,
    DESCENT_MAX_SPAN,
    build_descent_chains,
    build_tracks,
    detect_ball_blobs,
    detect_movers_by_diff,
    detect_movers_by_plate,
)

log = logging.getLogger("golfreelz.greenflight")

# HOW FAR BACK FROM THE PITCH MARK TO LOOK, in seconds. A ball is
# visible falling onto a green for about a second; the rest of this is
# margin for a high shot and for a pitch mark that is a frame or two
# out. Past four seconds the ball is above the frame or behind the
# treeline and there is nothing to find.
MAX_FLIGHT_SEC = 4.0

# ...AND A FEW FRAMES PAST IT, so a chain that runs THROUGH the pitch
# into the first of the bounce is still built whole. The fit is
# evaluated at the pitch frame either way; this only stops the linker
# cutting the chain off at the window edge.
AFTER_PITCH_FRAMES = 4

# HOW NEAR THE PITCH MARK A CHAIN'S OWN ARC MUST LAND, in pixels, and
# how much that loosens per frame of extrapolation.
#
# A CHAIN DOES NOT USUALLY REACH THE GROUND. MOG2 "gives the first part
# of the flight and then loses the ball -- against trees, against sky,
# or simply off the top of the frame", which is this codebase's own
# measured note, so the usual case is a chain that stops short and has
# to be carried the rest of the way. Carrying it is exactly where the
# ascent column search went wrong: it ran the chain back LINEARLY over
# 31 frames and overshot by 1361px. A ball is not linear, so the carry
# here is the fitted parabola, and the tolerance widens with the
# distance carried because a longer carry is honestly less certain.
ANCHOR_PX = 40.0
ANCHOR_PX_PER_FRAME = 4.0

# AND A HARD LIMIT ON THE CARRY. Beyond this the extrapolation is
# doing more work than the measurement, and a tolerance wide enough to
# accept it would accept most of the green with it.
ANCHOR_MAX_EXTRAP_FRAMES = 30

# A CHAIN SHORTER THAN THIS CANNOT BE FITTED HONESTLY. Three points lie
# on a parabola by construction, the same way three lie on a line --
# see MIN_DESCENT_POINTS, which was raised from three for exactly this
# reason after a three-point chain beat a real descent beside it.
MIN_FLIGHT_POINTS = 4

# HOW FAR THE POINTS MAY SIT FROM THEIR OWN FITTED ARC, RMS, in pixels.
#
# THIS REPLACES THE STRAIGHTNESS GATE RATHER THAN JOINING IT. `bend`
# measured deviation from a straight LINE, which a real flight has some
# of by definition -- so the bar had to be set high enough to let a
# genuine parabola through (real descents 0.18 to 0.61, noise 18.65).
# Residual against the parabola itself is the same discriminator
# without that compromise: a ball fits its own arc to well under a
# pixel, and a chain of unrelated speckle does not fit one at all.
MAX_FIT_RESID_PX = 6.0

# WHICH DETECTORS, AS A LADDER. Measured in debug3 on 200 frames of
# 720p with a ball falling past a textured tree line:
#
#   diff    2.5 ms/frame       585 detections
#   mog2   17.5 ms/frame     1,069 detections
#   plate  27.7 ms/frame   705,228 detections
#
# The plate has no per-frame cap, so on tree-line texture it buries the
# ball in its own noise. Run the cheap one that works first and
# escalate only on a miss -- the same shape as the green window ladder,
# and for the same reason: a later rung is not a better look, it is a
# more expensive one.
DETECTOR_LADDER = (("diff",), ("diff", "mog2"), ("diff", "mog2", "plate"))

# HOW HARD THE CHEAP DETECTOR LOOKS on each rung of that ladder.
DIFF_SENS = (2, 3, 3)

# THE LINKER'S ASSOCIATION RADIUS. debug3's own default for descents.
LINK_RADIUS_PX = 14.0


def _fit(points):
    """Fit x linear and y quadratic in frame number.

    THE CARRY IS BALLISTIC, NOT LINEAR, and that is the whole lesson of
    the ascent column search: it ran a chain back in a straight line to
    a measured frame 31 frames away and landed 1361px from the ball,
    then reported that 66 chains had been considered and none taken.

    Over the half second a descent lasts, horizontal speed is very
    nearly constant and vertical is very nearly constant acceleration,
    which is a line and a parabola. Returns
    (fx, fy, resid_px, gravity) where fx/fy take a frame number, or
    None when there are too few points to fit honestly.
    """
    if len(points) < 3:
        return None
    t = np.array([float(p["frame"]) for p in points])
    x = np.array([float(p["x"]) for p in points])
    y = np.array([float(p["y"]) for p in points])
    t0 = float(t[0])
    tt = t - t0
    try:
        cx = np.polyfit(tt, x, 1)
        cy = np.polyfit(tt, y, 2)
    except (np.linalg.LinAlgError, ValueError):
        return None

    def fx(f):
        return float(np.polyval(cx, float(f) - t0))

    def fy(f):
        return float(np.polyval(cy, float(f) - t0))

    _rx = x - np.polyval(cx, tt)
    _ry = y - np.polyval(cy, tt)
    resid = float(np.sqrt(np.mean(_rx * _rx + _ry * _ry)))
    # The t^2 coefficient is a half of the downward acceleration, in
    # pixels per frame squared. Positive means falling and speeding up,
    # which is what gravity does in image coordinates.
    return fx, fy, resid, float(cy[0]) * 2.0


def _detect(green_path, f_lo, f_hi, which, sens):
    """debug3's own detectors over one window. Tagged by source."""
    dets: list = []
    stats = {}
    if "diff" in which:
        _d = detect_movers_by_diff(green_path, f_lo, f_hi, sens=int(sens),
                                   per_frame=DESCENT_DIFF_PER_FRAME) or []
        for q in _d:
            q["src"] = "diff"
        dets.extend(_d)
        stats["diff"] = len(_d)
    if "mog2" in which:
        _m = detect_ball_blobs(green_path, f_lo, f_hi,
                               suppress_bodies=False,
                               cap_prefer="large") or {}
        _md = list(_m.get("dets") or [])
        for q in _md:
            q["src"] = "mog2"
        dets.extend(_md)
        stats["mog2"] = len(_md)
    if "plate" in which:
        _p = detect_movers_by_plate(green_path, f_lo, f_hi) or []
        for q in _p:
            q["src"] = "plate"
        dets.extend(_p)
        stats["plate"] = len(_p)
    dets.sort(key=lambda d: d["frame"])
    return dets, stats


def _judge(chain, anchor_xy, pitch_frame):
    """Does this chain's own arc land on the pitch mark?

    THE ONE QUESTION, and the only one asked. Every gate the old
    unanchored search needed -- a fall-rate band, a minimum drop, a
    straightness bar set between two populations of five -- was a proxy
    for "is this the shot". The pitch mark answers that directly, so
    none of them appear here.

    Returns a row describing the chain and, in `ok`, whether it is
    taken. A rejected chain is described in exactly the same shape,
    because the rejected ones are what somebody reads when the answer
    is wrong.
    """
    # ── THE FLIGHT ENDS AT THE PITCH MARK, BY DEFINITION ────────
    #
    # AFTER_PITCH_FRAMES exists so the LINKER can build a chain whole
    # rather than cutting it at the window edge. It is not an
    # invitation to fit a parabola through the bounce, and leaving
    # those points in did exactly that: on the fixture, the chain that
    # ran to f170 -- two frames past the pitch -- fitted its own arc to
    # 5.2px instead of 0.7 and its landing moved 20px, because the
    # first of the bounce is going back UP and no parabola covers both.
    pts = [p for p in chain["points"] if int(p["frame"]) <= int(pitch_frame)]
    row = {
        "n_points": len(pts),
        "n_trimmed": len(chain["points"]) - len(pts),
        "first_frame": int(pts[0]["frame"]) if pts else None,
        "last_frame": int(pts[-1]["frame"]) if pts else None,
        "from": [int(pts[0]["x"]), int(pts[0]["y"])] if pts else None,
        "to": [int(pts[-1]["x"]), int(pts[-1]["y"])] if pts else None,
        "fell_px": int(pts[-1]["y"] - pts[0]["y"]) if pts else 0,
        "srcs": sorted({str(p.get("src") or "?") for p in pts}),
        "ok": False, "why": None, "miss_px": None,
        "extrap_frames": None, "resid_px": None, "gravity": None,
        "lands_at": None, "score": None,
    }
    if len(pts) < MIN_FLIGHT_POINTS:
        row["why"] = ("%d point(s), under the %d a parabola can be fitted "
                      "to honestly" % (len(pts), MIN_FLIGHT_POINTS))
        return row
    _f = _fit(pts)
    if _f is None:
        row["why"] = "could not be fitted"
        return row
    fx, fy, resid, grav = _f
    row["resid_px"] = round(resid, 2)
    row["gravity"] = round(grav, 3)
    _ex = max(0, int(pitch_frame) - int(pts[-1]["frame"]))
    row["extrap_frames"] = _ex
    _lx, _ly = fx(pitch_frame), fy(pitch_frame)
    row["lands_at"] = [int(round(_lx)), int(round(_ly))]
    _miss = math.hypot(_lx - float(anchor_xy[0]),
                       _ly - float(anchor_xy[1]))
    row["miss_px"] = round(_miss, 1)
    _tol = ANCHOR_PX + ANCHOR_PX_PER_FRAME * _ex

    # A FALLING BALL ACCELERATES DOWNWARDS. In image coordinates y
    # grows downwards, so the t^2 term is positive. This is free, it is
    # physics rather than a tuned threshold, and it removes every chain
    # built from something drifting or rising.
    if grav <= 0:
        row["why"] = ("its arc curves the wrong way (%.3f px/frame² — a "
                      "falling ball accelerates DOWNWARDS)" % grav)
        return row
    if resid > MAX_FIT_RESID_PX:
        row["why"] = ("its points sit %.1fpx from their own fitted arc, "
                      "over %gpx — a ball fits its own parabola, a string "
                      "of unrelated specks does not"
                      % (resid, MAX_FIT_RESID_PX))
        return row
    if _ex > ANCHOR_MAX_EXTRAP_FRAMES:
        row["why"] = ("ends %d frame(s) before the pitch mark, over the %d "
                      "it may be carried — past that the extrapolation is "
                      "doing more work than the measurement"
                      % (_ex, ANCHOR_MAX_EXTRAP_FRAMES))
        return row
    if _miss > _tol:
        row["why"] = ("its arc lands at %d,%d — %.0fpx from the pitch mark, "
                      "outside the %.0fpx allowed after carrying it %d "
                      "frame(s)" % (row["lands_at"][0], row["lands_at"][1],
                                    _miss, _tol, _ex))
        return row
    row["ok"] = True
    # ── MEASUREMENT BEATS EXTRAPOLATION ─────────────────────────────
    #
    # RANKING BY MISS ALONE GOT THIS BACKWARDS. On the fixture it took
    # a 32-point chain that stopped a third of the way down and was
    # carried 26 frames, over a chain that was MEASURED the whole way
    # to the ground, purely because the short one's arc happened to
    # land 15px out against the long one's 36. That is the opposite of
    # the lesson every other part of this week taught.
    #
    # Carry is penalised at exactly the rate the tolerance loosens for
    # it, so the two halves of the rule cannot disagree: a frame of
    # extrapolation costs what a frame of extrapolation is worth.
    row["score"] = round(_miss + ANCHOR_PX_PER_FRAME * _ex, 1)
    row["why"] = ("%d point(s) falling %dpx, fitting their own arc to "
                  "%.1fpx, landing %.0fpx from the pitch mark after being "
                  "carried %d frame(s)"
                  % (len(pts), row["fell_px"], resid, _miss, _ex))
    return row


def find_flight_to(green_path, anchor_xy, anchor_frame: int, *,
                   max_flight_sec: float = MAX_FLIGHT_SEC,
                   out_dir=None, stem="greenflight") -> dict:
    """The flight that lands on this pixel at this frame.

    ANCHOR ON A MEASUREMENT, NOT ON AN INFERENCE. The obvious thing to
    pass here is the reverse walk's PITCH MARK, and it is the wrong
    thing. The pitch mark is derived -- "the first frame it stopped
    falling" -- from a chain that, whenever the walk gave up at the
    bounce, does not contain the fall at all; so the rule names a point
    somewhere in the roll. Measured on the smeared fixture: the walk's
    pitch mark came out at f181, (1502, 1045), while the ball really
    pitched at f168, (1427, 994) -- thirteen frames and ninety pixels
    out. Anchored on it, this search returned an eight-point chain of
    the roll. Anchored on the earliest point the walk actually SAW --
    f169, (1430, 987), eight pixels from the truth -- it returned
    fifty-two points of real flight falling 856px.

    So `anchor_xy` and `anchor_frame` are the earliest point the
    reverse walk actually saw the ball at. Where the walk did reach the
    fall, that point IS the pitch mark; where it did not, it is still a
    true position of the ball at a true frame, which the pitch mark is
    not.

    Returns {ok, points, n_points, first_frame, last_frame, fell_px,
    why, chains, ladder, rung, counts, image}. `points` are earliest
    first, the order the ball flew. `chains` describes EVERY chain
    considered with the reason it was taken or refused, because the
    refused ones are what somebody reads when the answer is wrong.

    Never raises.
    """
    out: dict = {
        "ok": False, "points": [], "n_points": 0, "first_frame": None,
        "last_frame": None, "fell_px": 0, "why": None, "image": None,
        "chains": [], "ladder": [], "rung": None, "counts": {},
        # ── WHAT THIS IS AND WHY IT IS NOT THE OLD SEARCH ──────────
        #
        # The old green descent search hunted the whole frame for
        # anything falling and was scrapped as too inconsistent. The
        # difference is not the detector -- they are the same
        # detectors -- it is that this one is told where the ball
        # ended up and only has to say which chain gets there.
        "how": {
            "anchored_on": ("the earliest point the reverse walk "
                            "actually SAW the ball at — not its "
                            "computed pitch mark, which is an "
                            "inference over a chain that may not "
                            "contain the fall, and was 90px out on "
                            "the fixture that prompted this"),
            "question": ("which chain's own fitted arc lands on that "
                         "pixel at that frame — not which chain looks "
                         "most like a descent"),
            "detectors": ("debug3's own, unchanged: frame differencing "
                          "first because on a green view it is the one "
                          "that works, then MOG2, then the plate "
                          "medians. Escalated only on a miss: the plate "
                          "has no per-frame cap and on treeline texture "
                          "returns hundreds of thousands of detections, "
                          "burying the ball in its own noise."),
            "carry": ("a fitted parabola, never a straight line. "
                      "Carrying a chain LINEARLY to a measured frame is "
                      "what made the tee's column search overshoot the "
                      "ball by 1361px over 31 frames."),
        },
    }
    cap = None
    try:
        if not anchor_xy or anchor_frame is None:
            out["why"] = ("nothing to anchor on — the reverse walk never "
                          "saw the ball at all, so there is no measured "
                          "position to search back from")
            return out
        cap = cv2.VideoCapture(str(green_path))
        if not cap.isOpened():
            out["why"] = "could not open the green clip"
            return out
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        cap = None

        _pf = int(anchor_frame)
        f_lo = max(0, _pf - int(round(float(max_flight_sec) * fps)))
        f_hi = _pf + AFTER_PITCH_FRAMES
        if total:
            f_hi = min(f_hi, total - 1)
        if f_hi - f_lo < MIN_FLIGHT_POINTS:
            out["why"] = ("the anchor is at the very start of the clip "
                          "— there is no footage before it to search")
            return out
        out["window"] = [f_lo, f_hi]
        out["window_sec"] = [round(f_lo / fps, 2), round(f_hi / fps, 2)]

        _best_rows: list = []
        for _i, _which in enumerate(DETECTOR_LADDER):
            _sens = DIFF_SENS[min(_i, len(DIFF_SENS) - 1)]
            dets, _stats = _detect(green_path, f_lo, f_hi, _which, _sens)
            # BOTH LINKERS, as debug3 does. The radius widens with the
            # gap and eventually spans the picture; the box walk looks
            # only in a tall narrow strip below the last point, so a
            # bush three hundred pixels across cannot be mistaken for
            # the next frame of a fall. Added, not substituted --
            # a chain one finds and the other does not is still a
            # chain, and the anchor judges both.
            _tr = build_tracks(dets, LINK_RADIUS_PX,
                               min_len=MIN_FLIGHT_POINTS,
                               max_gap=DESCENT_MAX_GAP,
                               corridor_px=DESCENT_CORRIDOR_PX,
                               max_span=DESCENT_MAX_SPAN)
            _bx = build_descent_chains(dets, fw, fh,
                                       max_gap=DESCENT_MAX_GAP,
                                       min_len=MIN_FLIGHT_POINTS)
            _chains = list(_tr) + list(_bx)
            _rows = [_judge(c, anchor_xy, _pf) for c in _chains]
            _taken = [r for r in _rows if r["ok"]]
            out["ladder"].append({
                "detectors": list(_which), "diff_sens": _sens,
                "n_dets": len(dets), "det_stats": _stats,
                "n_chains": len(_chains),
                "n_radius": len(_tr), "n_box": len(_bx),
                "n_landing_on_the_mark": len(_taken),
                "why": ("%d detection(s) -> %d chain(s) (%d by radius, %d "
                        "by box walk) -> %d landing on the pitch mark"
                        % (len(dets), len(_chains), len(_tr), len(_bx),
                           len(_taken))),
            })
            if len(_rows) > len(_best_rows):
                _best_rows = _rows
            if _taken:
                out["rung"] = _i + 1
                _best_rows = _rows
                # NEAREST THE MARK WINS. Among chains that all land on
                # it, the one whose arc gets closest is the one that
                # was measured rather than carried.
                _taken.sort(key=lambda r: (r["score"], -r["n_points"]))
                _win = _taken[0]
                _idx = _rows.index(_win)
                # TRIMMED THE SAME WAY IT WAS JUDGED. Reporting the
                # untrimmed chain would put the first frames of the
                # bounce on a picture labelled "the flight".
                _pts = [p for p in _chains[_idx]["points"]
                        if int(p["frame"]) <= _pf]
                out["points"] = [
                    {"frame": int(p["frame"]), "x": float(p["x"]),
                     "y": float(p["y"]), "area": p.get("area"),
                     "src": p.get("src"), "found": True}
                    for p in _pts]
                out["n_points"] = len(_pts)
                out["first_frame"] = int(_pts[0]["frame"])
                out["last_frame"] = int(_pts[-1]["frame"])
                out["fell_px"] = int(_pts[-1]["y"] - _pts[0]["y"])
                out["ok"] = True
                out["why"] = _win["why"]
                out["taken"] = _win
                break

        # EVERY CHAIN, TAKEN OR NOT, with the reason. Capped and sorted
        # so the near misses are at the top: a chain that landed 60px
        # off is a tolerance question and a chain that landed 900px off
        # is a different object, and the two need opposite responses.
        _best_rows.sort(key=lambda r: (not r["ok"],
                                       r["score"] if r.get("score")
                                       is not None else
                                       (r["miss_px"]
                                        if r["miss_px"] is not None
                                        else 1e9)))
        out["chains"] = _best_rows[:40]
        out["counts"] = {
            "chains_considered": len(_best_rows),
            "fitted": sum(1 for r in _best_rows if r["resid_px"] is not None),
            "curving_the_wrong_way": sum(
                1 for r in _best_rows
                if r["gravity"] is not None and r["gravity"] <= 0),
            "landed_on_the_mark": sum(1 for r in _best_rows if r["ok"]),
            "nearest_miss_px": next((r["miss_px"] for r in _best_rows
                                     if r["miss_px"] is not None), None),
        }
        if not out["ok"]:
            _near = out["counts"]["nearest_miss_px"]
            out["why"] = (
                "no chain in the %.1fs before the pitch mark lands on it. "
                "%d chain(s) were considered%s. The ball was either not "
                "detected at all in this window — look at the detection "
                "counts per rung — or it was detected and the linker "
                "never joined it up."
                % (float(max_flight_sec), len(_best_rows),
                   ("; the nearest came within %.0fpx" % _near)
                   if _near is not None else ""))

        if out_dir is not None:
            out["image"] = _flight_image(green_path, out, anchor_xy, _pf,
                                         Path(out_dir), stem)
    except Exception as exc:  # noqa: BLE001
        log.warning("greenflight: failed: %s", exc)
        out["why"] = f"the forward flight search failed: {exc}"
    finally:
        if cap is not None:
            cap.release()
    return out


def _flight_image(green_path, info, anchor_xy, pitch_frame, out_dir, stem):
    """The flight, the pitch mark, and every chain that was refused.

    THE REFUSED ONES ARE THE POINT. A picture of the chain that was
    taken proves nothing when the answer is wrong -- what is needed
    then is the chains that were NOT taken and how near each came, and
    whether the ball is in the picture at all. Grey is considered,
    colour is taken, and the dashed tail is the part that was carried
    rather than measured.
    """
    try:
        cap = cv2.VideoCapture(str(green_path))
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(pitch_frame))
        ok, im = cap.read()
        cap.release()
        if not ok or im is None:
            return None
        u = max(1, int(round(max(im.shape[:2]) / 600.0)))
        im[0:52 * u] = (im[0:52 * u].astype("float32") * 0.35).astype("uint8")

        # EVERY CHAIN CONSIDERED, in grey, with how far it missed.
        for r in (info.get("chains") or [])[:24]:
            if r.get("ok") or not r.get("lands_at"):
                continue
            cv2.line(im, tuple(r["from"]), tuple(r["to"]),
                     (120, 120, 120), u, cv2.LINE_AA)
            cv2.circle(im, tuple(r["to"]), 3 * u, (120, 120, 120), -1,
                       cv2.LINE_AA)
            if r.get("miss_px") is not None:
                cv2.putText(im, "%.0fpx" % r["miss_px"],
                            (r["to"][0] + 6 * u, r["to"][1]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4 * u,
                            (150, 150, 150), u, cv2.LINE_AA)

        _pts = info.get("points") or []
        for a, b in zip(_pts, _pts[1:]):
            cv2.line(im, (int(a["x"]), int(a["y"])),
                     (int(b["x"]), int(b["y"])), (90, 230, 255), u + 1,
                     cv2.LINE_AA)
        for q in _pts:
            cv2.circle(im, (int(q["x"]), int(q["y"])), 3 * u,
                       (90, 230, 255), -1, cv2.LINE_AA)
        # THE CARRIED TAIL, DASHED, so what was measured and what was
        # inferred are never the same mark on the picture.
        _tk = info.get("taken") or {}
        if _pts and _tk.get("lands_at"):
            _a = (int(_pts[-1]["x"]), int(_pts[-1]["y"]))
            _b = tuple(_tk["lands_at"])
            _n = max(1, int(math.hypot(_b[0] - _a[0], _b[1] - _a[1])
                            / (10.0 * u)))
            for _k in range(0, _n, 2):
                _p0 = (int(_a[0] + (_b[0] - _a[0]) * _k / _n),
                       int(_a[1] + (_b[1] - _a[1]) * _k / _n))
                _p1 = (int(_a[0] + (_b[0] - _a[0]) * (_k + 1) / _n),
                       int(_a[1] + (_b[1] - _a[1]) * (_k + 1) / _n))
                cv2.line(im, _p0, _p1, (90, 230, 255), u, cv2.LINE_AA)

        cv2.drawMarker(im, (int(anchor_xy[0]), int(anchor_xy[1])),
                       (90, 230, 120), cv2.MARKER_TILTED_CROSS, 22 * u,
                       u + 2)
        cv2.putText(im, "anchor - earliest the walk really saw it",
                    (int(anchor_xy[0]) + 14 * u, int(anchor_xy[1]) - 10 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (90, 230, 120), u,
                    cv2.LINE_AA)
        cv2.putText(im, str(info.get("why") or "")[:150], (12 * u, 22 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (255, 255, 255), u,
                    cv2.LINE_AA)
        _c = info.get("counts") or {}
        cv2.putText(im,
                    "%d chain(s) considered, %d landed on the mark"
                    % (_c.get("chains_considered", 0),
                       _c.get("landed_on_the_mark", 0))
                    + ("  |  grey = refused, with how far its arc missed"
                       if _c.get("chains_considered") else ""),
                    (12 * u, 42 * u), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * u,
                    (160, 230, 255), u, cv2.LINE_AA)
        _p = out_dir / f"{stem}-flight.jpg"
        if not cv2.imwrite(str(_p), im, [int(cv2.IMWRITE_JPEG_QUALITY), 86]):
            return None
        return str(_p)
    except Exception as exc:  # noqa: BLE001
        log.warning("greenflight: image failed: %s", exc)
        return None
