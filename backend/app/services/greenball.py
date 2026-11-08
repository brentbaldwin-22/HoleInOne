"""Balls on the green, found the way the tee finds a ball at address.

THE SAME DETECTOR, NOT A SECOND ONE. `ball_vanish._detections` is what
Test Cut uses to find a ball sitting on the turf in front of a golfer:
a white top-hat -- what is brighter than its own immediate
neighbourhood -- then area, roundness and fill. It was tuned against
real 1080p turf over a week of clips, and the thing it is tuned to
find is exactly the thing this file is looking for. Writing a second
detector for the green would be writing the same week again.

WHAT IS DIFFERENT IS THE SCALE, NOT THE QUESTION. On the tee the ball
is a few feet from the camera and spans a dozen pixels. On the green
it is a hundred yards away and spans three or four, and the top-hat's
15px structuring element is then wide enough to swallow it. So the
crop is cut into zones and each zone is scaled up before the detector
runs: at 3x a four-pixel ball is a twelve-pixel ball, which is the
size the constants were set against.

AND THE GRID IS GONE. The model used to be shown the crop with a
labelled pixel grid over it, because it knows WHAT it is looking at
and estimates WHERE badly. On a cropped green the grid had to be fine
enough to be useful and then its labels overlapped into an unreadable
mustard stripe across the picture -- while covering the ball it was
drawn to help find. A detector does not need a grid: it reports a
position because it measured one.

So the model, where it is still asked anything, is asked the only
question it is reliably good at: which of these circles is the ball.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .ball_vanish import BALL_MAX_AREA, BALL_MIN_AREA, _detections

log = logging.getLogger("golfreelz.greenball")

# HOW MANY ZONES THE CROP IS CUT INTO. Four, because that is what was
# asked for and because it is the number at which a 2560px-wide green
# crop gives each zone a 640px span -- scaled to 1280 that is a 2x
# zoom on top of the crop's own, which puts a back-of-the-green ball
# in the size range the detector's constants were measured at.
N_ZONES = 4

# HOW FAR THE ZONES OVERLAP, as a fraction of a zone's own size. A
# ball sitting on a boundary is cut in half by both zones and is then
# neither round enough nor big enough for either -- which is a ball
# lost to bookkeeping rather than to anything about the picture.
ZONE_OVERLAP_FRAC = 0.08

# A CROP THIS WIDE RELATIVE TO ITS HEIGHT is cut into four columns
# rather than a 2x2 grid. A green seen from behind the tee is a letter
# box; quartering it into squares would make two of the four zones
# mostly sky or mostly bunker.
WIDE_ASPECT = 2.5

# THE LONG EDGE EACH ZONE IS SCALED TO, and the cap on how far it may
# be stretched to get there. Past about 4x the interpolation is
# inventing the ball's edges rather than revealing them, and the fill
# and roundness tests start measuring the interpolator.
ZONE_TARGET_LONG_EDGE = 1280
ZONE_MAX_UPSCALE = 4.0

# TWO DETECTIONS THIS CLOSE, in ORIGINAL frame pixels, are the same
# thing. Used twice: to merge what overlapping zones both saw, and to
# decide whether a ball in the AFTER picture was already there in the
# BEFORE one.
SAME_BALL_PX = 8.0

# HOW FAR OUTSIDE THE OUTLINE a detection may still count, as a
# fraction of the green's smaller dimension. The outline is drawn by
# hand on a live view and is not a survey: a ball on the collar is on
# the green as far as anybody watching is concerned, and refusing it
# for being two feet outside a line somebody dragged with a mouse is
# the detector being precise about the wrong thing.
OUTLINE_SLACK_FRAC = 0.06

# HOW MANY TURNED-DOWN CANDIDATES COME BACK PER ZONE. A zone of turf
# in low sun throws plenty of one-pixel specks, and a list that long
# is not a report. The ones worth reading are the biggest.
MAX_REJECTS_PER_ZONE = 8


def _scaled_polygon(polygon, shape, drawn_at=None):
    """[[x,y], ...] in THIS frame's pixels, or None."""
    if not polygon:
        return None
    h, w = shape[:2]
    pts = np.array(polygon, dtype=np.float64)
    if drawn_at:
        dw, dh = float(drawn_at[0] or w), float(drawn_at[1] or h)
        if dw > 0 and dh > 0:
            pts[:, 0] *= w / dw
            pts[:, 1] *= h / dh
    return pts.round().astype(np.int32)


def _zone_rects(x0, y0, x1, y1):
    """Four overlapping rectangles covering (x0,y0)-(x1,y1)."""
    w, h = x1 - x0, y1 - y0
    cols, rows = (N_ZONES, 1) if w >= WIDE_ASPECT * h else (2, 2)
    out = []
    zw, zh = w / cols, h / rows
    ox, oy = zw * ZONE_OVERLAP_FRAC, zh * ZONE_OVERLAP_FRAC
    for r in range(rows):
        for c in range(cols):
            ax = int(max(x0, x0 + c * zw - ox))
            ay = int(max(y0, y0 + r * zh - oy))
            bx = int(min(x1, x0 + (c + 1) * zw + ox))
            by = int(min(y1, y0 + (r + 1) * zh + oy))
            if bx - ax > 8 and by - ay > 8:
                out.append((ax, ay, bx, by))
    return out


def _detect_in_zone(frame_bgr, rect):
    """(detections in ORIGINAL pixels, the zoomed zone, its scale).

    The detector is `ball_vanish._detections` with `rel=0.0`: an
    ABSOLUTE local-contrast threshold rather than one scaled off the
    brightest thing in the box. That distinction is the whole reason
    balls were being missed on the tee -- a white shirt in the box
    set the bar and the ball never cleared it -- and a green with a
    bunker, a flagstick or somebody's white cap in the same zone is
    the same trap.
    """
    ax, ay, bx, by = rect
    sub = frame_bgr[ay:by, ax:bx]
    if sub.size == 0:
        return [], None, 1.0
    sh, sw = sub.shape[:2]
    scale = min(ZONE_MAX_UPSCALE,
                ZONE_TARGET_LONG_EDGE / float(max(sw, sh)))
    if scale > 1.02:
        sub = cv2.resize(sub, (int(sw * scale), int(sh * scale)),
                         interpolation=cv2.INTER_CUBIC)
    else:
        scale = 1.0
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    zh, zw = gray.shape[:2]
    out = []
    # ── AND WHAT IT TURNED DOWN, ALWAYS ─────────────────────────────
    #
    # "No ball on this green" is one sentence covering five different
    # failures: nothing was brighter than its surroundings at all; the
    # ball was, but it is one pixel after the top-hat; it is a streak;
    # it is a blob the size of a shirt; or it is round and bright and
    # the right size and sits outside a hand-drawn outline. Those need
    # opposite fixes and the first run of this on real footage came
    # back empty with nothing to look at.
    _rej: list = []
    _stat: list = []
    for cx, cy, area, contrast in _detections(gray, [(0, 0, zw, zh)],
                                              rel=0.0, rejects=_rej,
                                              stats_out=_stat):
        # ── THE SIZE GATE BELONGS IN ORIGINAL PIXELS ────────────────
        #
        # `_detections` applies BALL_MIN_AREA in the pixels of the
        # image it is handed, and the image it is handed here has been
        # scaled up. At 2.5x its floor of 3px of area means 0.5px of
        # area in the frame -- so every re-quantised speck on a
        # mowing stripe passes it. Measured on a synthetic green with
        # real-ish turf noise: 59 "balls" before this line, 1 after,
        # and the one is the ball. The zoom exists to give the
        # top-hat's 15px kernel something to work with, not to make
        # the detector less fussy about size.
        _oa = float(area) / (scale * scale)
        if not (BALL_MIN_AREA <= _oa <= BALL_MAX_AREA):
            continue
        out.append({
            "x": float(ax + cx / scale), "y": float(ay + cy / scale),
            # AREA AND DIAMETER IN ORIGINAL PIXELS. The numbers the
            # detector returns are in the ZOOMED zone, where a 4px
            # ball is 48px of area rather than 3. Reporting those
            # would make every ball look like a bunker.
            "area": round(_oa, 1),
            "diam_px": round(2.0 * (_oa / 3.14159) ** 0.5, 1),
            "contrast": round(float(contrast), 1),
            "zoom": round(float(scale), 2),
        })
    # Rejections in ORIGINAL pixels too, and the biggest first: a near
    # miss is a thing that nearly passed, and a zone can throw dozens
    # of one-pixel specks that are not worth a row each.
    _rows = []
    for r in _rej:
        _oa = float(r["area"]) / (scale * scale)
        _rows.append({"x": float(ax + r["x"] / scale),
                      "y": float(ay + r["y"] / scale),
                      "area": round(_oa, 1),
                      "diam_px": round(2.0 * (_oa / 3.14159) ** 0.5, 1),
                      "why": r["why"]})
    _rows.sort(key=lambda z: -z["area"])
    _diag = {
        "rejected": _rows[:MAX_REJECTS_PER_ZONE],
        "n_rejected": len(_rows),
        # THE STRONGEST THING IN THE ZONE, whether or not anything
        # passed. This is the one number that separates "the ball was
        # never bright enough to be a candidate" -- an exposure or a
        # contrast problem, nothing to do with any gate -- from "it
        # was, and a gate refused it".
        "hat_max": (_stat[0]["hat_max"] if _stat else None),
        "hat_needs": (_stat[0]["needs"] if _stat else None),
    }
    return out, sub, scale, _diag


def _merge(dets, px=SAME_BALL_PX):
    """One row per thing, brightest kept, for detections in one frame."""
    out: list = []
    for d in sorted(dets, key=lambda z: -z["contrast"]):
        if any((d["x"] - o["x"]) ** 2 + (d["y"] - o["y"]) ** 2 <= px * px
               for o in out):
            continue
        out.append(d)
    return out


def _inside(poly, x, y, slack):
    """Is (x, y) on the green, allowing for a hand-drawn outline?"""
    if poly is None:
        return True
    return cv2.pointPolygonTest(poly, (float(x), float(y)), True) >= -slack


def _ring(img, x, y, colour, label, u=1, r=14, thick=None):
    """A ring on the ball, and its label wherever the label fits.

    A ball near the right-hand edge of a green had "at rest" written
    off the end of the picture -- which reads as a complete label that
    happens to say "at res", on the one mark the whole picture exists
    to show. Measure the text and put it on the other side when it
    would not fit, the same way the plate renderer does.
    """
    cv2.circle(img, (int(x), int(y)), int(r * u), colour,
               int(thick or (u + 1)), cv2.LINE_AA)
    if not label:
        return
    _sc = 0.5 * u
    (_tw, _th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, _sc, u)
    _lx = int(x) + int(r * u) + 4 * u
    if _lx + _tw > img.shape[1] - 4 * u:
        _lx = max(4 * u, int(x) - int(r * u) - 4 * u - _tw)
    _ly = int(y) - int(r * u) // 2
    _ly = min(img.shape[0] - 4 * u, max(_th + 4 * u, _ly))
    cv2.putText(img, label, (_lx, _ly), cv2.FONT_HERSHEY_SIMPLEX,
                _sc, colour, u, cv2.LINE_AA)


# Orange for a ball that was already there, cyan for the one that
# arrived. Chosen to survive a JPEG of mown grass, which eats green
# and most yellows.
WAS_THERE = (70, 170, 255)
ARRIVED = (255, 210, 60)


def find_balls_on_green(before_bgr, after_bgr, polygon=None, drawn_at=None,
                        out_dir=None, stem="greenball") -> dict:
    """Existing balls in the before frame, and what arrived by the after.

    Returns {crop, zones, before, after, new, best, images, why}. Every
    position is in the ORIGINAL frame's pixels, whatever zoom it was
    found at -- there is exactly one coordinate system that leaves this
    file, and it is the one every other part of the app already uses.

    Never raises.
    """
    out: dict = {"ok": False, "crop": None, "zones": [], "before": [],
                 "after": [], "new": [], "best": None, "images": {},
                 "why": None}
    try:
        h, w = after_bgr.shape[:2]
        poly = _scaled_polygon(polygon, after_bgr.shape, drawn_at)
        if poly is not None:
            x0 = int(max(0, poly[:, 0].min()))
            y0 = int(max(0, poly[:, 1].min()))
            x1 = int(min(w, poly[:, 0].max()))
            y1 = int(min(h, poly[:, 1].max()))
            _sl = OUTLINE_SLACK_FRAC * min(x1 - x0, y1 - y0)
        else:
            x0, y0, x1, y1, _sl = 0, 0, w, h, 0.0
        if x1 - x0 < 32 or y1 - y0 < 32:
            out["why"] = "the green's outline is too small to search"
            return out
        out["crop"] = {"rect": [x0, y0, x1 - x0, y1 - y0],
                       "from": ("the green's outline" if poly is not None
                                else "the whole frame — no outline drawn")}

        rects = _zone_rects(x0, y0, x1, y1)
        _b_all, _a_all = [], []
        for i, rect in enumerate(rects):
            _bd, _bz, _bs, _bdiag = _detect_in_zone(before_bgr, rect)
            _ad, _az, _as, _adiag = _detect_in_zone(after_bgr, rect)
            # OUTSIDE THE OUTLINE IS ITS OWN REJECTION, recorded with
            # the rest. A round, bright, ball-sized thing refused for
            # being two feet past a line somebody dragged with a mouse
            # is the single most misleading silent failure here.
            for _d in _ad:
                if not _inside(poly, _d["x"], _d["y"], _sl):
                    _adiag["rejected"].append(dict(
                        _d, why="outside the green's outline"))
                    _adiag["n_rejected"] += 1
            _bd = [d for d in _bd if _inside(poly, d["x"], d["y"], _sl)]
            _ad = [d for d in _ad if _inside(poly, d["x"], d["y"], _sl)]
            for d in _bd + _ad:
                d["zone"] = i + 1
            _b_all += _bd
            _a_all += _ad
            _zrow = {"n": i + 1, "rect": [rect[0], rect[1],
                                          rect[2] - rect[0],
                                          rect[3] - rect[1]],
                     "zoom": round(float(_as), 2),
                     "n_before": len(_bd), "n_after": len(_ad),
                     "hat_max": _adiag.get("hat_max"),
                     "hat_needs": _adiag.get("hat_needs"),
                     "n_rejected": _adiag.get("n_rejected"),
                     "rejected": _adiag.get("rejected")}
            if out_dir is not None and _bz is not None and _az is not None:
                _zrow["path"] = _zone_image(
                    _bz, _az, _bd, _ad, rect, _as,
                    Path(out_dir) / f"{stem}-zone{i + 1}.jpg",
                    rejects=_adiag.get("rejected"),
                    hat=(_adiag.get("hat_max"), _adiag.get("hat_needs")))
            out["zones"].append(_zrow)

        out["before"] = _merge(_b_all)
        out["after"] = _merge(_a_all)
        # NEW MEANS NOT THERE BEFORE. A ball that is in both pictures
        # is the partner's, already on the green when the shot was
        # played -- reported, because an operator reading a confusing
        # answer deserves to know how many balls are lying there, and
        # never confused with the one that arrived.
        out["new"] = [
            d for d in out["after"]
            if not any((d["x"] - o["x"]) ** 2 + (d["y"] - o["y"]) ** 2
                       <= SAME_BALL_PX * SAME_BALL_PX for o in out["before"])]
        # Brightest first: on a green the ball is the brightest small
        # round thing there is, and unlike the tee there is no white
        # shoe standing next to it.
        out["new"].sort(key=lambda z: -z["contrast"])
        out["best"] = out["new"][0] if out["new"] else None
        out["ok"] = bool(out["best"])
        _hats = [z["hat_max"] for z in out["zones"]
                 if z.get("hat_max") is not None]
        _needs = next((z["hat_needs"] for z in out["zones"]
                       if z.get("hat_needs")), None)
        _nrej = sum(z.get("n_rejected") or 0 for z in out["zones"])
        out["found_nothing_because"] = None
        if not out["new"]:
            # WHICH STAGE IT FELL AT. Five failures used to share one
            # empty answer, and they need opposite fixes.
            if _hats and max(_hats) < (_needs or 18):
                out["found_nothing_because"] = (
                    "nothing anywhere on the green was %d grey levels "
                    "brighter than its own surroundings — the brightest "
                    "was %d. That is not a ball a gate refused; it is a "
                    "ball that was never a candidate. The green is in "
                    "flat light or shade, or the frame times are wrong."
                    % ((_needs or 18), max(_hats)))
            elif out["after"]:
                # BEFORE THE GATE SENTENCE, deliberately. A green with
                # a bunker on it always has something that gets
                # rejected for size, so "things were turned down" is
                # true on nearly every run and says nothing. "Every
                # ball-shaped thing here was already here" is the
                # finding: the shot missed the green, or the frames
                # are too late and somebody has pocketed it.
                out["found_nothing_because"] = (
                    "%d ball-shaped thing(s) were found in the after "
                    "frame and every one was already there in the "
                    "before frame — nothing arrived between the two. "
                    "Either the shot missed, or the after frame is "
                    "late enough that the ball has been marked."
                    % len(out["after"]))
            elif _nrej:
                out["found_nothing_because"] = (
                    "%d thing(s) were bright enough and every one was "
                    "turned down — see the grey squares on the zone "
                    "pictures and the gate each died on. A grey square "
                    "where the ball is means a gate is set wrong."
                    % _nrej)
            else:
                out["found_nothing_because"] = (
                    "nothing ball-shaped in either frame, and nothing "
                    "bright enough was turned down either — which is "
                    "the combination that should not happen, so the "
                    "zone pictures are the place to look.")
        out["why"] = (
            "%d ball(s) already on the green, %d thing(s) in the after "
            "frame, %d of them new — searched %d zone(s) at up to %.1fx"
            % (len(out["before"]), len(out["after"]), len(out["new"]),
               len(out["zones"]),
               max([z["zoom"] for z in out["zones"]] or [1.0]))
            + ("" if out["new"] else ". " + out["found_nothing_because"]))

        if out_dir is not None:
            _d = Path(out_dir)
            out["images"] = {
                "before": _marked(before_bgr, poly, (x0, y0, x1, y1),
                                  out["before"], [], _d / f"{stem}-before.jpg"),
                "after": _marked(after_bgr, poly, (x0, y0, x1, y1),
                                 out["before"], out["new"],
                                 _d / f"{stem}-after.jpg"),
            }
            # ...AND THE NUMBERED PAIR, only when there is a choice to
            # make. One candidate is not a question, and rendering a
            # picker for it would invite one to be asked.
            if len(out["new"]) > 1:
                out["images"]["picker"] = pair_for_picker(
                    before_bgr, after_bgr, poly, (x0, y0, x1, y1),
                    out["before"], out["new"], _d / f"{stem}-pick.jpg")
    except Exception as exc:  # noqa: BLE001
        log.warning("greenball: failed: %s", exc)
        out["why"] = f"the tiled ball search failed: {exc}"
    return out


def _marked(frame_bgr, poly, box, was_there, arrived, path):
    """The crop, clean, with rings and nothing else on it.

    NO GRID, NO BAND, NO CAPTION BAR. The whole complaint that started
    this was a picture covered in overlapping yellow numbers, one of
    which sat on top of the ball. The only marks here are the balls.
    """
    try:
        x0, y0, x1, y1 = box
        crop = frame_bgr[y0:y1, x0:x1].copy()
        ch, cw = crop.shape[:2]
        u = max(1, int(round(max(cw, ch) / 700.0)))
        if poly is not None:
            _p = poly.copy()
            _p[:, 0] -= x0
            _p[:, 1] -= y0
            cv2.polylines(crop, [_p], True, (120, 230, 170), u)
        for i, d in enumerate(was_there):
            _ring(crop, d["x"] - x0, d["y"] - y0, WAS_THERE,
                  "already there" if len(was_there) == 1 else f"B{i + 1}", u)
        for i, d in enumerate(arrived):
            _ring(crop, d["x"] - x0, d["y"] - y0, ARRIVED,
                  "at rest" if len(arrived) == 1 else str(i + 1), u,
                  r=16, thick=u + 2)
        if not cv2.imwrite(str(path), crop,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
            return None
        return str(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("greenball: mark render failed: %s", exc)
        return None


def _zone_image(before_zoom, after_zoom, bdets, adets, rect, scale, path,
                rejects=None, hat=(None, None)):
    """One zone, before over after, at the zoom the detector ran at.

    THE PICTURE THE DETECTOR ACTUALLY SAW. A ball four pixels across
    in the frame is invisible in any view of the whole green, which
    makes "it found nothing here" unarguable with. At 3x it is a
    twelve-pixel disc and a person can say whether the detector was
    looking at a ball, a daisy or a sprinkler head.
    """
    try:
        ax, ay = rect[0], rect[1]
        h1, w1 = before_zoom.shape[:2]
        h2, w2 = after_zoom.shape[:2]
        W, H = max(w1, w2), h1 + h2 + 6
        pair = np.zeros((H, W, 3), np.uint8)
        pair[:h1, :w1] = before_zoom
        pair[h1 + 6:h1 + 6 + h2, :w2] = after_zoom
        u = max(1, int(round(max(W, H) / 700.0)))
        for d in bdets:
            _ring(pair, (d["x"] - ax) * scale, (d["y"] - ay) * scale,
                  WAS_THERE, "", u, r=12)
        for d in adets:
            _ring(pair, (d["x"] - ax) * scale,
                  (d["y"] - ay) * scale + h1 + 6, ARRIVED, "", u, r=12)
        # ── AND WHAT IT TURNED DOWN, IN GREY, WITH THE GATE ─────────
        #
        # A grey square exactly where the ball is says a gate is set
        # wrong and names which. No square there at all says the ball
        # never crossed the brightness threshold, which is an exposure
        # problem and not a gate's fault. Those need opposite fixes
        # and a picture with neither mark on it says only that
        # somebody should look at something else.
        for d in (rejects or [])[:MAX_REJECTS_PER_ZONE]:
            _rx = int((d["x"] - ax) * scale)
            _ry = int((d["y"] - ay) * scale) + h1 + 6
            cv2.rectangle(pair, (_rx - 9 * u, _ry - 9 * u),
                          (_rx + 9 * u, _ry + 9 * u), (150, 150, 150), u)
            _why = str(d.get("why") or "")
            _sc = 0.38 * u
            (_tw, _), _ = cv2.getTextSize(_why, cv2.FONT_HERSHEY_SIMPLEX,
                                          _sc, u)
            _lx = _rx + 12 * u
            if _lx + _tw > pair.shape[1] - 4 * u:
                _lx = max(4 * u, _rx - 12 * u - _tw)
            cv2.putText(pair, _why, (_lx, _ry + 4 * u),
                        cv2.FONT_HERSHEY_SIMPLEX, _sc, (170, 170, 170), u,
                        cv2.LINE_AA)
        cv2.putText(pair, "BEFORE", (8 * u, 20 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (235, 235, 235), u)
        _lbl = "AFTER"
        if hat and hat[0] is not None:
            _lbl += ("  -  brightest thing here is %d levels above its "
                     "surroundings (needs %d)" % (hat[0], hat[1] or 0))
        cv2.putText(pair, _lbl, (8 * u, h1 + 6 + 20 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (235, 235, 235), u)
        if not cv2.imwrite(str(path), pair,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
            return None
        return str(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("greenball: zone render failed: %s", exc)
        return None


def pair_for_picker(before_bgr, after_bgr, poly_pts, box, was_there,
                    arrived, path):
    """BEFORE | AFTER side by side, candidates numbered on the AFTER.

    THE SHAPE THE PICKER ALREADY EXPECTS. `ai_tracer.pick_ball_among`
    is asked "which numbered ring is the ball" of exactly this layout,
    and it is the only question a vision model answers reliably here:
    it knows WHAT it is looking at and estimates WHERE badly, which is
    why the position comes from the detector and only the choice comes
    from the model.

    Used only when more than one new thing arrived. One candidate is
    not a question.
    """
    try:
        x0, y0, x1, y1 = box
        b = before_bgr[y0:y1, x0:x1].copy()
        a = after_bgr[y0:y1, x0:x1].copy()
        ch, cw = a.shape[:2]
        u = max(1, int(round(max(cw * 2, ch) / 900.0)))
        pair = np.zeros((ch, cw * 2 + 4, 3), np.uint8)
        pair[:, :cw] = b
        pair[:, cw + 4:] = a
        if poly_pts is not None:
            _p = poly_pts.copy()
            _p[:, 0] -= x0
            _p[:, 1] -= y0
            for dx in (0, cw + 4):
                _q = _p.copy()
                _q[:, 0] += dx
                cv2.polylines(pair, [_q], True, (120, 230, 170), u)
        for d in was_there:
            _ring(pair, d["x"] - x0, d["y"] - y0, WAS_THERE, "", u, r=12)
        for i, d in enumerate(arrived):
            _ring(pair, d["x"] - x0 + cw + 4, d["y"] - y0, ARRIVED,
                  str(i + 1), u, r=14, thick=u + 2)
        cv2.putText(pair, "BEFORE", (8 * u, 22 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * u, (240, 240, 240), u)
        cv2.putText(pair, "AFTER", (cw + 12 * u, 22 * u),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * u, (240, 240, 240), u)
        if not cv2.imwrite(str(path), pair,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
            return None
        return str(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("greenball: picker pair render failed: %s", exc)
        return None
