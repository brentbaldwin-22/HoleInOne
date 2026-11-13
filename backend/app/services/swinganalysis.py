"""One pipeline for "was that a swing", from six frames and a heat map.

WHY THIS FILE EXISTS. Every piece of judgement in this system used to
live twice: once in pi-agent/agent/swingheat.py, where the rig cropped
and tiled its own pictures, and once in tracer.swing_heat_check, where
Test Cut did the same from a video. The two were held in step by hand
and drifted anyway -- across one week the crop geometry, the frame
offsets, the foot measurement and the tile grid all diverged at least
once, and each divergence meant the rig and the bench were handing the
model different pictures and getting different answers about the same
strike.

So the thinking moves here and the rig stops thinking. It cuts, it
accumulates the motion heat over the window (which needs the video, so
it has to happen where the video is), it crops BOTH the heat map and
six raw frames to the trigger zone, and it posts them. Everything
after that -- who the golfers are, which one is hitting, where to
crop, what to ask, what the ball did, how to weigh three answers --
happens once, here, on a machine with the CPU for it.

WHAT THE CALLER MUST SUPPLY, and why it is this and not a clip:

  frames   six images, chronological, ALREADY cropped to `crop`
  heat     the motion composite over the window, cropped the same way
  geo      measure_heat's reading of that composite, in crop pixels
  crop     where that crop sits in the original frame
  zones    the trigger boxes, in original frame pixels

The crop is the shared coordinate space. Everything the rig sends is
in it, everything this returns is in original-frame pixels, and the
one conversion between them happens in one place.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from . import swingjudge as _sj
from .ball_vanish import ball_in_frames
from .people import find_people_in_frames
from .tracer import (
    AUDIO2_OFFSETS_SEC, FRAME_TILE_PX, draw_zones, grid_of_tiles,
)

log = logging.getLogger(__name__)

# The pads that shape the crop round one golfer. Named here rather
# than buried: a bit over two thirds of a body width either side, a
# twelfth of a body height below the feet, a tenth above the head.
# See tracer.swing_heat_check, which these were measured for.
SIDE_PAD_FRAC = 0.69
BELOW_FEET_FRAC = 0.08
ABOVE_HEAD_FRAC = 0.10


# THE SMALLEST CROP WORTH JUDGING, in pixels. Below this there is
# nothing for a model to look at and nothing for a detector to find,
# and at zero `cv2.imwrite` raises rather than returning False.
MIN_CROP_PX = 8


def _write(path, img) -> bool:
    """Write a picture, or say it could not be written. Never raises.

    `cv2.imwrite` RETURNS False for an unwritable path and RAISES for
    an empty image, which is the worst of both and the reason a single
    bad crop could take down a whole verdict. One guard, used for
    every picture in this file.
    """
    try:
        if img is None or getattr(img, "size", 0) == 0:
            return False
        return bool(cv2.imwrite(str(path), img,
                                [int(cv2.IMWRITE_JPEG_QUALITY), 80]))
    except Exception as exc:  # noqa: BLE001
        log.warning("swinganalysis: could not write %s: %s", path, exc)
        return False


def _crop_rect(golfer, floor_y, heads, bounds):
    """(x0, y0, x1, y1) for the heat and for the frames, in crop px.

    EITHER MAY BE None, when the golfer's box does not land on the
    picture -- see `_usable`. The caller must not judge a picture it
    could not crop.
    """
    W, H = bounds
    pad = max(12, int(golfer["w"] * SIDE_PAD_FRAC))
    x0 = max(0, golfer["x"] - pad)
    x1 = min(W, golfer["x"] + golfer["w"] + pad)
    ground = golfer["y"] + golfer["h"]
    if floor_y is not None:
        ground = max(ground, int(floor_y))
    bottom = min(H, ground + max(6, int(golfer["h"] * BELOW_FEET_FRAC)))
    # ABOVE EVERY HEAD IN THE CROP, not just the picked golfer's: the
    # blob is motion and the moving part can be the arms while the
    # torsos stand still. See tracer.swing_heat_check.
    top = min([p["y"] for p in heads] or [golfer["y"]])
    top = max(0, top - max(8, int(golfer["h"] * ABOVE_HEAD_FRAC)))
    # The heat runs to the top of the picture, because a club at the
    # top of a backswing and a ball leaving both go up out of any box
    # drawn round a stance. The frames stop just above the heads.
    return (_usable(x0, 0, x1, bottom, W, H),
            _usable(x0, top, x1, bottom, W, H))


def _usable(x0, y0, x1, y1, W, H):
    """A rect that can actually be cropped with, or the whole picture.

    THE GOLFER'S BOX IS IN CROP PIXELS AND NEED NOT BE INSIDE THE
    CROP. People are detected in the whole frame and their boxes are
    shifted by the crop's offset, so a person standing outside the
    crop comes through here with coordinates off either end. Then
    `x0 = max(0, x - pad)` lands at or past W while `x1 = min(W, ...)`
    is W -- or `bottom` lands at or below zero -- and the rect is
    empty.
    
    An empty rect makes an empty crop, and an empty crop reaches
    `cv2.imwrite`, which does not return False for it. It raises:

        (-215:Assertion failed) !_img.empty() in function 'imwrite'

    That lands in analyse()'s outer catch and the whole strike comes
    back "could not tell — analysis failed", with an OpenCV assertion
    where the reason should be. Reported from the field on #1467, four
    audio triggers, every one of them lost.

    RETURNS None RATHER THAN THE WHOLE PICTURE, and the difference
    cost a day of audio triggers. Falling back to the frame stopped
    the crash and quietly changed the ANSWER: the judge was handed a
    whole 640x480 frame while `golfer_crop` still said True, rated it
    not-a-swing -- correctly, there is no swing to see at that scale --
    and `verify_drops` on the rig deleted the clip. Before the crash
    was fixed the same input returned "could not tell" and the clip
    went up unjudged, which is the right outcome and the one to keep.

    A golfer whose box does not land on the picture being analysed
    means the person detector and the crop disagree about where the
    frame is. That is a broken analysis, not a weak one, and the
    caller turns it into "could not tell". The no-golfer branch is
    different and unchanged: nobody was detected, the whole frame is
    genuinely all there is to judge, and its verdicts have always
    counted.
    """
    # THE INTERSECTION WITH THE PICTURE IS THE WHOLE TEST, and it is
    # the clamp as well. Judging the rect on its own size instead let
    # a ten-pixel box sitting entirely off the corner survive, because
    # ten is bigger than the minimum -- it then clamped to an
    # eight-pixel sliver of the corner, which crops without raising
    # and shows nobody anything. What matters is how much of the rect
    # is actually ON the picture.
    ix0 = max(0, int(x0))
    iy0 = max(0, int(y0))
    ix1 = min(int(W), int(x1))
    iy1 = min(int(H), int(y1))
    if ix1 - ix0 < MIN_CROP_PX or iy1 - iy0 < MIN_CROP_PX:
        return None
    return (ix0, iy0, ix1, iy1)


def analyse(frames, heat, geo, *, crop, zones, offsets=None,
            ratio=None, attack_ms=None, out_dir=None, prefix="verify",
            judge=True, audio2=False, model=None) -> dict:
    """Judge one strike. Never raises; always returns a dict.

    `frames` are BGR images in chronological order and `heat` the
    motion composite, all cropped to `crop` = (x, y, w, h) in the
    original frame. `geo` is measure_heat's reading of that
    composite. `zones` are the trigger boxes in original-frame pixels.

    Returns the verdict plus everything needed to argue with it: who
    was detected, which golfer was cropped to and why, what the ball
    did, and the filenames of the pictures the judges saw.
    """
    out = {
        "is_swing": None, "confidence": None, "reason": None,
        "heat_is_swing": None, "frames_is_swing": None,
        "ball_left": None, "ball_why": None, "ball_spot": None,
        "picked_front": None, "front_candidates": None,
        "golfer_crop": False, "decided_by": None,
        "image": None, "frames_image": None,
        "image_zones": None, "frames_image_zones": None,
    }
    try:
        shots = [f for f in (frames or []) if f is not None]
        if not shots or heat is None:
            out["reason"] = "no pictures to judge"
            return out
        offs = list(offsets or AUDIO2_OFFSETS_SEC)[:len(frames)]
        ox, oy = int(crop[0]), int(crop[1])
        H, W = heat.shape[:2]

        # ---- who is on the tee ---------------------------------
        order = sorted(zip(offs, frames), key=lambda t: (abs(t[0]), t[0]))
        people = find_people_in_frames(
            [(o, f) for o, f in order if f is not None])
        # Into ORIGINAL-FRAME pixels, once, here: zones are in them
        # and so is everything this returns.
        for p in people:
            p["box"] = [p["box"][0] + ox, p["box"][1] + oy,
                        p["box"][2], p["box"][3]]
            p["feet"] = [p["feet"][0] + ox, p["feet"][1] + oy]

        # ---- which of them is hitting --------------------------
        blobs = [b for b in ([geo.get("main")] + list(geo.get("others") or []))
                 if b and b.get("w", 0) >= 8 and b.get("h", 0) >= 8]
        pf = _sj.pick_front_golfer(blobs, zones, offset=(ox, oy),
                                   people=people)
        front, floor_y, why = pf.get("pick"), pf.get("floor"), pf["why"]
        if front is None and pf.get("close") and people:
            mt = _sj.break_tie_on_motion(people, blobs, offset=(ox, oy))
            if mt.get("pick"):
                front = mt["pick"]
                why = "%s; %s" % (pf["why"], mt["why"])
                for c in (pf.get("considered") or []):
                    c["picked"] = c["feet"] == list(front.get("feet") or [])
                floor_y = None
                fx, fy = front["feet"][0], front["feet"][1]
                for z in zones or []:
                    if (z[0] <= fx <= z[0] + z[2]
                            and z[1] - z[3] <= fy <= z[1] + 2 * z[3]):
                        floor_y = z[1] + z[3]
                        break
        out["picked_front"] = "%s — %s" % (pf.get("from") or "?", why)
        out["front_candidates"] = pf.get("considered")
        out["golfer_crop"] = front is not None
        # ── THE GEOMETRY, IN ORIGINAL-FRAME PIXELS, AS NUMBERS ──────
        #
        # `picked_front` is a sentence for a human and `front_candidates`
        # is a list to argue with; neither is a thing a later run can
        # USE. The produce needs the golfer's box and the floor of the
        # zone they are standing in, and it was re-deriving both with
        # a fresh MOG2 pass and a fresh person detector over the same
        # seconds of the same clip, minutes later, to reach the same
        # answer. Numbers, named, so they can simply be handed over.
        out["golfer_box"] = (None if front is None else
                             [int(v) for v in front["box"]])
        out["golfer_feet"] = (None if front is None else
                              [int(front["feet"][0]), int(front["feet"][1])])
        out["zone_floor_y"] = (None if floor_y is None else int(floor_y))
        out["n_people"] = len(people)

        # ---- the two pictures ----------------------------------
        if front is not None:
            g = {"x": front["box"][0] - ox, "y": front["box"][1] - oy,
                 "w": front["box"][2], "h": front["box"][3]}
            heads = [{"y": p["box"][1] - oy} for p in people
                     if g["x"] - 40 <= p["box"][0] - ox
                     <= g["x"] + g["w"] + 40] or [g]
            hrect, frect = _crop_rect(
                g, None if floor_y is None else floor_y - oy, heads, (W, H))
            # COULD NOT CROP TO THE GOLFER WE PICKED, so we cannot
            # answer. Judging the whole frame instead would be a
            # confident verdict on a picture nobody chose: at frame
            # scale there is no swing to see, the judge says so, and
            # with `verify_drops` on the rig that DELETES the strike.
            # "Could not tell" uploads it unjudged, which is what this
            # input did before `_usable` existed. See `_usable`.
            if hrect is None or frect is None:
                out["golfer_crop"] = False
                out["reason"] = (
                    "could not tell — the golfer picked at %dx%d sits off "
                    "the %dx%d picture being analysed, so there was no crop "
                    "to judge. The clip is kept and uploaded unjudged."
                    % (g["x"], g["y"], W, H))
                log.warning(
                    "swinganalysis: golfer box (%d,%d %dx%d) does not land "
                    "on the %dx%d analysed crop — returning 'could not "
                    "tell' rather than judging the whole frame",
                    g["x"], g["y"], g["w"], g["h"], W, H)
                return out
        else:
            hrect = frect = (0, 0, W, H)
        hx0, hy0, hx1, hy1 = hrect
        judged = heat[hy0:hy1, hx0:hx1].copy()
        # BELT AND BRACES. `_usable` clamps the rect to the heat's own
        # dimensions, so this should not be reachable -- but an empty
        # `judged` is what reaches cv2.imwrite and RAISES, taking the
        # whole verdict with it, and the cost of being sure is one
        # comparison.
        if judged.size == 0:
            judged = heat.copy()
        if max(judged.shape[:2]) < 512 and min(judged.shape[:2]) > 0:
            sc = 512.0 / max(judged.shape[:2])
            judged = cv2.resize(judged,
                                (int(judged.shape[1] * sc),
                                 int(judged.shape[0] * sc)),
                                interpolation=cv2.INTER_CUBIC)
        tiles, used = [], []
        fx0, fy0, fx1, fy1 = frect
        for off, f in zip(offs, frames):
            if f is None:
                continue
            t = f[fy0:fy1, fx0:fx1]
            if t.size == 0:
                continue
            th, tw = t.shape[:2]
            if max(th, tw) > FRAME_TILE_PX:
                sc = FRAME_TILE_PX / float(max(th, tw))
                t = cv2.resize(t, (max(1, int(tw * sc)), max(1, int(th * sc))))
            lbl = "%+.2fs" % off
            for col, wid in (((0, 0, 0), 3), ((255, 255, 255), 1)):
                cv2.putText(t, lbl, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, col, wid, cv2.LINE_AA)
            tiles.append(t)
            used.append(off)

        # ---- what the ball did ---------------------------------
        if front is not None:
            anchor = (front["feet"][1] - oy, front["box"][3])
            ppl_crop = [{"box": [p["box"][0] - ox, p["box"][1] - oy,
                                 p["box"][2], p["box"][3]]} for p in people]
            bl = ball_in_frames([f for f in frames],
                                rect=(fx0, fy0, fx1, fy1),
                                people=ppl_crop, anchor=anchor)
            out["ball_left"] = bl["left"]
            out["ball_why"] = bl["why"]
            out["ball_spot"] = (None if not bl["spot"] else
                                [bl["spot"][0] + ox, bl["spot"][1] + oy])
        else:
            out["ball_why"] = ("no zoom crop on this one, so the ball was "
                               "not looked for")

        # ---- write the pictures --------------------------------
        #
        # ONE UNWRITABLE PICTURE MUST NOT COST THE VERDICT. `imwrite`
        # does not return False on an empty image, it RAISES -- and
        # every one of these calls sat inside the function's outer
        # catch, so an empty crop came back as "could not tell —
        # analysis failed: OpenCV(5.0.0) ... (-215:Assertion failed)
        # !_img.empty() in function 'imwrite'". That is an assertion
        # where the reason should be, on a strike nobody can now
        # judge. Seen in the field on #1467: four audio triggers, all
        # four lost to it.
        if out_dir:
            d = Path(out_dir)
            hn, fn = f"{prefix}-heat.jpg", f"{prefix}-frames.jpg"
            if _write(d / hn, judged):
                out["image"] = hn
            else:
                out["picture_why"] = (
                    "the heat crop came out empty, so there is no picture "
                    "for the judges to look at")
            if tiles:
                if _write(d / fn, grid_of_tiles(tiles)):
                    out["frames_image"] = fn
            # THE OPERATOR'S COPIES, with the zones, the measured feet
            # and the ball drawn on. Never what the judges see.
            _ball = ({"spot": [out["ball_spot"][0] - ox,
                               out["ball_spot"][1] - oy]}
                     if out["ball_spot"] else None)
            _feet = []
            for c in (out["front_candidates"] or []):
                _feet.append(dict(c, feet=[c["feet"][0], c["feet"][1]],
                                  box=list(c["box"])))
            hz = draw_zones(judged, zones, offset=(ox + hx0, oy + hy0),
                            scale=(judged.shape[1] / float(max(1, hx1 - hx0))),
                            feet=_feet,
                            ball=None if not _ball else
                            {"spot": [_ball["spot"][0] + ox,
                                      _ball["spot"][1] + oy]})
            if hz is not None and _write(d / f"{prefix}-heat-zones.jpg", hz):
                out["image_zones"] = f"{prefix}-heat-zones.jpg"
            if tiles:
                zt = []
                for t, off in zip(tiles, used):
                    sc = t.shape[1] / float(max(1, fx1 - fx0))
                    a = draw_zones(t, zones, offset=(ox + fx0, oy + fy0),
                                   scale=sc, feet=_feet,
                                   ball=None if not out["ball_spot"] else
                                   {"spot": out["ball_spot"]})
                    zt.append(a if a is not None else t)
                if _write(d / f"{prefix}-frames-zones.jpg",
                          grid_of_tiles(zt)):
                    out["frames_image_zones"] = f"{prefix}-frames-zones.jpg"

        # ---- and the verdict -----------------------------------
        #
        # NO PICTURE, NO VERDICT -- BUT SAY WHICH. This used to fall
        # straight through with `reason` still None, so an unwritable
        # crop and a model that genuinely could not decide produced
        # the same "could not tell" with nothing to tell them apart.
        if judge and out_dir and not out["image"]:
            out["reason"] = (out.get("picture_why")
                             or "no picture could be written for the "
                                "judges to look at")
        if judge and out_dir and out["image"]:
            from .ai_tracer import (
                judge_swing_audio2, judge_swing_heat_image,
                judge_swing_with_frames,
            )
            d = Path(out_dir)
            if audio2 and out["frames_image"]:
                ja = judge_swing_audio2(
                    d / out["frames_image"], d / out["image"],
                    ratio=ratio, attack_ms=attack_ms, offsets=used,
                    ball_left=out["ball_left"], model=model)
                out.update({k: ja.get(k) for k in
                            ("classification", "golfer_visible",
                             "swing_motion_visible", "impact_alignment",
                             "confidence_pct")})
                out["is_swing"] = ja.get("is_swing")
                out["confidence"] = ja.get("confidence")
                out["reason"] = ja.get("reason")
                out["decided_by"] = "audio 2"
                return out
            jh = judge_swing_heat_image(d / out["image"], model=model)
            out["heat_is_swing"] = jh.get("is_swing")
            out["heat_reason"] = jh.get("reason")
            out["heat_confidence"] = jh.get("confidence")
            jf = {}
            if out["frames_image"]:
                jf = judge_swing_with_frames(
                    d / out["frames_image"], d / out["image"], model=model)
                out["frames_is_swing"] = jf.get("is_swing")
                out["frames_reason"] = jf.get("reason")
                out["frames_confidence"] = jf.get("confidence")
            dv = _sj.decide_verdict(out["heat_is_swing"],
                                    out["frames_is_swing"],
                                    out["ball_left"], ratio)
            out["is_swing"] = dv["is_swing"]
            out["confidence"] = dv["confidence"]
            out["reason"] = dv["why"]
            out["decided_by"] = dv["how"]
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("swinganalysis: %s", exc)
        out["reason"] = "analysis failed: %s" % exc
        return out
