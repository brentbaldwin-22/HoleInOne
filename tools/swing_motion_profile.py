#!/usr/bin/env python3
"""Profile the frame-to-frame motion signal a swing detector sees.

WHY THIS EXISTS. detect_swings_combined reports peak times and a
confidence, and on real tee footage it reported the two WEAKEST
candidates in the clip and missed the three actual swings. A detector
that returns the wrong answer tells you nothing about whether the
underlying signal is usable; this prints the signal itself so that
question can be answered separately from the peak-picking on top.

Measured on event-736 (Rivertowne, foursome on the tee, 120s @ 50fps),
ROI = 0.25..0.68 of frame height:

    t=  0s  swing          ratio 5.16
    t= 50s  swing          ratio 7.24
    t= 60s  swing          ratio 6.41
    t= 15s  people milling ratio 3.72
    quiet                  ratio ~1

So a swing is only about 2x a foursome moving about -- not the 10x the
"static scene, sudden burst" premise assumes. Three other players
walking, pulling clubs and taking practice swings is the noise floor,
and it is high.

ROI scoping beats whole-frame by 10-30% on THIS footage, where the
golfers are large in frame. It matters far more on a camera aimed down
the hole (events 914/1001), where the golfer is a few pixels and cloud
movement sets the floor.

    python3 tools/swing_motion_profile.py CLIP.mp4 [roi_top] [roi_bot]

roi_top/roi_bot are fractions of frame height; default 0.25 0.68.
"""

import sys
import cv2
import numpy as np

path = sys.argv[1]
roi_top = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
roi_bot = float(sys.argv[3]) if len(sys.argv) > 3 else 0.68
cap = cv2.VideoCapture(path)
fps = cap.get(cv2.CAP_PROP_FPS) or 50.0
H = 180
prev = None
whole, roi = [], []
while True:
    ok, f = cap.read()
    if not ok:
        break
    g = cv2.cvtColor(cv2.resize(f, (320, H)), cv2.COLOR_BGR2GRAY).astype(np.int16)
    if prev is not None:
        d = np.abs(g - prev)
        whole.append(d.mean())
        roi.append(d[int(H * roi_top):int(H * roi_bot), :].mean())
    prev = g
cap.release()

def smooth_ratio(sig):
    a = np.asarray(sig, float)
    k = max(1, int(round(fps * 0.15)))
    sm = np.convolve(a, np.ones(k) / k, mode="same")
    return sm / (float(np.median(sm)) or 1e-9)

rw, rr = smooth_ratio(whole), smooth_ratio(roi)
n = min(len(rw), len(rr))
bucket = 2.0
print("=== %s ===" % path.split("/")[-1][:40])
print("  t(s)   whole   ROI    (per %.0fs bucket, peak ratio; bar = ROI)" % bucket)
for b in range(int(n / fps / bucket) + 1):
    lo, hi = int(b * bucket * fps), int(min(n, (b + 1) * bucket * fps))
    if hi <= lo:
        continue
    w, r = rw[lo:hi].max(), rr[lo:hi].max()
    flag = " <<<" if r >= 4.0 else ""
    print("  %5.0f  %5.2f  %5.2f  %s%s"
          % (b * bucket, w, r, "#" * int(min(40, r * 4)), flag))
