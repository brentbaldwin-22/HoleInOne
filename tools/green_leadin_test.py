"""The comet should already be moving when the green view cuts in.

The chain starts where the operator could first SEE the ball, which is
after the cut, so the clip opened on an empty green and the ball
appeared out of nothing. This runs the plotted points backwards along
the flight they describe.

EXTRAPOLATION IS NOT MEASUREMENT, so every assertion that it works is
paired with one that it REFUSES: too few points, points that are not a
flight, and a window longer than a fit can honestly carry.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.green_flight import (
    lead_in, LEADIN_MIN_POINTS, LEADIN_MAX_SEC, LEADIN_MAX_RMS_PX)

FPS = 60.0
W, H = 1920, 1080


def flight(f):
    """A ball falling across the frame: x linear, y under gravity."""
    t = f / FPS
    return (300.0 + 240.0 * t, 80.0 + 60.0 * t + 420.0 * t * t)


def main():
    # The operator plots the last half-second, f60..f90.
    plotted = [{"frame": f, "x": flight(f)[0], "y": flight(f)[1]}
               for f in range(60, 91, 6)]
    dbg = {}
    got = lead_in(plotted, cut_frame=20, fps=FPS, frame_w=W, frame_h=H,
                  debug=dbg)
    print(f"plotted f60..f90, cut at f20 -> {len(got)} lead-in frames "
          f"(rms {dbg['lead_in']['rms_px']}px, capped by "
          f"{dbg['lead_in']['capped_by']})")
    assert got, dbg
    # IT HAS TO LAND ON THE REAL PATH, not merely produce points. These
    # frames have a known truth, so check against it.
    worst = max(
        ((p["x"] - flight(p["frame"])[0]) ** 2
         + (p["y"] - flight(p["frame"])[1]) ** 2) ** 0.5
        for p in got)
    print(f"  worst error against the true flight: {worst:.2f}px")
    assert worst < 2.0, f"extrapolation drifted {worst:.1f}px off the truth"
    assert got[-1]["frame"] == 59, got[-1]
    assert all(p.get("lead_in") for p in got), "lead-in points not marked"
    # Continuous with the plotted chain. The last lead-in frame is ONE
    # FRAME before the first plotted one, so the ball has moved a frame's
    # worth between them -- comparing to zero measures the ball's speed,
    # not a discontinuity, and the first version of this asserted exactly
    # that and failed on a perfect fit.
    step = (((flight(60)[0] - flight(59)[0]) ** 2
             + (flight(60)[1] - flight(59)[1]) ** 2) ** 0.5)
    gap = ((got[-1]["x"] - plotted[0]["x"]) ** 2
           + (got[-1]["y"] - plotted[0]["y"]) ** 2) ** 0.5
    print(f"  join: {gap:.1f}px against {step:.1f}px of travel per frame")
    assert abs(gap - step) < 1.0, (gap, step)

    # CONTROL: too few points must refuse, not guess.
    few = plotted[:LEADIN_MIN_POINTS - 1]
    d2 = {}
    assert lead_in(few, 20, FPS, W, H, debug=d2) == [], d2
    print(f"  {len(few)} points -> refused: {d2['lead_in']['why']}")

    # CONTROL: points that are not one flight must refuse. Without
    # this, any scatter of clicks yields a confident parabola.
    import random
    rng = random.Random(3)
    noisy = [{"frame": p["frame"],
              "x": p["x"] + rng.uniform(-90, 90),
              "y": p["y"] + rng.uniform(-90, 90)} for p in plotted]
    d3 = {}
    out3 = lead_in(noisy, 20, FPS, W, H, debug=d3)
    print(f"  scattered points -> {len(out3)} frames "
          f"({d3['lead_in'].get('why', 'accepted')})")
    assert out3 == [], (
        f"a scatter of clicks was extrapolated as a flight: "
        f"rms {d3['lead_in'].get('rms_px')}px vs limit {LEADIN_MAX_RMS_PX}")

    # CONTROL: the window is bounded. A cut far in the past must not
    # produce seconds of invented flight.
    d4 = {}
    out4 = lead_in(plotted, cut_frame=-100000, fps=FPS, frame_w=W, frame_h=H,
                   debug=d4)
    span = (out4[-1]["frame"] - out4[0]["frame"]) / FPS if out4 else 0
    print(f"  cut far in the past -> {len(out4)} frames = {span:.2f}s "
          f"(capped by {d4['lead_in']['capped_by']})")
    assert span <= LEADIN_MAX_SEC + 1e-6, span

    # CONTROL: it stops at the frame edge rather than drawing the ball
    # outside the picture.
    assert all(0 <= p["x"] < W and 0 <= p["y"] < H for p in out4), "off-frame"

    # CONTROL: nothing to do when the chain already reaches the cut.
    d5 = {}
    assert lead_in(plotted, cut_frame=80, fps=FPS, frame_w=W, frame_h=H,
                   debug=d5) == []
    print(f"  chain already past the cut -> refused: {d5['lead_in']['why']}")

    print("\nALL PASS")


if __name__ == "__main__":
    main()
