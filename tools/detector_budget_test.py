#!/usr/bin/env python3
"""Can an operator SEE what the detector was held to?

THE NUMBERS EXISTED AND NOBODY DREW THEM. `sweep_ascents` has counted
the per-frame budget's saturation since the cap became a gate --
cap_hit_frames, cap_hit_frac, dropped_to_cap, max_hits_in_a_frame, plus
the band it searched and where that band's floor came from. Every one
of them was returned to the browser and none was rendered anywhere.

What that cost: a flight on upload 1280 came back as a 7-point chain
with 25 detections around it, and three separate theories were formed
and tested from screenshots -- corridor width, threshold, a stale axis
-- before anyone noticed the run had already measured which of the
three possible causes it was. The three need opposite work:

  the budget bound on most frames -> the ball lost a slot before any
      chain gate saw it; no gate tuning can reach it.
  it bound on none, few detections -> the ball never cleared threshold
      in the band, or the band does not contain the flight.
  it bound on none, many detections -> the detector saw it and the
      chain gates are the place to look.

So this test pins the pairing, both halves: the sweep SETS the keys on
a real run, and the page READS them. Either half alone is what was
already true while the numbers were invisible.

    python3 tools/detector_budget_test.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []

# WHAT THE PANEL NEEDS. Not every key the sweep returns -- only the ones
# that answer "which of the three cases is this", which is the question
# the panel exists for.
NEEDED = [
    "n_dets",
    "scanned_frames",
    "per_frame_cap",
    "cap_hit_frames",
    "cap_hit_frac",
    "dropped_to_cap",
    "max_hits_in_a_frame",
    "merged_away",
    "merge_px",
    "ghost_px",
    "three_frame",
    "corridor_px",
    "band",
    "band_floor_source",
    "tee_line_y",
    # THE SIZE SPREAD, so ascent_prefer_area can be chosen off the
    # footage. Measured in tools/ascent_band_traffic_test.py: within 2x
    # of the ball's real blob area it holds the ball on every frame,
    # off by three it loses it on every frame. It is the strongest gate
    # here and the only one nobody could set, because the number was
    # computed and never drawn.
    "area_profile",
]

PAGE = ROOT / "frontend" / "src" / "pages" / "AdminProduction.jsx"


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def synth(path, frames, blobs_per_frame, size=6):
    """A clip where N things move every frame, so saturation is a number
    we chose rather than one we hope for."""
    import numpy as np
    import cv2

    W, H = 640, 360
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         30.0, (W, H))
    for f in range(frames):
        im = np.zeros((H, W, 3), dtype="uint8")
        for b in range(blobs_per_frame):
            x = 20 + (b * 37 + f * 5) % (W - 40)
            y = 20 + (b * 23) % (H - 40)
            cv2.rectangle(im, (x, y), (x + size, y + size),
                          (255, 255, 255), -1)
        vw.write(im)
    vw.release()


def rising(path, frames=40):
    """One small ball climbing through an otherwise quiet band -- enough
    for the sweep to build a chain and serialise its points."""
    import numpy as np
    import cv2

    W, H = 640, 360
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    for f in range(frames):
        im = np.zeros((H, W, 3), dtype="uint8")
        x = 300 + f * 3
        y = 300 - f * 7
        if y > 4:
            cv2.circle(im, (int(x), int(y)), 4, (255, 255, 255), -1)
        vw.write(im)
    vw.release()


def main() -> int:
    # ---- half one: a real run sets every one of them ----------------
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "busy.mp4"
        synth(clip, 20, 25)
        out = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                               gates={"ascent_per_frame": 4})
        check("the sweep ran", bool(out.get("ok") or out.get("n_dets")), True)
        for k in NEEDED:
            check(f"  a real run reports {k}", k in out, True)

        # AND THE SATURATION REPORT IS INTERNALLY CONSISTENT, because a
        # panel that prints "bound on 19 frames (0%)" is worse than no
        # panel: it reads as a budget that is fine.
        _fr = out.get("scanned_frames") or 0
        _cf = out.get("cap_hit_frames") or 0
        _frac = out.get("cap_hit_frac")
        check("  the busy clip saturates a 4-slot budget", _cf > 0, True)
        check("  and the fraction agrees with the count",
              _frac is not None and _cf > 0 and _frac > 0, True)
        check("  the fraction is a fraction", 0.0 < float(_frac) <= 1.0, True)
        check("  and it dropped what it could not keep",
              (out.get("dropped_to_cap") or 0) > 0, True)
        check("  naming the busiest frame it saw",
              (out.get("max_hits_in_a_frame") or 0)
              > (out.get("per_frame_cap") or 0), True)

        # THE BAND IS THE FIRST THING TO CHECK and the easiest to get
        # wrong, so it is drawn with its own provenance: a skyline a
        # person drew reads differently from a height guessed off a box.
        _band = out.get("band")
        check("  the band is drawn as x,y,w,h",
              isinstance(_band, list) and len(_band) == 4, True)
        check("  with no skyline, the floor says so",
              "no skyline" in (out.get("band_floor_source") or ""), True)
        _sky = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9}, sky_frac=0.4)
        check("  with one, it credits the skyline",
              "skyline drawn" in (_sky.get("band_floor_source") or "")
              and "no skyline" not in (_sky.get("band_floor_source") or ""),
              True)

        # AND RAISING THE BUDGET IS WHAT MAKES THE PANEL GO QUIET. If the
        # numbers did not move with the gate they would be decoration.
        _hi = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                               gates={"ascent_per_frame": 60})
        check("  raising the budget clears the saturation",
              (_hi.get("cap_hit_frames"), _hi.get("dropped_to_cap")), (0, 0))

    # ---- half two: the page reads them ------------------------------
    # This is the half that was missing. Every key above was computed,
    # serialised and thrown away by the browser.
    src = PAGE.read_text()
    check("the page has a detector-budget panel",
          "function DetectorBudget" in src, True)
    check("  and renders it", "<DetectorBudget" in src, True)
    for k in NEEDED:
        check(f"  the page reads {k}", k in src, True)

    # ---- and a chain's points carry what they measured -------------
    # The areas of an ACCEPTED chain's points are a ball on this tee,
    # which is exactly the number ascent_prefer_area wants. They were
    # dropped during serialisation one line before anything could read
    # them.
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "rise.mp4"
        rising(clip)
        out = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9})
        _con = out.get("considered") or []
        check("a rising ball gives the sweep a chain to weigh",
              len(_con) > 0, True)
        _pts = [q for c in _con for q in (c.get("points") or [])]
        check("  whose points are serialised", len(_pts) > 0, True)
        check("  each carrying the frame, x, y AND area",
              all(set(q) >= {"frame", "x", "y", "area"} for q in _pts), True)
        check("  with a real area on the ones a detection supplied",
              any((q.get("area") or 0) > 0 for q in _pts), True)
    check("the page reads the chain points' areas", "q.area" in src, True)
    check("  from the accepted chains only",
          "!(c.why || []).length" in src, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
