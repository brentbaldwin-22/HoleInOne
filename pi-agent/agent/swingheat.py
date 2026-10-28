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


def build(clip_path, at_sec: float, *, before_sec: float = 0.5,
          after_sec: float = 0.5, crop=None, out_path=None):
    """Render the heat composite around `at_sec`. Path, or None.

    `at_sec` is a time WITHIN THE CLIP, not a wall clock -- an impact
    cut sits at a known offset from its own start and the caller knows
    it; this does not.

    Never raises. A composite that cannot be built means the clip
    uploads unjudged, which is the same outcome as not asking.
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
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
            idx = start_f - 1
            counted = 0
            while idx < end_f:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                idx += 1
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
            except Exception:  # noqa: BLE001
                pass

        dest = Path(out_path) if out_path else Path(
            str(clip_path) + ".heat.jpg")
        if not cv2.imwrite(str(dest), img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]):
            return None
        return dest
    except Exception as exc:  # noqa: BLE001
        log.warning("swingheat: could not build a composite (%s)", exc)
        return None


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
