#!/usr/bin/env python3
"""Prove an RTSP camera can drive the agent's own person detector.

Opens the stream through `agent.common.open_camera` -- the same function
the Pi uses, now that it understands rtsp:// -- and runs the same
YoloPersonDetector the tee agent runs. If a person in front of the
camera lights this up, the capture half of the port is done and only the
recording and upload path is left.

    export GOLFREELZ_CAM_PASSWORD=...
    python3 tools/test_rtsp_detect.py \
        --rtsp 'rtsp://admin:{password}@10.0.0.249/profile1/media.smp'
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "pi-agent"
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / "detect.jpg"))
    args = ap.parse_args()

    import cv2
    from agent.common import open_camera
    from agent.tee import YoloPersonDetector

    model = ROOT / "models" / "yolov8n.onnx"
    print(f"model      : {model} ({'found' if model.exists() else 'MISSING'})")
    if not model.exists():
        return 2

    print("opening    : rtsp stream via the agent's open_camera()")
    cap = open_camera({"device": args.rtsp})
    det = YoloPersonDetector(model, input_size=320, conf_threshold=args.conf,
                             min_box_area_frac=0.0)
    print(f"detector   : YOLOv8n, conf>={args.conf}")
    print(f"watching   : {args.seconds:.0f}s — stand in front of the camera\n")

    t0 = time.time()
    frames = hits = 0
    best = None
    while time.time() - t0 < args.seconds:
        ok, frame = cap.read()
        if not ok:
            print("  read failed — stream dropped")
            break
        frames += 1
        c = det.detect(frame)
        if c is not None:
            hits += 1
            if best is None:
                best = (frame.copy(), c)
                print(f"  PERSON at pixel {c[0]},{c[1]}  (frame {frames})")
    cap.release()

    print(f"\nframes read      : {frames}")
    print(f"frames w/ person : {hits}")
    if best is not None:
        img, (x, y) = best
        cv2.circle(img, (int(x), int(y)), 18, (0, 200, 255), 3)
        cv2.line(img, (int(x) - 30, int(y)), (int(x) + 30, int(y)), (0, 200, 255), 2)
        cv2.line(img, (int(x), int(y) - 30), (int(x), int(y) + 30), (0, 200, 255), 2)
        cv2.imwrite(args.out, img)
        print(f"marked frame     : {args.out}")
        print("\nPASS — the agent's detector works on this camera's stream.")
    else:
        print("\nNo person detected. Either nobody was in frame, or the "
              "camera is pointed at a wall. Re-run standing in view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
