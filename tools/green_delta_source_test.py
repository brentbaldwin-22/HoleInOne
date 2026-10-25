"""A measured offset must beat the clocks; a guessed one must not.

Upload 826: the two Pi clocks disagreed by 2.07s, which put the ball in
the air for 8.4s against the 6.6s the green camera's own frames show,
and shifted the tee/green cut by the same amount. Marking the strike in
BOTH views measures the offset from one instant seen twice and owes
nothing to either clock.

The danger in preferring a stored number is that a previous run's
ASSUMPTION is stored in the same field. So each assertion that the
measurement wins is paired with one that a bare stored value loses.
"""
import os, sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.routers.admin as admin


class _Row:
    id = 826

    def __init__(self, em):
        self.edit_metrics = em


def run(em, stamps):
    """stamps: (tee, green) datetimes, or (None, None) for an upload
    with no camera event."""
    orig = admin._d3_first_frame_instants
    admin._d3_first_frame_instants = lambda db, row: (
        stamps[0], stamps[1], "camera_event")
    try:
        return admin._d3_green_delta_sec(None, _Row(em))
    finally:
        admin._d3_first_frame_instants = orig


TEE = datetime(2026, 10, 20, 16, 41, 42)
GREEN = datetime(2026, 10, 20, 16, 41, 52)       # 10s, and 2s of it wrong
CLOCKS = (TEE, GREEN)


def main():
    # What the clocks say, with nothing measured.
    sec, src = run({}, CLOCKS)
    print(f"clocks only                 -> {sec:+.2f}s  ({src})")
    assert abs(sec - 10.0) < 1e-6, sec
    assert src == "camera_event", src

    # The operator marked the strike in both views: 8.03s, not 10.
    measured = {"green_impact_frame": 97, "tee_green_delta_sec": 8.03}
    sec2, src2 = run(measured, CLOCKS)
    print(f"strike marked in both views -> {sec2:+.2f}s  ({src2})")
    assert abs(sec2 - 8.03) < 1e-6, (
        f"the measurement lost to the clocks: got {sec2}")
    assert src2 == "operator", src2

    # CONTROL: a stored delta with NO green impact mark is a previous
    # run's assumption, and must not outrank the stamps. Without this
    # the new branch would promote every guess ever written back.
    guessed = {"tee_green_delta_sec": 8.03}
    sec3, src3 = run(guessed, CLOCKS)
    print(f"stored delta, nothing marked-> {sec3:+.2f}s  ({src3})")
    assert abs(sec3 - 10.0) < 1e-6, (
        f"a bare stored delta outranked the camera stamps: {sec3}")
    assert src3 == "camera_event", src3

    # CONTROL: a mark with no number must not take the reader down, and
    # must fall through to the clocks rather than returning nonsense.
    half = {"green_impact_frame": 97}
    sec4, src4 = run(half, CLOCKS)
    print(f"mark but no number          -> {sec4:+.2f}s  ({src4})")
    assert abs(sec4 - 10.0) < 1e-6 and src4 == "camera_event", (sec4, src4)

    # ...and a mark with a non-numeric number.
    bad = {"green_impact_frame": 97, "tee_green_delta_sec": "nonsense"}
    sec5, src5 = run(bad, CLOCKS)
    print(f"mark with a bad number      -> {sec5:+.2f}s  ({src5})")
    assert abs(sec5 - 10.0) < 1e-6 and src5 == "camera_event", (sec5, src5)

    # The measurement still works on an upload with no camera event at
    # all, which is the case that has no clocks to fall back to.
    sec6, src6 = run(measured, (None, None))
    print(f"measured, no camera event   -> {sec6:+.2f}s  ({src6})")
    assert abs(sec6 - 8.03) < 1e-6 and src6 == "operator", (sec6, src6)

    # What it buys, in the units the operator judges: the flight time.
    tee_fps = green_fps = 60.0
    impact_tee_f, landing_green_f = 579, 494
    for label, d in (("clocks", 10.0), ("measured", 8.03)):
        flight = ((landing_green_f / green_fps) + d) - (impact_tee_f / tee_fps)
        print(f"  flight with the {label:8} offset: {flight:.2f}s")
    print("\nALL PASS")


if __name__ == "__main__":
    main()
