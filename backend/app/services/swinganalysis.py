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

# THE CROP RULE LIVES IN ONE PLACE, and this is not it: see
# services/swingcrop. `tracer.swing_heat_check` computes the same crop
# for the Test cut page, and while there were two copies a fix to
# either one left the other wrong -- which is how the same bug came to
# be reported twice, once against the rig path and once against the
# page used to check the rig path.
#
# Re-exported under the old private names so the rest of this file and
# its tests read as they did.
from .swingcrop import (                                # noqa: E402
    SIDE_PAD_FRAC, BELOW_FEET_FRAC, ABOVE_HEAD_FRAC, MIN_CROP_PX,
    ZONE_PAD_FRAC,
    usable as _usable,
    crop_rect as _crop_rect,
    zone_rect as _zone_rect_pair,
    zoned_person as _zoned_person,
)


def _zone_rect(zones, ox, oy, W, H, heads=None):
    """The zone crop's HEAT rect. See swingcrop.zone_rect."""
    return _zone_rect_pair(zones, ox, oy, W, H, heads=heads)[0]


def _heat_map_from(geo, ox, oy, W, H):
    """(zx, zy, sx, sy) for turning FRAME pixels into HEAT pixels.

    THE TWO PICTURES ARE NOT THE SAME PICTURE, and this file assumed
    they were for weeks. The rig crops the raw frames to the trigger
    zone and crops the HEAT composite twice -- the zone, then a
    tighter rect around the golfer -- and upscales the result when it
    is small. So a person detected in the frames has coordinates that
    mean nothing in the heat.

    Event #1505: "the golfer picked at 1168x558 sits off the 881x998
    picture being analysed". The golfer was found correctly, in the
    frames, at a position the heat picture does not contain. The clip
    went up unjudged, which is the safe answer and not a useful one.

    `geo["zoom"]` is that second rect in frame pixels and
    `geo["zoom_px"]` the size it ended up, both sent by the rig. With
    them the map is exact. WITHOUT them -- an older rig -- this falls
    back to the crop offset and no scaling, which is what it always
    did, so nothing gets worse while the rigs catch up.
    """
    _z = (geo or {}).get("zoom")
    _zp = (geo or {}).get("zoom_px")
    try:
        if (_z and len(_z) == 4 and float(_z[2]) > 0 and float(_z[3]) > 0):
            _pw, _ph = ((float(_zp[0]), float(_zp[1]))
                        if _zp and len(_zp) == 2 else (float(W), float(H)))
            return (float(_z[0]), float(_z[1]),
                    _pw / float(_z[2]), _ph / float(_z[3]))
    except (TypeError, ValueError, IndexError):
        pass
    return (float(ox), float(oy), 1.0, 1.0)


def _head_boxes(people, zx, zy, sx=1.0, sy=1.0):
    """Everybody detected, as head dicts in CROP pixels.

    EVERYBODY, not a pre-filtered few: `swingcrop.head_line` decides
    who is in the crop, and it has to see them all to do it. This used
    to filter here with a flat 40 px, which is one thing on a golfer
    90 px tall and another on one 400 px tall.
    """
    return [{"x": (p["box"][0] - zx) * sx, "y": (p["box"][1] - zy) * sy,
             "w": p["box"][2] * sx}
            for p in (people or []) if p and p.get("box")]
# SMALLEST A FRAME TILE MAY BE SHOWN AT, on its long side. The tiles
# used only ever to be shrunk -- `FRAME_TILE_PX` is a ceiling -- which
# was fine while a crop meant a golfer who filled it. A fallback crop
# around a distant figure can be 120px across, and handing a judge a
# 120px picture with a 20px caption burned into the corner wastes the
# zoom that produced it. Upscaling adds no detail; it stops the detail
# already there from being thrown away by whatever resamples next, and
# it gives the caption somewhere to sit. The heat picture beside it has
# had the same floor, at 512, all along.
FRAME_TILE_MIN_PX = 384


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
        "golfer_crop": False, "decided_by": None, "zoom_from": None,
        "heat_map_from": None,
        "frames_used": None,
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
        # FRAME PIXELS -> HEAT PIXELS. Not the identity, and assuming
        # it was is what sent #1505 up unjudged. See _heat_map_from.
        _zx, _zy, _sx, _sy = _heat_map_from(geo, ox, oy, W, H)
        out["heat_map_from"] = (
            "the crop offset (no zoom rect from the rig)"
            if (_sx == 1.0 and _sy == 1.0 and _zx == ox and _zy == oy)
            else "the rig's zoom rect at %.2fx%.2f" % (_sx, _sy))

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
            mt = _sj.break_tie_on_motion(people, blobs, offset=(ox, oy),
                                         zoned=pf.get("considered"))
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
            g = {"x": (front["box"][0] - _zx) * _sx,
                 "y": (front["box"][1] - _zy) * _sy,
                 "w": front["box"][2] * _sx, "h": front["box"][3] * _sy}
            heads = _head_boxes(people, _zx, _zy, _sx, _sy) or [g]
            hrect, frect = _crop_rect(
                g,
                None if floor_y is None else (floor_y - _zy) * _sy,
                heads, (W, H))
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
            # ── NOBODY WAS PICKED. ZOOM ANYWAY. ─────────────────────
            #
            # This used to hand the judge the whole frame, and on a
            # wide course picture that is a golfer thirty pixels tall
            # somewhere in a panorama. The judge says "not a swing",
            # correctly -- there is no swing visible at that scale --
            # and with `verify_drops` on the rig the clip is deleted.
            # Reported from the field as "I thought we were zooming in
            # on the golfer": we were, but only down the branch where
            # somebody's feet landed in a zone.
            #
            # So the crop falls back a rung at a time instead of all
            # the way to the frame. Each rung is a worse guess at
            # WHERE the swing is and every one of them is a better
            # picture than the whole course.
            _alt = _zoned_person(pf.get("considered"))
            if _alt is not None:
                _g = {"x": (_alt["box"][0] - _zx) * _sx,
                      "y": (_alt["box"][1] - _zy) * _sy,
                      "w": _alt["box"][2] * _sx, "h": _alt["box"][3] * _sy}
                _heads = _head_boxes(people, _zx, _zy, _sx, _sy) or [_g]
                _fl = _alt.get("floor")
                hrect, frect = _crop_rect(
                    _g,
                    None if _fl is None else (float(_fl) - _zy) * _sy,
                    _heads, (W, H))
                _from = ("a person standing in a zone that the picker "
                         "would not call — not confirmed as the golfer")
            else:
                hrect, frect = _zone_rect_pair(
                    zones, _zx, _zy, W, H,
                    heads=_head_boxes(people, _zx, _zy, _sx, _sy))
                # TWO DIFFERENT THINGS, AND THE OPERATOR READS THIS
                # LINE. "Nobody was detected" and "the people detected
                # were not on the tee" send them to look in different
                # places -- at the detector, or at where the zones are
                # drawn -- and only one of them is true at a time.
                _n = len(pf.get("considered") or [])
                _from = ("the trigger zones, since nobody was detected "
                         "at all" if not _n else
                         "the trigger zones — %d person(s) were found but "
                         "none were standing in one" % _n)
            # A RUNG THAT DID NOT WORK IS NOT A VERDICT. Unlike the
            # picked-golfer branch above, there is nothing to be wrong
            # about here: no golfer was identified, so judging the
            # whole frame is the behaviour this case has always had
            # and its verdicts have always counted. Fall through to it
            # rather than refuse to answer.
            if hrect is None or frect is None:
                hrect = frect = (0, 0, W, H)
            else:
                out["zoom_from"] = _from
                out["picked_front"] = "%s · zoomed to %s" % (
                    out["picked_front"], _from)
                log.info(
                    "swinganalysis: no golfer picked — zoomed to %s "
                    "(%dx%d of %dx%d) rather than judging the whole frame",
                    _from, frect[2] - frect[0], frect[3] - frect[1], W, H)
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
            elif 0 < max(th, tw) < FRAME_TILE_MIN_PX:
                # A small crop is a CLOSE one -- see FRAME_TILE_MIN_PX.
                sc = FRAME_TILE_MIN_PX / float(max(th, tw))
                t = cv2.resize(t, (max(1, int(tw * sc)), max(1, int(th * sc))),
                               interpolation=cv2.INTER_CUBIC)
            lbl = "%+.2fs" % off
            for col, wid in (((0, 0, 0), 3), ((255, 255, 255), 1)):
                cv2.putText(t, lbl, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, col, wid, cv2.LINE_AA)
            tiles.append(t)
            used.append(off)
        # WHICH MOMENTS THE JUDGE ACTUALLY SAW. The six offsets are
        # what was ASKED for; a frame that could not be had, or a crop
        # that sliced to nothing, drops out silently above. A verdict
        # argued from four tiles and one argued from six are not the
        # same verdict, and until now nothing downstream could tell
        # them apart.
        out["frames_used"] = [round(float(u), 2) for u in used]

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
        elif out.get("zoom_from"):
            # THERE IS A CROP NOW, which there did not use to be, so
            # the old line ("no zoom crop on this one") is no longer
            # true. What is missing is not the picture but the ANCHOR:
            # `ball_in_frames` measures a departure against a confirmed
            # golfer's feet and height, and a figure that was never
            # confirmed as the golfer gives it a ruler it cannot trust.
            out["ball_why"] = ("zoomed to %s, but the ball was not looked "
                               "for — that needs a confirmed golfer to "
                               "measure against" % out["zoom_from"])
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
