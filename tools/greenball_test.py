#!/usr/bin/env python3
"""The zoned green detector: what it finds, and what it refuses.

WHY THIS EXISTS. The ball detector's constants -- area, roundness,
fill, local contrast -- were measured against real turf at the scale
the TEE camera sees it. This file runs that detector on a green, where
the ball is a third the size, by scaling each zone up before it runs.

That scaling is the whole trick and it is also the whole trap: the
area gate is applied in the pixels of the image the detector is handed,
so scaling the image up without scaling the gate makes the detector
accept things a third the size of anything it was ever meant to. The
first run of this on a synthetic green with real-ish turf noise
returned 59 "balls already on the green". The fix is to hold the gate
in the ORIGINAL frame's pixels, and this test is what keeps it held.

The green is synthetic on purpose: mowing stripes, per-pixel noise, a
bunker and a treeline, with balls planted at known pixels. A detector
tested only on clean fixtures is a detector tested on nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_fails = []
H, W = 1080, 1920
GREEN = [[40, 700], [1880, 660], [1880, 980], [60, 1010]]


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def green_frame(balls, seed=3, bunker=True):
    """1920x1080: sky, treeline, striped turf with noise, and a bunker."""
    rng = np.random.default_rng(seed)
    f = np.full((H, W, 3), 60, np.uint8)
    f[:380] = (170, 180, 190)
    f[380:620] = (40, 55, 42)
    for y in range(620, H):
        b = 78 + 7 * ((y // 24) % 2)
        f[y, :] = (b - 12, b + 10, b - 6)
    f[620:] = cv2.add(f[620:],
                      rng.integers(0, 7, (H - 620, W, 3), dtype=np.uint8))
    if bunker:
        cv2.ellipse(f, (520, 960), (300, 70), 0, 0, 360, (190, 195, 200), -1)
    for (bx, by) in balls:
        cv2.circle(f, (bx, by), 3, (238, 240, 242), -1)
    return f


def near(dets, x, y, px=10):
    return any((d["x"] - x) ** 2 + (d["y"] - y) ** 2 <= px * px
               for d in dets)


def main():
    from app.services.greenball import (
        N_ZONES, find_balls_on_green, _zone_rects)

    # ── the ordinary case: one partner's ball, one shot ─────────────
    before = green_frame([(640, 790)])
    after = green_frame([(640, 790), (1689, 811)])
    r = find_balls_on_green(before, after, GREEN)

    ok("the partner's ball is seen before the shot",
       near(r["before"], 640, 790), True)
    ok("  and only it -- turf noise is not a ball",
       len(r["before"]), 1)
    ok("both balls are seen after", len(r["after"]), 2)
    ok("only the new one counts as new", len(r["new"]), 1)
    ok("  and it is the one that arrived",
       near(r["new"], 1689, 811), True)
    ok("  reported within a pixel or two of where it was planted",
       (abs(r["best"]["x"] - 1689) <= 3, abs(r["best"]["y"] - 811) <= 3),
       (True, True))
    ok("  at about the size it was drawn",
       4.0 <= r["best"]["diam_px"] <= 11.0, True)
    ok("four zones were searched", len(r["zones"]), N_ZONES)
    ok("  and each one was scaled up",
       all(z["zoom"] > 1.0 for z in r["zones"]), True)

    # ── AN EMPTY GREEN IS EMPTY ─────────────────────────────────────
    # The one failure that matters most: turf noise, mowing stripes
    # and a bunker must not produce a ball. This is the assertion the
    # size gate exists for.
    r0 = find_balls_on_green(green_frame([]), green_frame([]), GREEN)
    ok("nothing on an empty green before", len(r0["before"]), 0)
    ok("  nor after", len(r0["after"]), 0)
    ok("  and no answer is invented", r0["best"], None)
    ok("  and ok is false", r0["ok"], False)

    # ── A BALL OUTSIDE THE OUTLINE IS NOT ON THE GREEN ──────────────
    # A white dot short of the green is identical to one on it except
    # for where it is, which is the entire reason the outline exists.
    out_of_bounds = green_frame([(900, 1060)])     # below the outline
    r2 = find_balls_on_green(green_frame([]), out_of_bounds, GREEN)
    ok("a ball well outside the outline is refused", len(r2["new"]), 0)

    # ── A BALL ON THE COLLAR IS STILL THE BALL ──────────────────────
    # The outline is dragged with a mouse on a live view. A few feet
    # of slack is the difference between a detector and a pedant.
    collar = green_frame([(1700, 1000)])           # just below the line
    r3 = find_balls_on_green(green_frame([]), collar, GREEN)
    ok("a ball just outside the line still counts",
       near(r3["new"], 1700, 1000, 12), True)

    # ── TWO NEW BALLS ARE TWO, AND BOTH ARE REPORTED ────────────────
    two = green_frame([(640, 790), (1689, 811), (1100, 850)])
    r4 = find_balls_on_green(green_frame([(640, 790)]), two, GREEN)
    ok("two arrivals are both reported", len(r4["new"]), 2)
    ok("  brightest first", r4["new"][0]["contrast"]
       >= r4["new"][1]["contrast"], True)

    # ── THE ZONES COVER THE WHOLE BOX, WITH OVERLAP ─────────────────
    rects = _zone_rects(40, 660, 1880, 1010)
    ok("the zones span the full width",
       (min(r[0] for r in rects), max(r[2] for r in rects)), (40, 1880))
    ok("  and the full height",
       (min(r[1] for r in rects), max(r[3] for r in rects)), (660, 1010))
    ok("  and neighbours overlap rather than abut",
       rects[1][0] < rects[0][2], True)
    # A tall box is quartered instead of sliced.
    ok("a squarer green is cut 2x2, not into four slices",
       len(_zone_rects(0, 0, 800, 600)), 4)
    ok("  and those four are not all in one row",
       len({r[1] for r in _zone_rects(0, 0, 800, 600)}), 2)

    # ── NO OUTLINE: SEARCH THE FRAME, DO NOT RAISE ──────────────────
    r5 = find_balls_on_green(green_frame([]), after, None)
    ok("no outline still returns an answer", r5["ok"], True)
    ok("  and says it searched the whole frame",
       "no outline" in (r5["crop"]["from"] or ""), True)

    # ── AND WHEN IT FINDS NOTHING, IT SAYS WHICH STAGE ──────────────
    #
    # THE FAILURE THAT STARTED THIS. The first run on real footage
    # came back empty, the dialog fell through to the plates, and
    # there was nothing at all to look at -- so a change that had
    # shipped was indistinguishable from one that had not. Five quite
    # different failures shared that one empty answer.

    # 1. Nothing anywhere was brighter than its surroundings. Note
    #    the frame has NO BUNKER: a bunker is a huge bright region
    #    that always gets rejected for size, so a green with one in
    #    it can never reach this diagnosis -- which is itself worth
    #    knowing and is why the bunker-ful frame is used below.
    flat = green_frame([], bunker=False)
    r6 = find_balls_on_green(flat, flat, GREEN)
    ok("a flat empty green names the brightness stage",
       "never a candidate" in (r6["found_nothing_because"] or ""), True)
    ok("  and reports the brightest thing it did see, per zone",
       all(z.get("hat_max") is not None for z in r6["zones"]), True)
    ok("  which is under the bar",
       all(z["hat_max"] < z["hat_needs"] for z in r6["zones"]), True)

    # 2. Something WAS bright enough, and a gate refused it. That is
    #    the opposite diagnosis and the opposite fix.
    streak = green_frame([], bunker=False)
    cv2.line(streak, (1500, 800), (1620, 802), (235, 237, 239), 2)
    r7 = find_balls_on_green(green_frame([], bunker=False), streak, GREEN)
    ok("a bright streak is turned down, not found", len(r7["new"]), 0)
    ok("  and the summary blames a gate, not the light",
       "turned down" in (r7["found_nothing_because"] or ""), True)
    ok("  and some zone recorded the rejection",
       sum(z.get("n_rejected") or 0 for z in r7["zones"]) >= 1, True)
    ok("  with the measurement and the limit in the sentence",
       any("over" in (q["why"] or "") or "sliver" in (q["why"] or "")
           or "not round" in (q["why"] or "")
           for z in r7["zones"] for q in (z.get("rejected") or [])), True)
    ok("  and that zone saw something bright",
       max(z["hat_max"] for z in r7["zones"]) >= 18, True)

    # 3. A ball that is in both frames is not an arrival, and that is
    #    its own sentence too -- the shot missed, it did not fail.
    both = green_frame([(640, 790)])
    r8 = find_balls_on_green(both, both, GREEN)
    ok("a ball in both frames is not new", len(r8["new"]), 0)
    ok("  and the summary says nothing arrived",
       "nothing arrived" in (r8["found_nothing_because"] or ""), True)

    # ── THE PICTURES EXIST WHETHER OR NOT ANYTHING WAS FOUND ────────
    # Rendering them only on success is what made the empty run
    # unreadable, so this is the assertion that keeps them.
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        r9 = find_balls_on_green(green_frame([]), green_frame([]), GREEN,
                                 out_dir=_td, stem="t")
        ok("an empty run still writes the before picture",
           bool(r9["images"].get("before")), True)
        ok("  and the after picture", bool(r9["images"].get("after")), True)
        ok("  and one picture per zone",
           sum(1 for z in r9["zones"] if z.get("path")), len(r9["zones"]))
        ok("  and no picker, since there was nothing to pick between",
           r9["images"].get("picker"), None)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
