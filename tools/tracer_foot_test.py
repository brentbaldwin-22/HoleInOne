#!/usr/bin/env python3
"""WHICH FRAME HOLDS THE LAUNCH POINT?

Clip 1430 reported tracer 0.601 while the player stood at ~0.73. At
00:05 its tracer is an arc that has LIFTED OFF THE GROUND -- the left
branch ends in a rounded tip in mid-air, around x 0.606, y 0.50. That
tip is still below the 0.45 foot gate, so it was collected as a
candidate like any other, and the old median-of-five let those late
frames outvote the early ones. 0.601 is the descending tail.

The stroke's tail recedes, so its foot climbs over the clip. The ball
is the LOWEST that line is ever seen.

Both halves of the fix are covered: the search window -- anchored on
impact, because ai_tracer cuts 4.5s before it and a FIXED 1s-3s window
searched the address on every clip from the normal pipeline -- and the
lowest-foot rule for choosing among the frames inside it. They are pure, so they are read off the shipping
source and exec'd alone -- admin.py cannot be imported (no fastapi, and
py3.9 chokes on deps.py).

    python3 tools/tracer_foot_test.py
"""

import pathlib

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "backend/app/routers/admin.py").read_text()
_ns: dict = {}
exec(
    _SRC[_SRC.index("_TRACER_WIN_HALF_SEC = "):
         _SRC.index("def _tracer_start_x(")],
    _ns,
)
_pick_tracer_foot = _ns["_pick_tracer_foot"]
_tracer_scan_bounds = _ns["_tracer_scan_bounds"]
HALF = _ns["_TRACER_WIN_HALF_SEC"]
WIN_LO = _ns["_TRACER_WIN_START_SEC"]
WIN_HI = _ns["_TRACER_WIN_END_SEC"]

# ai_tracer cuts every clip at `before_impact_sec`, so this is where
# the swing lands on anything from the normal pipeline.
PIPELINE_IMPACT = 4.5
D3PROD_IMPACT = 2.0

# (x, seconds, foot_y). Clip 1430 as the screenshots show it: the
# stroke leaves the ground at ~0.765/0.68 just after impact, then the
# tail recedes -- the foot climbs the frame and drifts left along the
# arc until, at 5s, it is a tip at 0.606 hanging at y 0.50.
CLIP_1430 = [
    (0.765, 2.13, 0.681),
    (0.762, 2.27, 0.679),
    (0.690, 3.60, 0.590),
    (0.612, 4.80, 0.505),
    (0.601, 5.07, 0.498),
]


def median_x(cands):
    """What the old code did, for comparison."""
    xs = sorted(c[0] for c in cands)
    return xs[len(xs) // 2]


def main() -> int:
    hit = _pick_tracer_foot(CLIP_1430)
    print(f"picked  x={hit[0]:.3f}  t={hit[1]:.2f}s  foot y={hit[2]:.3f}")

    assert abs(hit[0] - 0.765) < 1e-9, (
        f"took {hit[0]:.3f}, not the launch at 0.765")
    assert hit[1] < 2.5, f"took a frame at {hit[1]:.2f}s, well after impact"
    assert hit[2] == max(c[2] for c in CLIP_1430), "did not take the lowest foot"

    # MUTATION: the rule it replaced must fail these.
    old = median_x(CLIP_1430)
    print(f"old median-of-five would have taken x={old:.3f}")
    assert abs(old - 0.690) < 1e-9 or abs(old - 0.601) < 0.1, old
    assert abs(old - 0.765) > 0.05, (
        "MUTATION DID NOT BITE: the median agreed, so this proves nothing")

    # Ties go to the earlier frame -- nearer impact.
    tie = _pick_tracer_foot([(0.40, 4.0, 0.68), (0.77, 2.1, 0.68)])
    assert tie[1] == 2.1, f"tie went to {tie[1]}s, not the earlier frame"
    print("tie-break: equally low feet -> the earlier frame")

    # A single candidate, and none at all.
    assert _pick_tracer_foot([(0.5, 1.0, 0.6)])[0] == 0.5
    assert _pick_tracer_foot([]) is None
    print("one candidate, and none, both handled")

    # A stroke that never lifts: every frame agrees, so the rule must
    # be a no-op rather than preferring something odd.
    flat = [(0.70, 2.1, 0.66), (0.70, 2.3, 0.66), (0.70, 2.5, 0.66)]
    assert _pick_tracer_foot(flat)[0] == 0.70, "steady stroke disturbed"
    print("steady stroke: unchanged")

    # ---- the search window, anchored on impact -----------------
    def win(impact=None, cap=8.0, fps=30.0, idx0=0):
        lo, hi = _tracer_scan_bounds(fps, idx0, WIN_LO, WIN_HI, cap,
                                     impact_t=impact)
        return round(lo / fps, 2), round(hi / fps, 2)

    # Clip 1430: a d3prod swing at ~2s. Anchoring must reproduce
    # EXACTLY the 1s-3s window that was asked for after watching it.
    assert win(D3PROD_IMPACT) == (1.0, 3.0), win(D3PROD_IMPACT)
    print(f"d3prod  impact {D3PROD_IMPACT}s -> {win(D3PROD_IMPACT)}")

    # The normal pipeline cuts 4.5s before impact, so a FIXED 1-3s
    # window would have searched the address and found no stroke at
    # all. This is the regression that broke the next clip produced.
    assert win(PIPELINE_IMPACT) == (3.5, 5.5), win(PIPELINE_IMPACT)
    assert win(PIPELINE_IMPACT)[0] > 3.0, (
        "the pipeline's swing is still outside a 1-3s window")
    print(f"pipeline impact {PIPELINE_IMPACT}s -> {win(PIPELINE_IMPACT)}")

    # The 5s tip that produced 0.601 is outside the d3prod window, so
    # the lowest-foot rule stays the second line of defence.
    assert 5.07 > win(D3PROD_IMPACT)[1], "the 5s frame is still in range"

    # Impact unknown: one window has to cover both populations.
    assert win() == (WIN_LO, WIN_HI) == (1.0, 6.0), win()
    assert win()[0] <= D3PROD_IMPACT <= win()[1], "d3prod swing not covered"
    assert win()[0] <= PIPELINE_IMPACT <= win()[1], "pipeline swing not covered"
    print(f"impact unknown -> {win()} (covers both)")

    # Never negative, however early impact lands.
    assert win(0.2)[0] == 0.0, win(0.2)

    # A caller's cap can tighten the window but never widen it: the
    # Player ID scan passes the cut so the green tracer is unreachable.
    assert win(PIPELINE_IMPACT, cap=4.0)[1] == 4.0, win(PIPELINE_IMPACT, cap=4.0)
    assert win(cap=99.0)[1] == WIN_HI, "max_scan_sec widened the window"

    # The green scan measures its window from the cut, not from zero.
    g_lo, g_limit = _tracer_scan_bounds(30.0, 200, 0.0, 8.0, 8.0)
    assert (g_lo, g_limit) == (200, 440), (g_lo, g_limit)
    print(f"green window: frames {g_lo}-{g_limit}, measured from the cut")

    # Never inverted, however absurd the inputs.
    bad_lo, bad_limit = _tracer_scan_bounds(30.0, 0, 9.0, 3.0, 8.0)
    assert bad_limit >= bad_lo, (bad_lo, bad_limit)

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
