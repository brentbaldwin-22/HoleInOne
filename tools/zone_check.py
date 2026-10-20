#!/usr/bin/env python3
"""Would these trigger zones have fired on this clip?

THE TRIGGER TESTS ONE POINT, and it is not the one people expect. The
person detector returns the CENTRE of its bounding box -- roughly
waist height -- and `_in_roi` asks whether that single point is inside
a zone. A box drawn around the tee SURFACE therefore catches nobody:
the golfer's feet are in it and the point being tested is above it.

Run this on the Pi against a clip and the zones you have drawn. It
reports every person the real detector finds, where it put the
centroid, and which zone (if any) would have claimed it -- so the
question stops being answered from screenshots.

    python3 tools/zone_check.py CLIP.mp4                  # zones from config
    python3 tools/zone_check.py CLIP.mp4 --every 10       # every 10th frame
    python3 tools/zone_check.py CLIP.mp4 --config /path/to/config.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

DEFAULT_CONFIG = "/opt/golfreelz-agent/config.yaml"


def _parse_zone(text: str) -> dict:
    """'Back 943,889 776x100' -> a box, the way the agent logs it."""
    parts = text.split()
    if len(parts) == 3:
        label, xy, wh = parts
    elif len(parts) == 2:
        label, (xy, wh) = "zone", parts
    else:
        raise SystemExit(f"cannot read zone {text!r} — want "
                         f"'Name X,Y WxH' as the agent log prints it")
    x, _, y = xy.partition(",")
    w, _, h = wh.lower().partition("x")
    return {"label": label, "x": int(x), "y": int(y),
            "w": int(w), "h": int(h)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    # THE ZONES LIVE ON THE BACKEND, not in config.yaml -- the file is
    # only the bootstrap a fresh agent uses before its first poll. So
    # the usual way to get them is to copy what the agent logged when
    # they arrived, which is this exact shape:
    #
    #   trigger zones updated from the backend: Back 943,889 776x100
    #
    # hence --zone 'Back 943,889 776x100', repeatable.
    ap.add_argument("--zone", action="append", default=[],
                    metavar="'NAME X,Y WxH'",
                    help="a zone as the agent log prints it; repeatable. "
                         "Overrides --config when given.")
    ap.add_argument("--frame", default="1920x1080",
                    help="frame size the zones were drawn against "
                         "(default 1920x1080)")
    ap.add_argument("--every", type=int, default=15,
                    help="check every Nth frame (default 15)")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop after this many checks (0 = whole clip)")
    args = ap.parse_args()

    import cv2
    import yaml

    from agent import tee as tee_mod
    from agent import tee_roi

    cfg = yaml.safe_load(open(args.config)) or {}
    det_cfg = (cfg.get("detection") or {})
    if args.zone:
        fw_s, _, fh_s = args.frame.partition("x")
        roi = {"frame": {"w": int(fw_s), "h": int(fh_s)},
               "boxes": [_parse_zone(z) for z in args.zone]}
    else:
        roi = cfg.get("roi") or cfg.get("trigger_zones") or {}

    # THE ZONES THE AGENT IS ACTUALLY USING come from the backend, not
    # the file -- the file is only the bootstrap. Say which was read,
    # because a check against the wrong rectangle is worse than none.
    boxes = tee_roi.boxes(roi)
    if not boxes:
        print(f"no zones in {args.config} — the agent takes its zones "
              f"from the backend on first poll, so this file may never "
              f"have held them. Copy them off the agent log instead:\n"
              f"  sudo journalctl -u golfreelz-agent "
              f"| grep 'trigger zones updated' | tail -1\n"
              f"then pass each one as --zone 'Back 943,889 776x100'.")
        return 2

    cap = cv2.VideoCapture(args.clip)
    if not cap.isOpened():
        print(f"could not open {args.clip}")
        return 2
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    print(f"clip {fw}x{fh} @ {fps:.1f}fps")

    _roi = tee_roi.scaled(roi, fw, fh) if fw and fh else roi
    print("zones (in this clip's pixels):")
    for b in tee_roi.boxes(_roi):
        y0, y1 = b["y"], b["y"] + b["h"]
        print(f"  {b.get('label') or 'zone':10s} "
              f"x {b['x']:5d}-{b['x']+b['w']:5d}   y {y0:5d}-{y1:5d}"
              f"   ({100*y0/fh:.0f}%-{100*y1/fh:.0f}% down frame)")

    agent = tee_mod.TeeAgent.__new__(tee_mod.TeeAgent)
    agent.det_cfg = det_cfg
    det = tee_mod.TeeAgent._build_detector(agent, int(det_cfg.get("width", 640)))
    if det is None:
        print("no detector available")
        return 2
    print(f"detector: {type(det).__name__}\n")

    f = 0
    checked = hits = inside = 0
    by_zone: dict = {}
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if f % max(1, args.every) == 0:
            checked += 1
            c = det.detect(frame)
            if c is not None:
                hits += 1
                which = None
                for b in tee_roi.boxes(_roi):
                    if (b["x"] <= c[0] <= b["x"] + b["w"]
                            and b["y"] <= c[1] <= b["y"] + b["h"]):
                        which = b.get("label") or "zone"
                        break
                if which:
                    inside += 1
                    by_zone[which] = by_zone.get(which, 0) + 1
                print(f"  f{f:6d}  t={f/fps:6.1f}s  centroid ({c[0]:5d},"
                      f"{c[1]:5d})  {100*c[1]/fh:5.1f}% down   "
                      f"{'IN ' + which if which else 'outside every zone'}")
            if args.max_frames and checked >= args.max_frames:
                break
        f += 1
    cap.release()
    print(f"\n{checked} frame(s) checked, a person found in {hits}, "
          f"{inside} of those inside a zone")
    for k, v in sorted(by_zone.items()):
        print(f"   {k}: {v}")
    if hits and not inside:
        print("\nNOBODY LANDED IN A ZONE. Compare the '% down frame' column "
              "above against the zone rows at the top: if the centroids sit "
              "ABOVE your zones, the boxes are drawn around the tee surface "
              "rather than around the golfer, and the point being tested is "
              "their waist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
