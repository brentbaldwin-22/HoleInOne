#!/usr/bin/env python3
"""Which knob actually holds a ball in a crowded band? Measured.

THE QUESTION THAT COST A DAY. Upload 1280's flight came back as a
7-point chain with 25 detections around it. Three theories were formed
from screenshots and all three were wrong: the corridor (the operator
tried 10, 25 and 35 -- "does nothing"), the frame-diff threshold
(DIFF_SENS is identical in the sweep and in the operator's own scan),
and the per-frame budget's ordering.

The clue that mattered was the operator's: zoom the wizard's scan to
3.7x and press Scan, and dots appear in the sky that the sweep never
produced -- same footage, same threshold, same detector. Zooming does
exactly one thing. It shrinks the region, so fewer blobs qualify.

So the lever is TRAFFIC, not discrimination. This test pins that, and
pins the three things that are NOT the lever, because each of them has
now been proposed once and would be proposed again.

The numbers below are retention over a synthetic band carrying what a
tee frame carries: a treeline of large movers, mid-sized clutter, sensor
speckle below the floor, and lone sparkle the ball's own size.

    python3 tools/ascent_band_traffic_test.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []
BALL_AREA = 45
TRIALS = 120
SEEDS = (1, 2, 3, 4)


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def frame_hits(rng, ball_xy, bw, bh):
    """One frame's qualifying blobs, generated over the WHOLE view and
    then clipped to the band -- so narrowing the band removes traffic
    rather than merely rescaling it, which is the thing being measured."""
    hits = []

    def add(a, x, y):
        if 0 <= y <= bh and 0 <= x <= bw:
            hits.append((a, x, y))

    for _ in range(180):                      # the treeline
        add(rng.randint(120, 900), rng.uniform(0, 1920),
            rng.uniform(496 * 0.72, 496))
    for _ in range(40):                       # trolleys, people, flag
        add(rng.randint(20, 300), rng.uniform(0, 1920),
            rng.uniform(496 * 0.4, 496 * 0.8))
    for _ in range(150):                      # sensor speckle
        add(rng.randint(1, 2), rng.uniform(0, 1920), rng.uniform(0, 496))
    for _ in range(200):                      # sparkle the ball's size
        add(rng.randint(3, 15), rng.uniform(0, 1920), rng.uniform(0, 496))
    hits.append((BALL_AREA, ball_xy[0], ball_xy[1]))
    return hits


def smallest_first(hits, per_frame, grid_px):
    """THE REJECTED REPAIR, kept so the measurement below is a
    comparison rather than an assertion. Cells are ordered loneliest
    first and, among equally lonely ones, SMALLEST above the speckle
    floor -- the mirror of what pick_spread_blobs does today."""
    budget = int(per_frame)
    keep = sorted(hits, reverse=True)[:max(1, budget // 2)]
    taken = {id(h) for h in keep}
    cell = max(8, int(grid_px))
    by_cell: dict = {}
    for h in hits:
        by_cell.setdefault(
            (int(h[1] // cell), int(h[2] // cell)), []).append(h)
    for v in by_cell.values():
        v.sort(reverse=True)
    order = sorted(by_cell, key=lambda k: (
        by_cell[k][0][0] < d3.SPREAD_SMALL_FLOOR,
        len(by_cell[k]), by_cell[k][0][0]))
    at = {k: 0 for k in order}
    while len(keep) < budget:
        added = False
        for k in order:
            lst, i = by_cell[k], at[k]
            while i < len(lst) and id(lst[i]) in taken:
                i += 1
            at[k] = i + 1
            if i < len(lst):
                keep.append(lst[i])
                taken.add(id(lst[i]))
                added = True
                if len(keep) >= budget:
                    break
        if not added:
            break
    return keep


def retained(per_frame=40, grid=d3.SPREAD_GRID, bw=1920, bh=496,
             prefer_area=0, picker=None):
    """On how many of TRIALS frames did the ball survive the cut, as a
    percentage, averaged over SEEDS."""
    total = 0
    for seed in SEEDS:
        rng = random.Random(seed)
        hit = 0
        for t in range(TRIALS):
            bx = 600 + t * 4.0
            by = max(6.0, bh * 0.62 - (t % 40) * 7.0)
            hits = frame_hits(rng, (bx, by), bw, bh)
            hits.sort(reverse=True)
            if prefer_area > 0:
                # The calibrated path, exactly as detect_movers_by_diff
                # takes it: rank by nearness to a known ball area.
                hits.sort(key=lambda h: abs(h[0] - prefer_area))
                picked = hits[:int(per_frame)]
            else:
                _p = picker or d3.pick_spread_blobs
                picked = _p(hits, per_frame,
                            max(8, min(bw, bh) / grid))
            if any(abs(q[1] - bx) < 0.5 and abs(q[2] - by) < 0.5
                   for q in picked):
                hit += 1
        total += hit
    return round(100.0 * total / (TRIALS * len(SEEDS)))


def main() -> int:
    full = retained()
    print(f"\n  baseline: the full band, budget {d3.SWEEP_PER_FRAME}, "
          f"ball kept on {full}% of frames\n")
    check("a ball in the full band loses the cut on most frames",
          full < 60, True)

    # ---- THE LEVER: less traffic ------------------------------------
    # This is what the operator's 3.7x zoom does, and it is the only
    # change here that works without calibrating anything.
    sky = retained(bh=300)
    sides = retained(bw=900)
    both = retained(bw=900, bh=300)
    print(f"\n  skyline raised:     {sky}%"
          f"\n  left/right trimmed: {sides}%"
          f"\n  both:               {both}%\n")
    check("raising the skyline holds the ball far more often",
          sky > full + 25, True)
    check("trimming the columns does too", sides > full + 25, True)
    check("and doing both is where it stops losing it",
          both > 90, True)
    check("  each one beats the full band", min(sky, sides) > full, True)

    # ---- THE WEAK LEVER: a bigger budget ----------------------------
    # It works, and it costs 8x to get where narrowing the band gets
    # for nothing -- the pick is still choosing 40-of-570 rather than
    # being given less to choose from.
    ladder = [(pf, retained(per_frame=pf)) for pf in (10, 40, 80, 320)]
    print("\n  budget ladder: " + ", ".join(
        f"{pf}->{r}%" for pf, r in ladder) + "\n")
    check("a bigger budget monotonically helps",
          [r for _, r in ladder] == sorted(r for _, r in ladder), True)
    check("  but even 8x the budget is worse than narrowing both edges",
          ladder[-1][1] < both, True)

    # ---- NOT A LEVER: the cell ordering -----------------------------
    # pick_spread_blobs deals half the budget round-robin over cells,
    # loneliest first and biggest-within-cell. Reversing that to
    # smallest-above-the-floor looks like the obvious repair and was
    # measured: it wins outright when the only small things are the
    # ball and sub-floor speckle, and collapses the moment the band
    # carries lone sparkle the ball's own size, which every treeline
    # does. Smallest-first is exactly as extremal as largest-first.
    sm = retained(picker=smallest_first)
    print(f"\n  smallest-above-floor cell order: {sm}% "
          f"(today's order: {full}%)\n")
    check("reversing the cell order is not the fix",
          abs(sm - full) < 25, True)

    # ---- NOT A LEVER: a finer grid ----------------------------------
    # More cells means the spread half's slots are dealt thinner, and
    # the ball's cell may never be reached at all.
    fine = [(g, retained(grid=g)) for g in (4, 8, 16, 32)]
    print("  grid ladder: " + ", ".join(
        f"{g}->{r}%" for g, r in fine) + "\n")
    check("a finer grid does not rescue it either",
          max(r for _, r in fine) <= max(full, fine[0][1]), True)

    # ---- THE KNIFE EDGE: ascent_prefer_area -------------------------
    # The one calibrated signal, and the only thing that identifies a
    # ball rather than ranking it. Within about 2x of the ball's real
    # area it is total; off by 3x it is total in the other direction.
    # That is why it still defaults to 0: nobody has measured what a
    # ball on a given tee actually spans.
    on = retained(prefer_area=BALL_AREA)
    near = retained(prefer_area=BALL_AREA * 2)
    off = retained(prefer_area=BALL_AREA // 3)
    way = retained(prefer_area=BALL_AREA * 4)
    print(f"\n  prefer_area {BALL_AREA} (exact): {on}%"
          f"   {BALL_AREA * 2} (2x): {near}%"
          f"   {BALL_AREA // 3} (1/3): {off}%"
          f"   {BALL_AREA * 4} (4x): {way}%\n")
    check("a calibrated ball area holds it on every frame", on > 95, True)
    check("  and tolerates being wrong by 2x", near > 95, True)
    check("  but being wrong the other way loses it entirely",
          off < 10, True)
    check("  as does being wrong by 4x", way < 10, True)
    check("  which is why it still defaults to off",
          d3.GATE_DEFAULTS["ascent_prefer_area"], 0)

    # ---- AND THE THING THE OPERATOR CHANGED -------------------------
    # The corridor is a CHAIN gate: it decides which detections link to
    # each other, and runs after everything above. It cannot put a
    # detection back that the band never looked at or the budget never
    # emitted -- which is why 10, 25 and 35 all read as "does nothing".
    check("the corridor is a chain gate, not a detector one",
          "ascent_corridor_px" in d3.GATE_SPEC
          and d3.GATE_SPEC["ascent_corridor_px"][0] > 0, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
