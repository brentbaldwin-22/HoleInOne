"""A motion-heat composite of a swing, built on the rig.

WHY THE RIG AND NOT THE SERVER. The audio gate says a sharp loud sound
happened; it cannot say a club caused it. The judge that can read the
difference lives behind an API, and the question is where the picture
it reads gets made. Making it here is the only arrangement that saves
anything: a cut clip is ~7 MB over a modem that manages ~125 KB/s,
this composite is ~100 KB, and a verdict obtained BEFORE the upload
can stop 7 MB that nobody wanted. Obtained after, it saves nothing.

A COPY OF WHAT backend/app/services/tracer.swing_heat_check RENDERS,
and it has to stay one. The prompt on the other end describes this
picture in detail -- a compact hot figure, a thin cooler plume -- and
was tuned against images the server produced. A different colormap,
blend or scale here would hand the judge a picture its instructions no
longer describe, and the failure would look like the model getting
worse rather than the renderer drifting.

The agent never calls the model itself: the key stays on the server.
This builds the image; common.verify_swing asks.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("golfreelz_agent.swingheat")

# Mirrors tracer.py. Changing either without the other is the drift
# this module's docstring warns about.
BG_VAR_THRESHOLD = 30
DETECT_MAX_WIDTH = 1280
WARMUP_FRAMES = 12
JPEG_QUALITY = 80


# WHERE THE FRAME STRIP SAMPLES, in seconds from the impact. Late
# backswing, the strike, and two points of follow-through. The
# composite says WHERE something moved; these say WHAT it was, which
# is the question a heat blob cannot answer and a person can at a
# glance. Four, in a 2x2 grid, because /verify-swing carries one file
# and four tiles at ~640px each still read well.
FRAME_OFFSETS_SEC = (-0.25, 0.0, 0.15, 0.40)

# How much room to leave around the moving thing when cropping the
# strip to it. 1.0 would cut the club off at the top of the backswing.
FRAME_PAD_FRAC = 1.0

# Each tile in the strip, longest side. Big enough to see a club
# against sky, small enough that four of them are ~150-250 KB.
FRAME_TILE_PX = 640


def build(clip_path, at_sec: float, *, before_sec: float = 0.5,
          after_sec: float = 0.5, crop=None, out_path=None,
          meta=None, frames_out=None, prefer_xy=None):
    """Render the heat composite around `at_sec`. Path, or None.

    `meta`, when a dict is passed, is filled with what this pass
    measured -- `geo`, the swingjudge description of the hot regions
    in the picture the judge will actually see. Measured HERE because
    this is the one place the heat map exists before it is flattened
    into a colormapped JPEG; reading it back off the rendered image
    means reading the grass as well as the motion.

    `frames_out`, when a path is given, also writes a 2x2 strip of the
    ORIGINAL frames around the impact, cropped to whatever moved. See
    FRAME_OFFSETS_SEC.

    `at_sec` is a time WITHIN THE CLIP, not a wall clock -- an impact
    cut sits at a known offset from its own start and the caller knows
    it; this does not.

    Never raises. A composite that cannot be built means the clip
    uploads unjudged, which is the same outcome as not asking, and
    neither the measurement nor the strip may change that: both are
    best-effort additions inside the same try.
    """
    try:
        cap = cv2.VideoCapture(str(clip_path))
        if not cap.isOpened():
            return None
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if width <= 0 or height <= 0:
                return None
            det_scale = 1.0
            if width > DETECT_MAX_WIDTH:
                det_scale = DETECT_MAX_WIDTH / float(width)
            det_w = max(1, int(round(width * det_scale)))
            det_h = max(1, int(round(height * det_scale)))

            # WARM THE MODEL BEFORE THE WINDOW, not inside it. At 60fps
            # a half second is 30 frames and a 12-frame warmup would
            # eat a third of the swing.
            lead = max(WARMUP_FRAMES / max(fps, 1.0), 0.4)
            accum_f0 = max(0, int((at_sec - before_sec) * fps))
            start_f = max(0, int((at_sec - before_sec - lead) * fps))
            end_f = int((at_sec + after_sec) * fps)

            bg = cv2.createBackgroundSubtractorMOG2(
                history=150, varThreshold=BG_VAR_THRESHOLD,
                detectShadows=False,
            )
            heat = np.zeros((det_h, det_w), np.uint16)
            first_frame = None
            # ONE DECODE PASS FOR BOTH. Keeping the strip's frames as
            # they go by costs a handful of copies; a second pass over
            # the clip costs seconds, on the thread that is already
            # the bottleneck.
            want = {}
            if frames_out is not None:
                for off in FRAME_OFFSETS_SEC:
                    f = int((at_sec + off) * fps)
                    if f >= 0:
                        want[f] = off
            kept: dict = {}
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
            idx = start_f - 1
            counted = 0
            while idx < end_f:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                idx += 1
                if idx in want:
                    kept[want[idx]] = frame.copy()
                det = (cv2.resize(frame, (det_w, det_h))
                       if det_scale != 1.0 else frame)
                fg = bg.apply(det)
                if idx - start_f < WARMUP_FRAMES or idx < accum_f0:
                    continue
                if first_frame is None:
                    # THE FIRST FRAME OF THE WINDOW, not of the pass.
                    # The background the heat is drawn on should be the
                    # scene the swing happened in.
                    first_frame = frame.copy()
                heat += (fg > 0).astype(np.uint16)
                counted += 1
        finally:
            cap.release()

        if first_frame is None or counted <= 0:
            return None
        hm = np.log1p(heat.astype(np.float32))
        peak = float(hm.max())
        if peak <= 0:
            return None
        hm8 = np.clip(hm / peak * 255.0, 0, 255).astype(np.uint8)
        hm_n = cv2.resize(hm8, (width, height),
                          interpolation=cv2.INTER_LINEAR)
        color = cv2.applyColorMap(hm_n, cv2.COLORMAP_TURBO)
        img = first_frame
        blend = cv2.addWeighted(img, 0.4, color, 0.6, 0)
        on = hm_n > 0
        img[on] = blend[on]

        # THE CROP IS APPLIED TO BOTH, and the measurement comes after
        # it. What matters is the picture the judge is handed: a blob
        # that is half the cropped frame is a different fact from the
        # same blob in the whole hole, and measuring before the crop
        # would report the second while the model sees the first.
        ox, oy = 0, 0
        if crop:
            try:
                cx, cy, cw, ch = (int(crop[0]), int(crop[1]),
                                  int(crop[2]), int(crop[3]))
                x0 = max(0, min(width - 1, cx))
                y0 = max(0, min(height - 1, cy))
                x1 = max(x0 + 1, min(width, cx + cw))
                y1 = max(y0 + 1, min(height, cy + ch))
                # Zones are drawn round FEET; a crop too small to hold
                # a club at the top of a backswing is worse than none.
                if (x1 - x0) >= 160 and (y1 - y0) >= 160:
                    img = img[y0:y1, x0:x1].copy()
                    hm_n = hm_n[y0:y1, x0:x1]
                    ox, oy = x0, y0
            except Exception:  # noqa: BLE001
                pass

        if meta is not None:
            try:
                from . import swingjudge
                # WHICH BLOB IS THE GOLFER, not which is biggest.
                # See swingjudge.measure_heat: on clip 1734 a bystander
                # rummaging in a bag beat the actual swing by 27% of
                # area, and the crop, the frames and the stance gate
                # all then pointed at him.
                _pxy = prefer_xy
                if _pxy is not None and (ox or oy):
                    _pxy = (float(_pxy[0]) - ox, float(_pxy[1]) - oy)
                _geo = swingjudge.measure_heat(hm_n, prefer_xy=_pxy)
                # IN THE ORIGINAL FRAME'S PIXELS, because the caller's
                # other evidence -- the person detector's stance point
                # -- is in those, and comparing the two is the whole
                # point of the stance gate.
                _geo["crop_offset"] = (ox, oy)
                _m = _geo.get("main")
                if _m:
                    _m["frame_x"] = _m["x"] + ox
                    _m["frame_y"] = _m["y"] + oy
                    _m["frame_base"] = (_m["base"][0] + ox,
                                        _m["base"][1] + oy)
                meta["geo"] = _geo
                # AND ZOOM TO THE GOLFER, exactly as
                # tracer.swing_heat_check does. The zone crop is only
                # as tight as the zones are, and on a hole whose
                # trigger boxes are wide flat strips that leaves a
                # 70 px golfer in a 1920 px landscape. Headroom is not
                # symmetric: the club plume rises ABOVE the head and
                # is the single most important thing in the picture,
                # so a full blob height above and a third below.
                _mz = _geo.get("main")
                if _mz and _mz["w"] >= 8 and _mz["h"] >= 8:
                    _zh, _zw = img.shape[:2]
                    _px = int(_mz["w"] * 0.75)
                    _zx0 = max(0, _mz["x"] - _px)
                    _zx1 = min(_zw, _mz["x"] + _mz["w"] + _px)
                    _zy0 = max(0, _mz["y"] - int(_mz["h"] * 1.0))
                    _zy1 = min(_zh, _mz["y"] + _mz["h"]
                               + int(_mz["h"] * 0.33))
                    if (_zx1 - _zx0) >= 160 and (_zy1 - _zy0) >= 160:
                        img = img[_zy0:_zy1, _zx0:_zx1].copy()
                        meta["zoom"] = [_zx0 + ox, _zy0 + oy,
                                        _zx1 - _zx0, _zy1 - _zy0]
            except Exception as exc:  # noqa: BLE001
                log.debug("swingheat: could not measure the heat (%s)", exc)

        if frames_out is not None and kept:
            try:
                _box = None
                _m = (meta or {}).get("geo", {}).get("main")
                if _m:
                    _box = (_m.get("frame_x", _m["x"]),
                            _m.get("frame_y", _m["y"]), _m["w"], _m["h"])
                if _frame_strip(kept, _box, width, height, frames_out):
                    (meta if meta is not None else {})["frames"] = str(
                        frames_out)
            except Exception as exc:  # noqa: BLE001
                log.debug("swingheat: could not build a strip (%s)", exc)

        dest = Path(out_path) if out_path else Path(
            str(clip_path) + ".heat.jpg")
        if not cv2.imwrite(str(dest), img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]):
            return None
        return dest
    except Exception as exc:  # noqa: BLE001
        log.warning("swingheat: could not build a composite (%s)", exc)
        return None


def _frame_strip(kept: dict, box, width: int, height: int, out_path):
    """Write the original frames around impact as one 2x2 tile. Bool.

    WHY REAL FRAMES AT ALL. The composite is a one-second projection
    of motion: it says where something moved and throws away both WHEN
    and WHAT. A model asked "is this blob a club arc" is being asked a
    question about an abstraction; asked "is this person holding a
    golf club, and does the club move between these frames", it is
    being asked the thing it is actually good at. On 5 Nov the judge
    called a 150x199 near-square mass a golfer with a club, from a
    picture in which no club could be seen because no club was in it.

    CROPPED TO WHAT MOVED, not to the trigger zone. The zones on hole
    8 are wide flat strips, so zone_crop's 3x width overruns the frame
    and the composite still spans the whole hole -- a golfer 45 px
    tall in a 1920 px picture. Cropping to the blob puts the candidate
    across the tile instead, which also hands the model the scale the
    heat map never carried.

    One image rather than four because /verify-swing takes one file
    and a protocol change costs a coordinated rig update; four tiles
    at FRAME_TILE_PX still read comfortably.
    """
    if not kept:
        return False
    if box:
        bx, by, bw, bh = (int(box[0]), int(box[1]),
                          int(box[2]), int(box[3]))
        px, py = int(bw * FRAME_PAD_FRAC), int(bh * FRAME_PAD_FRAC)
        x0, y0 = max(0, bx - px), max(0, by - py)
        x1, y1 = min(width, bx + bw + px), min(height, by + bh + py)
        if x1 - x0 < 64 or y1 - y0 < 64:
            x0, y0, x1, y1 = 0, 0, width, height
    else:
        x0, y0, x1, y1 = 0, 0, width, height

    tiles = []
    for off in FRAME_OFFSETS_SEC:
        f = kept.get(off)
        if f is None:
            continue
        t = f[y0:y1, x0:x1]
        th, tw = t.shape[:2]
        if max(th, tw) > FRAME_TILE_PX:
            sc = FRAME_TILE_PX / float(max(th, tw))
            t = cv2.resize(t, (max(1, int(tw * sc)), max(1, int(th * sc))))
        # SAY WHICH MOMENT THIS IS. Four near-identical tiles in a grid
        # are unorderable, and the order is the evidence: the club has
        # to be in a different place in each.
        cv2.putText(t, "%+.2fs" % off, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(t, "%+.2fs" % off, (6, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(t)
    if not tiles:
        return False
    th = max(t.shape[0] for t in tiles)
    tw = max(t.shape[1] for t in tiles)
    cells = []
    for t in tiles:
        pad = np.zeros((th, tw, 3), np.uint8)
        pad[:t.shape[0], :t.shape[1]] = t
        cells.append(pad)
    while len(cells) < 4:
        cells.append(np.zeros((th, tw, 3), np.uint8))
    grid = np.vstack([np.hstack(cells[0:2]), np.hstack(cells[2:4])])
    return bool(cv2.imwrite(str(out_path), grid,
                            [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]))


def zone_crop(roi, boxes_fn):
    """A generous box round the trigger zones, or None.

    Grown because zones are drawn round FEET: the box that catches a
    stance is far too short to hold a club at the top of a backswing,
    so it grows upward most of all. None when no zones are drawn --
    a camera nobody has drawn boxes for gets the whole frame rather
    than a guessed rectangle.
    """
    try:
        zs = boxes_fn(roi) or []
        if not zs:
            return None
        x0 = min(int(z["x"]) for z in zs)
        y0 = min(int(z["y"]) for z in zs)
        x1 = max(int(z["x"]) + int(z["w"]) for z in zs)
        y1 = max(int(z["y"]) + int(z["h"]) for z in zs)
        w, h = x1 - x0, y1 - y0
        return (x0 - w, y0 - 3 * h, 3 * w, 4 * h)
    except Exception:  # noqa: BLE001
        return None
