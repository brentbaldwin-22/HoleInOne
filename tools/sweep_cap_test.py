#!/usr/bin/env python3
"""Does the sweep say when its per-frame cap is what lost the ball?

THE QUESTION THAT KEPT BEING ASKED BY HAND. detect_movers_by_diff keeps
the biggest `per_frame` blobs and drops the rest. That is the right
order — inside a ball-sized window the ball is the big one — but it is
extremal, and a band with a treeline in it has plenty of things bigger
than a ball at 180 yards.

When the cap binds on most frames, a missing ball is not being refused
by any of the eight chain gates: it is never emitted, and nothing in
the gate editor can reach it. Those two cases produce the SAME report —
"0 ball(s) seen leaving" — and need opposite work, which is how
Rivertowne 8 cost an afternoon of gate theories.

So the detector counts it and the sweep says so.

    python3 tools/sweep_cap_test.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def synth(path, frames, blobs_per_frame, size=6):
    """A clip where N things move every frame, so the cap is a number we
    chose rather than one we hope for."""
    import numpy as np
    import cv2

    W, H = 640, 360
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         30.0, (W, H))
    for f in range(frames):
        im = np.zeros((H, W, 3), dtype="uint8")
        for b in range(blobs_per_frame):
            # Each blob moves every frame, so every one is a detection.
            x = 20 + (b * 37 + f * 5) % (W - 40)
            y = 20 + (b * 23) % (H - 40)
            cv2.rectangle(im, (x, y), (x + size, y + size),
                          (255, 255, 255), -1)
        vw.write(im)
    vw.release()


def main() -> int:
    import tempfile
    from pathlib import Path as _P  # noqa: F401

    with tempfile.TemporaryDirectory() as td:
        quiet = Path(td) / "quiet.mp4"
        busy = Path(td) / "busy.mp4"
        synth(quiet, 40, 3)
        synth(busy, 40, 25)

        # ---- the cap does not bind on a quiet band ------------------
        st = {}
        d3.detect_movers_by_diff(quiet, 0, 39, sens=3, per_frame=10,
                                 three_frame=True, stats=st)
        check("a quiet band never hits the cap", st.get("capped", 0), 0)
        check("  and still counts the frames it looked at",
              st.get("frames", 0) > 0, True)
        check("  reporting the worst frame it saw",
              0 < st.get("max_hits", 0) <= 10, True)

        # ---- and it does on a busy one ------------------------------
        st = {}
        d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=10,
                                 three_frame=True, stats=st)
        check("a busy band hits the cap on every frame",
              st.get("capped", 0) == st.get("frames", 0)
              and st.get("frames", 0) > 0, True)
        check("  and says how much it threw away",
              st.get("dropped", 0) > 0, True)
        check("  and how bad the worst frame was",
              st.get("max_hits", 0) > 10, True)

        # ---- raising the cap is what makes it stop binding ----------
        st = {}
        d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=40,
                                 three_frame=True, stats=st)
        check("raising the cap past the traffic stops it binding",
              st.get("capped", 0), 0)

        # ---- stats is optional; nothing breaks without it -----------
        got = d3.detect_movers_by_diff(busy, 0, 39, sens=3, per_frame=10,
                                       three_frame=True)
        check("the detector still works when nobody asks for stats",
              len(got) > 0, True)

    # ---- the cap is a GATE now, not a constant ----------------------
    # Every other gate filters chains that exist. This one decides
    # whether the ball's chain exists at all, so when it is too low the
    # one knob that could help is the one knob nobody can reach.
    check("the per-frame keep is tunable per hole",
          d3.resolve_gates({"ascent_per_frame": 8})["ascent_per_frame"], 8)
    check("  nonsense falls back to the default",
          d3.resolve_gates({"ascent_per_frame": "x"})["ascent_per_frame"],
          d3.GATE_DEFAULTS["ascent_per_frame"])
    check("  and it is clamped, not rejected, at the rail",
          d3.resolve_gates({"ascent_per_frame": 9999})["ascent_per_frame"],
          d3.GATE_SPEC["ascent_per_frame"][2])

    # IT MUST NOT BE STRICTER THAN THE OPERATOR'S OWN SCAN. The sweep
    # runs at sensitivity 3; overriding that below its own per-frame
    # default meant the search deciding whether a swing exists saw less
    # than the scan an operator presses when they want to find one — on
    # the same footage, same threshold, same area band.
    _sens3_default = d3.DIFF_SENS[3][1]
    check("the sweep keeps at least as many blobs as its sensitivity does",
          d3.SWEEP_PER_FRAME >= _sens3_default, True)

    # AND THE TUNED VALUE HAS TO REACH THE DETECTOR. Reading it out of
    # resolve_gates proves only that the editor can store a number; a
    # mutation that resolved the gate and then passed the module
    # constant anyway survived exactly that test, because the default
    # equals the constant and an untuned run cannot tell them apart.
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "busy.mp4"
        synth(clip, 20, 25)
        for want in (4, 40):
            out = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                                   gates={"ascent_per_frame": want})
            check(f"  a hole tuned to {want} runs the detector at {want}",
                  out.get("per_frame_cap"), want)
        # And the saturation report follows it: the same clip is capped
        # at 4 and not at 40, which is the whole point of tuning it.
        lo = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                              gates={"ascent_per_frame": 4})
        hi = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                              gates={"ascent_per_frame": 60})
        check("  and raising it is what stops the cap binding",
              (lo.get("cap_hit_frames", 0) > 0,
               hi.get("cap_hit_frames", 0)), (True, 0))

    # ---- three-frame differencing is a switch now --------------------
    # The last real difference between this sweep and the scan an
    # operator runs by hand. The AND kills the "where it was" ghost
    # that otherwise reads a ball's velocity as zero — but it also asks
    # the ball to clear threshold in both diffs at the same pixels, and
    # what that costs on a real flight has never been measured. The
    # switch is so it can be, on one clip, instead of argued about.
    check("three-frame differencing defaults on",
          d3.GATE_DEFAULTS["ascent_three_frame"], 1)
    check("  and can be turned off per hole",
          d3.resolve_gates({"ascent_three_frame": 0})["ascent_three_frame"],
          0)
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "sw.mp4"
        synth(clip, 20, 6)
        for want in (0, 1):
            out = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                                   gates={"ascent_three_frame": want})
            check(f"  a hole set to {want} runs the detector that way",
                  out.get("three_frame"), bool(want))
        # THE GHOST IS REAL, and it is why three-frame exists: a
        # two-frame diff lights a mover twice, where it is and where it
        # was. Compared with ghost suppression OFF, or this measures
        # the suppressor rather than the ghost — two-frame now ships
        # with it on, which is what broke this assertion when it was
        # written against the older default.
        two = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                               gates={"ascent_three_frame": 0,
                                      "ascent_ghost_px": 0})
        three = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                                 gates={"ascent_three_frame": 1})
        check("  two-frame sees more, because it sees every ghost too",
              two.get("n_dets", 0) > three.get("n_dets", 0), True)
        # AND SUPPRESSION IS ONLY READ WITH TWO-FRAME. The AND has no
        # ghost to remove, and running it there would be a second,
        # unasked-for filter on a detector that is already the strictest
        # of the two.
        check("  three-frame reports no ghost radius",
              three.get("ghost_px"), 0.0)
        check("  two-frame reports the one it used",
              d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                               gates={"ascent_three_frame": 0,
                                      "ascent_ghost_px": 6}
                               ).get("ghost_px"), 6.0)
        # REPORTED IS NOT USED. A mutation that reported the radius and
        # then passed zero to the detector survived the check above,
        # which is the same hole the per-frame cap had: the only proof
        # is that the detections themselves change.
        _wide = d3.sweep_ascents(clip, 60.0, roi={"y": 0.9},
                                 gates={"ascent_three_frame": 0,
                                        "ascent_ghost_px": 20})
        check("  and a wide radius actually removes detections",
              _wide.get("n_dets", 0) < two.get("n_dets", 0), True)

    # ---- the detections around a chain -------------------------------
    # Every picture the sweep draws shows what it ASSEMBLED. A ball the
    # detector never saw and a ball the tracker refused look identical
    # from outside and need opposite fixes, and five attempts to tell
    # them apart by simulation failed. The raw detections settle it.
    chain = {"points": [{"frame": 100, "x": 1360, "y": 460},
                        {"frame": 104, "x": 1373, "y": 360}]}
    dets = (
        [{"frame": f, "x": 1365, "y": 460 - 25 * (f - 100), "area": 9}
         for f in range(100, 116)]              # the flight, every frame
        + [{"frame": f, "x": 300, "y": 400, "area": 50}
           for f in range(90, 130)]             # a tree, far to the left
        + [{"frame": f, "x": 1360, "y": 200, "area": 9}
           for f in range(200, 210)]            # something, much later
    )
    near = d3._dets_near_chain(dets, chain)
    _fr = {d["frame"] for d in near}
    check("the flight's own frames are kept",
          {f for f in range(100, 105)} <= _fr, True)
    check("  and the pad reaches past the chain's last point",
          max(_fr) >= 104 + 10, True)
    check("the tree, far to the side, is not", 300 in {d["x"] for d in near},
          False)
    check("  nor is anything from another part of the clip",
          any(d["frame"] >= 200 for d in near), False)
    check("no chain means nothing to show", d3._dets_near_chain(dets, None),
          [])
    check("  and a chain with no points is the same",
          d3._dets_near_chain(dets, {"points": []}), [])
    # THE WHOLE POINT: more detections come back than the chain kept, or
    # the picture cannot show a tracker failure at all.
    check("more is returned than the chain kept",
          len(near) > len(chain["points"]), True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
