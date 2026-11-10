"""The descent, tracked backwards from a ball we have already found.

THE OLD SEARCH ASKED THE HARDEST VERSION OF THE QUESTION. It segmented
every frame of a window of green footage, built every chain it could
out of everything that moved, and then tried to pick the ball out of
them with gates -- on a green ringed with brush, where the wind moves
more of the frame than the ball does. It worked often enough to be
worth keeping and not often enough to be trusted, and the gates that
made it stricter were the gates that lost real descents.

This asks the easy version instead. We now MEASURE where the ball
finished (see greenball), so the descent is not a search for an
unknown object among many: it is a walk backwards from a known one.
At every step there is a position, a velocity and a small window, and
the only question is which of the few bright things in that window is
the ball that was there a frame ago. A branch waving in the treeline
is never in the window.

NO BALL AT REST, NO DESCENT. That is the whole contract. The previous
behaviour -- hunt the green and attach whatever best survived the
gates -- is what produced landings on clips where the shot missed the
green entirely, and a drawn comet arriving somewhere the ball never
went is worse than no comet at all.

WHY BACKWARDS. Forwards, the ball enters the frame small, fast and
somewhere unknown, which is the problem the old search could not
solve. Backwards, it starts stationary, at a pixel we measured, and
every step is a short extrapolation from the step before. The
uncertainty grows as it climbs, which is the right direction: by the
time the window is wide the ball is high against plain sky with
nothing near it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .ball_vanish import BALL_MAX_AREA, _detections
from .greenball import GREEN_BALL_MIN_AREA

log = logging.getLogger("golfreelz.greendescent")

# HOW FAR BACK TO WALK, in seconds. A ball is visible falling onto a
# green for about a second; the rest of this is the bounce and the
# roll, which are part of the same chain and worth having. Past about
# four seconds the ball is above the frame or behind the treeline and
# the walk is extrapolating into nothing.
MAX_BACK_SEC = 4.0

# ...AND HOW LONG THE BALL MAY HAVE BEEN SITTING THERE FIRST.
#
# THE BUDGET WAS BEING SPENT ON THE REST. The walk starts at the
# frame the ball was MEASURED at, which is wherever the green window
# ladder found it -- eight, fourteen, twenty seconds after the
# strike. The ball has been lying still for most of that, so four
# seconds of walking back never reached the descent: on a real clip
# it reported "241 points, falling 0px over 240 frames" and stopped
# at the budget, having watched a stationary ball the whole way.
#
# A stationary frame costs one small window and finds the ball
# exactly where it was, so it is nearly free. The four-second budget
# is for the part that matters -- the fall, the bounce and the roll
# -- and it starts counting from the frame the ball is first seen to
# have moved.
MAX_STILL_SEC = 30.0

# HOW MANY FRAMES ARE HELD IN MEMORY AT ONCE. The walk is backwards
# and decoders are forwards, so the clip is read in chunks: one seek
# per chunk, the chunk read forwards, then walked in reverse. Seeking
# per frame is what made the first plate search take minutes.
CHUNK_FRAMES = 24

# THE WINDOW THE DETECTOR SEARCHES, in original pixels, and how it
# grows. It starts small because a ball at rest is exactly where it
# was a frame ago, and widens with the ball's own speed plus a margin
# for every frame since it was last seen.
WINDOW_BASE_PX = 48
WINDOW_PER_SPEED = 3.0
WINDOW_PER_MISS_PX = 26
WINDOW_MAX_PX = 420

# ...AND HOW FAR FROM THE PREDICTION A DETECTION MAY SIT and still be
# the same ball, as a fraction of the window. Looser than the window
# would allow at its edge: the window is where to look, this is what
# counts as a match.
MATCH_FRAC = 0.5

# EVERY ZONE IS SCALED TO THIS before the detector runs. The whole
# reason the green needed its own treatment is that a ball a hundred
# yards out is three or four pixels across and the top-hat's 15px
# kernel swallows it. Capped so a small window is not interpolated
# into mush.
ZOOM_TARGET_PX = 640
ZOOM_MAX = 6.0

# HOW MANY FRAMES IN A ROW MAY FIND NOTHING before the walk gives up.
# A ball passes behind the flagstick, a player, or its own shadow on
# the way down; it does not vanish for half a second.
#
# WAS 8, AND IT GAVE UP ON THE REST RATHER THAN THE DESCENT. On a
# real clip the walk reported "147 points walked back from the ball
# at rest, falling -1px over 146 frames; stopped because lost it for
# 8 frames in a row" -- a hundred and forty-six frames of a ball
# sitting perfectly still, and then it stopped. It never reached the
# descent at all.
#
# Two things were wrong. A stationary ball holds the velocity
# estimate at zero, so the window stays at its 48px minimum right up
# to the moment the ball was rolling -- and then the first frame of
# the roll is a step the small window cannot cover. And eight frames
# is an eighth of a second at 60fps, which is less than a ball
# spends behind a flagstick.
MAX_MISSES = 20

# ...AND THE WALK DOES NOT GIVE UP WHILE THE BALL HAS NEVER MOVED.
# Giving up during the rest is giving up before the question has
# been asked: the whole point of walking back is to reach the part
# where it was moving. Until the chain has travelled this far from
# where it started, a run of misses widens the window and keeps
# going rather than ending the walk.
STILL_PX = 12.0

# HOW WIDE THE WINDOW OPENS WHILE THE BALL IS STILL. A ball at rest
# is exactly where it was, so the window is small -- which is right
# until the frame the roll begins, where it is the reason the ball
# is lost. Through a stationary run the window grows anyway, so the
# first moving frame has somewhere to be found.
WINDOW_STILL_GROWTH_PX = 14

# HOW FAST THE BALL MUST STILL BE FALLING, in pixels per frame, for a
# point to count as part of the fall rather than the roll. Used only
# to name the pitch mark -- the chain itself is reported whole.
PITCH_MIN_DY = 2.5

# HOW MANY SEARCH WINDOWS ARE DRAWN. One picture per frame would be a
# flipbook nobody reads; eight spread across the chain shows the ball
# at rest, mid-roll, at the bounce and in the air.
MAX_WINDOW_TILES = 8

# HOW MANY ZOOMED WINDOWS ARE HELD IN MEMORY WHILE THE WALK RUNS.
#
# THIS WAS UNBOUNDED, AND IT IS THE LIKELIEST REASON A LONG CLIP CAME
# BACK WITH NOTHING. Eight tiles are drawn, so the loop kept a tile
# per frame and `_window_tiles` picked eight at the end -- which is
# fine over a 60-frame fall and not fine over the rest. A 48px window
# at 6x is 576x576 of uint8, 332KB; the still budget is 30 seconds,
# which at 60fps is 1,800 frames, which is 600MB of grayscale crops
# held for the length of the walk. On a box already holding the
# decoder, the plates and the frontend's clips that is an allocation
# failure, and an allocation failure in here surfaces as the generic
# "the reverse descent walk failed" -- a sentence that says nothing
# about memory and sent five rounds of debugging at the detector.
#
# So the list is thinned as it grows: at the cap every second tile is
# dropped and the keep-stride doubles, which costs resolution in the
# middle of a long walk and keeps the spread the eight pictures are
# chosen from. The cap is in tiles, not megabytes, because the tile
# size is fixed by ZOOM_TARGET_PX.
MAX_TILES_HELD = 96

# AND HOW MANY PER-FRAME LOG ROWS SURVIVE INTO THE REPORT. The log is
# the answer to "what did it actually look at" and it is per frame,
# so it is thinned the same way. The counts beside it are NOT
# thinned: they are totals over every frame the walk looked at, which
# is what the question "did it ever see the ball" needs.
MAX_LOG_ROWS = 300


def _detect_in_window(gray, cx, cy, half):
    """Ball-shaped things in a zoomed window. ORIGINAL-pixel results.

    THE SIZE GATE IS HELD IN THE FRAME'S PIXELS. `_detections` applies
    BALL_MIN_AREA in the pixels of the image it is handed, and the
    image here has been scaled up; without correcting for that, every
    re-quantised speck on a mowing stripe is a ball. Same lesson, same
    line, as greenball.
    """
    h, w = gray.shape[:2]
    x0 = int(max(0, cx - half))
    y0 = int(max(0, cy - half))
    x1 = int(min(w, cx + half))
    y1 = int(min(h, cy + half))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return [], None, 1.0, (x0, y0, x1, y1)
    sub = gray[y0:y1, x0:x1]
    sh, sw = sub.shape[:2]
    scale = min(ZOOM_MAX, ZOOM_TARGET_PX / float(max(sw, sh)))
    if scale > 1.02:
        sub = cv2.resize(sub, (int(sw * scale), int(sh * scale)),
                         interpolation=cv2.INTER_CUBIC)
    else:
        scale = 1.0
    zh, zw = sub.shape[:2]
    out = []
    # THE BOUNDS GO IN SCALED, rather than the results coming out and
    # being re-judged. `_detections` gates in the pixels of the image
    # it is handed, so at 6x a 50px ball is 1800px of area and dies on
    # a MAX_AREA written for 400 -- inside the detector, before this
    # function ever sees it. Judging the result afterwards cannot
    # recover something that was already thrown away.
    _s2 = scale * scale
    for dx, dy, area, contrast in _detections(
            sub, [(0, 0, zw, zh)], rel=0.0,
            min_area=GREEN_BALL_MIN_AREA * _s2,
            max_area=BALL_MAX_AREA * _s2,
            kernel_px=15 * scale):
        out.append({"x": x0 + dx / scale, "y": y0 + dy / scale,
                    "area": round(float(area) / _s2, 1),
                    "contrast": round(float(contrast), 1)})
    return out, sub, scale, (x0, y0, x1, y1)


def _chunk(cap, f0, n, total):
    """`n` grayscale frames from f0 forwards. One seek, then read."""
    f0 = max(0, int(f0))
    n = int(max(1, n))
    if total:
        n = min(n, max(1, total - f0))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    out = []
    for _ in range(n):
        ok, fr = cap.read()
        if not ok or fr is None:
            break
        out.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    return out


def _thin(seq, keep_every, cap):
    """Append-side thinning: halve the list, double the stride.

    HOLDING EVERYTHING AND CHOOSING AT THE END ONLY WORKS IF
    EVERYTHING FITS. It does not here -- see MAX_TILES_HELD. Dropping
    the oldest would keep the end of the walk and throw away the
    descent, which is the part being looked for; dropping every second
    keeps the whole span at half the density, and halving again keeps
    it at a quarter. Returns the new stride.
    """
    if len(seq) < cap:
        return keep_every
    del seq[1::2]
    return keep_every * 2


def track_descent_back(green_path, rest_xy, rest_frame: int, *,
                       max_back_sec: float = MAX_BACK_SEC,
                       out_dir=None, stem="greendescent") -> dict:
    """Walk backwards from the ball at rest and report what it finds.

    Returns {ok, points, n_points, first_frame, last_frame, fall_px,
    pitch_frame, pitch_xy, why, windows, image}. `points` are EARLIEST
    FIRST -- the order the ball flew in -- each
    {frame, x, y, area, contrast, found}; a point with found=False was
    extrapolated across a frame that saw nothing and is carried so the
    gap is visible rather than closed silently.

    Never raises.
    """
    out: dict = {"ok": False, "points": [], "n_points": 0,
                 "first_frame": None, "last_frame": int(rest_frame),
                 "fall_px": 0, "pitch_frame": None, "pitch_xy": None,
                 "why": None, "windows": [], "image": None,
                 "n_missed": 0, "moved_at_frame": None,
                 # ── WHAT THIS ACTUALLY DOES, SAID OUT LOUD ────────
                 #
                 # Asked directly, five rounds in: is it zooming, is
                 # it every frame or every few, and is the detector
                 # MOG, a model or brightness. All three were
                 # answerable only by reading this file, which means
                 # the report was not showing its work. They are
                 # constants, so they cost nothing to carry, and a
                 # number that disagrees with the prose is a bug the
                 # report now reveals rather than hides.
                 "detector": {
                     "kind": "brightness",
                     "how": ("white top-hat — each pixel against the "
                             "median of its own neighbourhood, so a "
                             "ball is kept for being brighter than "
                             "the grass immediately around it, not "
                             "brighter than some threshold. Then four "
                             "gates: area, roundness, how filled the "
                             "blob is, and local contrast. The SAME "
                             "function that finds a ball at address "
                             "on the tee and a ball at rest on the "
                             "green — ball_vanish._detections."),
                     "not": ("no MOG2 and no background subtractor — "
                             "the camera pans and a subtractor on a "
                             "panning green lights up the whole "
                             "treeline. No model and no AI anywhere "
                             "in this walk: nothing here is asked, "
                             "everything is measured."),
                     "zoom": ("every window is scaled so its long "
                              "edge is %dpx, capped at %gx — a ball a "
                              "hundred yards out is three pixels "
                              "across and the top-hat's kernel would "
                              "swallow it whole. The kernel and the "
                              "area bounds are scaled with it, which "
                              "is the thing that was wrong the first "
                              "three times: a fixed kernel at 6x "
                              "returns the ball's RIM and fragments "
                              "it into eighty blobs."
                              % (ZOOM_TARGET_PX, ZOOM_MAX)),
                     "cadence": ("every frame, backwards, one window "
                                 "per frame — no stride and no "
                                 "sampling. The clip is read in "
                                 "chunks of %d so there is one seek "
                                 "per chunk rather than one per "
                                 "frame; the chunk is decoded "
                                 "forwards and walked in reverse."
                                 % CHUNK_FRAMES),
                 },
                 # ── AND WHAT IT SAW, FRAME BY FRAME ───────────────
                 #
                 # The chain says where the ball went. This says what
                 # was in the window on every frame, including the
                 # frames the chain has nothing on -- which is where
                 # a walk that "still is not working" actually goes
                 # wrong, and the one thing none of the five previous
                 # rounds could see.
                 "log": [], "log_every": 1,
                 "counts": {}}
    cap = None
    try:
        if not rest_xy:
            out["why"] = "no ball at rest to walk back from"
            return out
        cap = cv2.VideoCapture(str(green_path))
        if not cap.isOpened():
            out["why"] = "could not open the green clip"
            return out
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        f_end = int(max(0, min(int(rest_frame),
                               (total - 1) if total else int(rest_frame))))
        # THE FAR FLOOR covers the rest AND the flight; the tight one
        # is applied from the frame the ball is first seen to move.
        f_start = max(0, f_end - int(round(
            (float(max_back_sec) + MAX_STILL_SEC) * fps)))
        _moving_floor = None

        px, py = float(rest_xy[0]), float(rest_xy[1])
        vx = vy = 0.0
        misses = 0
        # Newest first; reversed at the end so the chain reads in the
        # order the ball actually flew.
        chain = [{"frame": f_end, "x": px, "y": py, "area": None,
                  "contrast": None, "found": True, "window": None}]
        # THE LAST PLACE IT WAS ACTUALLY SEEN, which is what velocity
        # must be measured from. Measuring it from the running
        # position instead reads the EXTRAPOLATION ERROR as motion:
        # after a miss the running position has drifted, so the next
        # hit computes a velocity equal to that drift, the next
        # prediction overshoots, and the walk settles into a limit
        # cycle that finds the ball once every few frames and invents
        # the rest. Measured on a fixture with a stationary ball: 26
        # found and 66 extrapolated before this, a clean run after.
        seen_f, seen_x, seen_y = f_end, px, py
        # HAS IT MOVED AT ALL YET? Until it has, a run of misses is
        # not evidence the walk is lost -- it is evidence the ball is
        # still sitting there and the detector blinked.
        _moved = False
        _start_x, _start_y = px, py
        tiles: list = []
        _tile_every = 1
        _log: list = []
        _log_every = 1
        # ── THE TOTALS, WHICH ARE NEVER THINNED ────────────────────
        #
        # "did it ever see the ball" and "was the ball in the window
        # but too far from where the walk was pointing" are different
        # failures with different fixes, and neither is visible in a
        # chain. `n_too_far` is the important one: candidates existed
        # and every one was beyond MATCH_FRAC of the window, which
        # means the prediction is wrong rather than the detector
        # blind, and the fix is the velocity model, not the gates.
        _ct = {"frames": 0, "with_cands": 0, "cands": 0, "taken": 0,
               "none_at_all": 0, "too_far": 0, "nearest_too_far_px": None,
               "widest_window_px": 0, "min_zoom": None, "max_zoom": None}
        _stop = None

        f = f_end - 1
        while f >= f_start:
            c0 = max(f_start, f - CHUNK_FRAMES + 1)
            frames = _chunk(cap, c0, f - c0 + 1, total)
            if not frames:
                _stop = "the clip would not read that far back"
                break
            for idx in range(len(frames) - 1, -1, -1):
                fi = c0 + idx
                if fi > f:
                    continue
                gray = frames[idx]
                h, w = gray.shape[:2]
                # WHERE IT WAS A FRAME AGO. Reverse step: subtract the
                # velocity rather than add it.
                qx, qy = px - vx, py - vy
                _speed = (vx * vx + vy * vy) ** 0.5
                # ...PLUS ROOM FOR A BALL THAT HAS NOT MOVED YET.
                # See STILL_PX: the roll begins at some frame, and
                # that frame is the one the minimum window cannot
                # cover.
                _still = (not _moved) and len(chain) > 4
                half = int(min(WINDOW_MAX_PX,
                               WINDOW_BASE_PX + WINDOW_PER_SPEED * _speed
                               + WINDOW_PER_MISS_PX * misses
                               + (WINDOW_STILL_GROWTH_PX * min(20, len(chain))
                                  if _still else 0)))
                dets, sub, scale, box = _detect_in_window(gray, qx, qy, half)
                best, bd = None, None
                _lim = MATCH_FRAC * half
                for d in dets:
                    _dd = ((d["x"] - qx) ** 2 + (d["y"] - qy) ** 2) ** 0.5
                    if _dd > _lim:
                        continue
                    # NEAREST THE PREDICTION, then brightest. On a
                    # green the ball is the brightest small round
                    # thing in a window this size, but a prediction
                    # that is already good should not be dragged onto
                    # a brighter thing further away.
                    _sc = (_dd, -d["contrast"])
                    if bd is None or _sc < bd:
                        best, bd = d, _sc
                # ── BOOK IT BEFORE ACTING ON IT ────────────────────
                #
                # Counted here rather than in the two branches below,
                # because the branch that finds nothing is the branch
                # that matters and it is the one that kept getting
                # less reporting than the other.
                _near = None
                for d in dets:
                    _dd = ((d["x"] - qx) ** 2 + (d["y"] - qy) ** 2) ** 0.5
                    if _near is None or _dd < _near:
                        _near = _dd
                _ct["frames"] += 1
                _ct["cands"] += len(dets)
                _ct["widest_window_px"] = max(_ct["widest_window_px"],
                                              int(half) * 2)
                if scale:
                    _ct["min_zoom"] = (scale if _ct["min_zoom"] is None
                                       else min(_ct["min_zoom"], scale))
                    _ct["max_zoom"] = (scale if _ct["max_zoom"] is None
                                       else max(_ct["max_zoom"], scale))
                if dets:
                    _ct["with_cands"] += 1
                else:
                    _ct["none_at_all"] += 1
                if best is not None:
                    _ct["taken"] += 1
                elif dets:
                    _ct["too_far"] += 1
                    if _near is not None:
                        _ct["nearest_too_far_px"] = (
                            round(_near, 1)
                            if _ct["nearest_too_far_px"] is None
                            else min(_ct["nearest_too_far_px"],
                                     round(_near, 1)))
                _log.append({
                    "frame": int(fi),
                    "looked_at": [int(round(qx)), int(round(qy))],
                    "window_px": int(half) * 2,
                    "zoom": round(float(scale), 2),
                    "match_within_px": round(float(_lim), 1),
                    "n_cands": len(dets),
                    "nearest_px": None if _near is None else round(_near, 1),
                    "took": (None if best is None else
                             [int(round(best["x"])), int(round(best["y"]))]),
                    "area": None if best is None else best.get("area"),
                    "contrast": (None if best is None
                                 else best.get("contrast")),
                    "why": ("took the nearest of %d" % len(dets)
                            if best is not None else
                            ("nothing bright and ball-shaped in the "
                             "window" if not dets else
                             "%d candidate(s), nearest %.1fpx away, "
                             "match limit %.1fpx — the window was "
                             "pointing at the wrong place, not blind"
                             % (len(dets), _near or 0.0, _lim))),
                })
                _log_every = _thin(_log, _log_every, MAX_LOG_ROWS)
                if best is not None:
                    # PER FRAME, OVER THE REAL GAP. `seen_f - fi` is
                    # how many frames back this detection is from the
                    # last one that was actually seen, which is 1 in
                    # the normal case and more across a hide.
                    _gap = max(1, seen_f - fi)
                    _nvx = (seen_x - best["x"]) / _gap
                    _nvy = (seen_y - best["y"]) / _gap
                    # SMOOTHED, because one two-pixel centroid is
                    # noisy and the window is placed from it. Heavier
                    # on the new value than the old, so a real bounce
                    # is followed rather than averaged away.
                    vx = 0.65 * _nvx + 0.35 * vx
                    vy = 0.65 * _nvy + 0.35 * vy
                    px, py = best["x"], best["y"]
                    seen_f, seen_x, seen_y = fi, px, py
                    misses = 0
                    if not _moved and (
                            (px - _start_x) ** 2 + (py - _start_y) ** 2
                            > STILL_PX * STILL_PX):
                        _moved = True
                        out["moved_at_frame"] = int(fi)
                        # FROM HERE THE REAL BUDGET APPLIES. Walking
                        # back from the first moving frame is the
                        # thing four seconds was ever meant to cover.
                        _moving_floor = max(
                            f_start,
                            int(fi) - int(round(float(max_back_sec) * fps)))
                    chain.append({"frame": fi, "x": px, "y": py,
                                  "area": best["area"],
                                  "contrast": best["contrast"],
                                  "found": True, "window": list(box)})
                else:
                    misses += 1
                    px, py = qx, qy
                    chain.append({"frame": fi, "x": px, "y": py,
                                  "area": None, "contrast": None,
                                  "found": False, "window": list(box)})
                    if misses > MAX_MISSES and _moved:
                        _stop = ("lost it for %d frames in a row, after "
                                 "it had started moving" % MAX_MISSES)
                        break
                    if misses > MAX_MISSES * 3:
                        _stop = ("lost it for %d frames in a row without "
                                 "ever seeing it move — the ball sat "
                                 "still for the whole window, so the "
                                 "descent is further back than %.1fs"
                                 % (MAX_MISSES * 3, max_back_sec))
                        break
                if out_dir is not None and sub is not None:
                    tiles.append((fi, sub, scale, box,
                                  best, bool(best is not None)))
                    _tile_every = _thin(tiles, _tile_every, MAX_TILES_HELD)
                if not (0 <= px < w and 0 <= py < h):
                    _stop = "the walk left the frame"
                    break
                if _moving_floor is not None and fi <= _moving_floor:
                    _stop = ("%.1fs of walking back from the frame it "
                             "started moving" % max_back_sec)
                    break
            if _stop:
                break
            f = c0 - 1
            if not _moved and (f_end - f) > int(MAX_STILL_SEC * fps):
                _stop = ("%.0fs of walking back and the ball never "
                         "moved — it was already at rest when this "
                         "window begins" % MAX_STILL_SEC)
                break

        # TRIM THE TAIL OF MISSES. A chain that ends in eight
        # extrapolated points ends eight frames after it stopped
        # knowing anything, and the first of those points is where the
        # picture should stop.
        while chain and not chain[-1]["found"]:
            chain.pop()
        chain.reverse()
        out["points"] = chain
        out["n_points"] = len(chain)
        out["n_missed"] = sum(1 for q in chain if not q["found"])
        # ── THE WORK GOES OUT ON BOTH BRANCHES ─────────────────────
        #
        # ESPECIALLY THE ONE THAT FAILED. Three separate stages in
        # this system have now computed their pictures and their
        # reasons inside the success branch and dropped them on the
        # failure branch -- exactly the branch somebody is reading
        # them for. Attached here, above the early return, so there
        # is no branch left for it to fall off.
        out["log"] = _log
        out["log_every"] = _log_every
        _ct["log_every"] = _log_every
        _ct["frames_per_second_looked_at"] = round(float(fps), 1)
        _ct["stopped_because"] = _stop
        for _k in ("min_zoom", "max_zoom"):
            if _ct.get(_k) is not None:
                _ct[_k] = round(float(_ct[_k]), 2)
        out["counts"] = _ct
        # AND A SENTENCE FOR THE TOP OF THE BOX, built from the
        # totals rather than from the chain, so it is true even when
        # the chain is one point long.
        out["work"] = (
            "%d frame(s) looked at, every frame, backwards; windows "
            "%d-%dpx wide at %sx-%sx zoom; %d candidate(s) in %d "
            "frame(s); %d taken, %d frame(s) had nothing bright and "
            "ball-shaped at all, %d had candidates that were all too "
            "far from where the walk was pointing%s"
            % (_ct["frames"], WINDOW_BASE_PX * 2, _ct["widest_window_px"],
               _ct.get("min_zoom"), _ct.get("max_zoom"),
               _ct["cands"], _ct["with_cands"], _ct["taken"],
               _ct["none_at_all"], _ct["too_far"],
               ("" if _ct.get("nearest_too_far_px") is None else
                " (nearest such was %.1fpx away)"
                % _ct["nearest_too_far_px"])))
        if len(chain) < 2:
            out["why"] = ("the ball at rest was found but nothing before "
                          "it was — " + (_stop or "no earlier frame "
                                         "matched"))
            if out_dir is not None and tiles:
                # THE WINDOWS, ON THE BRANCH THAT FOUND NOTHING. The
                # question here is "what was in the window", and
                # these are the only pictures that answer it.
                out["windows"] = _window_tiles(tiles, Path(out_dir), stem)
            return out
        out["first_frame"] = int(chain[0]["frame"])
        out["fall_px"] = int(round(chain[-1]["y"] - chain[0]["y"]))
        # THE PITCH MARK: the FIRST time it stopped falling.
        #
        # Which is not the last time, and the difference is a whole
        # bounce. A ball comes down, hits, leaps, comes down again and
        # then rolls; "the last frame it was falling fast" is the end
        # of the bounce, several feet from where it actually pitched.
        # What everything downstream calls the landing is the first
        # contact -- the mark on the green -- so that is what is
        # named here.
        #
        # Found by walking the chain forward: once it has been falling
        # at PITCH_MIN_DY or more for a few frames, the first frame
        # that is not falling is the contact.
        _pitch = None
        _falling = 0
        for i in range(1, len(chain)):
            _dy = chain[i]["y"] - chain[i - 1]["y"]
            if _dy >= PITCH_MIN_DY:
                _falling += 1
                continue
            if _falling >= 3:
                _pitch = i - 1
                break
            _falling = 0
        if _pitch is not None:
            out["pitch_frame"] = int(chain[_pitch]["frame"])
            out["pitch_xy"] = [int(round(chain[_pitch]["x"])),
                               int(round(chain[_pitch]["y"]))]
        out["ok"] = True
        out["why"] = (
            "%d point(s) walked back from the ball at rest, falling "
            "%dpx over %d frame(s)%s%s"
            % (len(chain), out["fall_px"],
               int(chain[-1]["frame"]) - int(chain[0]["frame"]),
               ("; %d extrapolated across frames that saw nothing"
                % out["n_missed"]) if out["n_missed"] else "",
               ("; stopped because " + _stop) if _stop else ""))

        if out_dir is not None:
            out["windows"] = _window_tiles(tiles, Path(out_dir), stem)
            out["image"] = _chain_image(green_path, chain, out,
                                        Path(out_dir), stem)
    except Exception as exc:  # noqa: BLE001
        log.warning("greendescent: failed: %s", exc)
        out["why"] = f"the reverse descent walk failed: {exc}"
    finally:
        if cap is not None:
            cap.release()
    return out


def _window_tiles(tiles, out_dir, stem):
    """A handful of the search windows, at the zoom they were searched.

    THE PICTURE THE DETECTOR ACTUALLY SAW. A ball four pixels across
    is invisible in any view of a whole green, which makes "it lost it
    here" unarguable with. These are spread across the chain rather
    than taken from its start, so one tile shows the ball at rest, one
    mid-roll, one at the bounce and one in the air.
    """
    rows = []
    try:
        if not tiles:
            return rows
        step = max(1, len(tiles) // MAX_WINDOW_TILES)
        picked = tiles[::step][:MAX_WINDOW_TILES]
        for fi, sub, scale, box, best, found in picked:
            img = cv2.cvtColor(sub, cv2.COLOR_GRAY2BGR)
            u = max(1, int(round(max(img.shape[:2]) / 400.0)))
            if best is not None:
                cv2.circle(img,
                           (int((best["x"] - box[0]) * scale),
                            (int((best["y"] - box[1]) * scale))),
                           int(10 * u), (255, 210, 60), u + 1, cv2.LINE_AA)
            cv2.putText(img, "f%d %s" % (fi, "found" if found else "NOTHING"),
                        (6 * u, 18 * u), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45 * u, (235, 235, 235) if found else (90, 170, 255),
                        u, cv2.LINE_AA)
            _p = out_dir / f"{stem}-win-f{fi}.jpg"
            if cv2.imwrite(str(_p), img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
                rows.append({"frame": int(fi), "found": bool(found),
                             "rect": [int(v) for v in box],
                             "zoom": round(float(scale), 2),
                             "path": str(_p)})
    except Exception as exc:  # noqa: BLE001
        log.warning("greendescent: window tiles failed: %s", exc)
    return rows


def _chain_image(green_path, chain, info, out_dir, stem):
    """The whole walk on one frame of the green."""
    try:
        cap = cv2.VideoCapture(str(green_path))
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(chain[-1]["frame"]))
        ok, im = cap.read()
        cap.release()
        if not ok or im is None:
            return None
        u = max(1, int(round(max(im.shape[:2]) / 600.0)))
        for a, b in zip(chain, chain[1:]):
            cv2.line(im, (int(a["x"]), int(a["y"])),
                     (int(b["x"]), int(b["y"])),
                     (255, 210, 60) if (a["found"] and b["found"])
                     else (150, 150, 150), u + 1, cv2.LINE_AA)
        for q in chain:
            if q["found"]:
                cv2.circle(im, (int(q["x"]), int(q["y"])), 2 * u,
                           (255, 210, 60), -1, cv2.LINE_AA)
        if info.get("pitch_xy"):
            cv2.drawMarker(im, tuple(info["pitch_xy"]), (90, 230, 120),
                           cv2.MARKER_TILTED_CROSS, 18 * u, u + 1)
            cv2.putText(im, "pitch mark",
                        (info["pitch_xy"][0] + 12 * u,
                         info["pitch_xy"][1] - 8 * u),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (90, 230, 120),
                        u, cv2.LINE_AA)
        cv2.circle(im, (int(chain[-1]["x"]), int(chain[-1]["y"])), 12 * u,
                   (70, 170, 255), u + 1, cv2.LINE_AA)
        cv2.putText(im, "at rest",
                    (int(chain[-1]["x"]) + 16 * u, int(chain[-1]["y"])),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (70, 170, 255), u,
                    cv2.LINE_AA)
        cv2.putText(im, str(info.get("why") or ""), (10 * u,
                    im.shape[0] - 10 * u), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45 * u, (255, 255, 255), u, cv2.LINE_AA)
        _p = out_dir / f"{stem}-chain.jpg"
        if not cv2.imwrite(str(_p), im,
                           [int(cv2.IMWRITE_JPEG_QUALITY), 86]):
            return None
        return str(_p)
    except Exception as exc:  # noqa: BLE001
        log.warning("greendescent: chain image failed: %s", exc)
        return None
