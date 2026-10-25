"""Adding roll-out points must not evict the descent that precedes them.

The operator plotted a descent, it worked, then they went back and
added points for the ball rolling out on the green -- later frames --
and the earlier points stopped being used. The window that bounds the
track was measured back from the LAST point, and the roll-out moved it.

Each assertion is paired with its opposite: the budget still has to
throw away a runaway approach, or removing it would pass this file.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.admin import _saved_green_track, D3_MAX_COMET_SEC


class _Row:
    """Just enough of a LongVideoUpload for the reader."""
    id = 1

    def __init__(self, swings):
        self.edit_metrics = {"swings": swings}


def track(frames):
    return [{"frame": f, "x": 100 + f, "y": 200} for f in frames]


FPS = 60.0
BUDGET = int(round(D3_MAX_COMET_SEC * FPS))     # 180 frames at 60fps


def main():
    print(f"budget = {D3_MAX_COMET_SEC}s = {BUDGET} frames at {FPS:.0f}fps")

    # The descent: a second of fall landing at f520.
    descent = list(range(460, 521, 10))
    # ...then the operator adds the roll-out, four seconds of it.
    rollout = list(range(530, 761, 30))

    # --- the bug, in the shape it was reported ----------------------
    row = _Row([{ "idx": 0, "landing_frame": 520,
                  "green_track": track(descent + rollout)}])
    got = _saved_green_track(row, 0, FPS)
    kept = sorted(p["frame"] for p in got)
    print(f"descent {descent[0]}-{descent[-1]} + roll-out to {rollout[-1]}")
    print(f"  kept {len(kept)} of {len(descent) + len(rollout)}: "
          f"f{kept[0]}-f{kept[-1]}")
    for f in descent:
        assert f in kept, (
            f"f{f} of the plotted descent was dropped because the "
            f"operator added roll-out points after the landing")
    for f in rollout:
        assert f in kept, f"roll-out point f{f} was dropped"

    # CONTROL: the budget must still bound the APPROACH, or this test
    # passes just as well with the limit deleted.
    runaway = list(range(0, 460, 20))           # minutes of prepended junk
    row2 = _Row([{"idx": 0, "landing_frame": 520,
                  "green_track": track(runaway + descent + rollout)}])
    got2 = _saved_green_track(row2, 0, FPS)
    kept2 = sorted(p["frame"] for p in got2)
    dropped = [f for f in runaway if f not in kept2]
    print(f"  runaway approach: dropped {len(dropped)} of {len(runaway)} "
          f"prepended points")
    assert dropped, (
        "nothing was dropped from a track reaching back to f0 — the "
        "budget is not bounding the approach any more")
    assert all(f in kept2 for f in rollout), "roll-out lost in the control"
    # Everything kept must be within the window of the LANDING.
    assert min(kept2) >= 520 - BUDGET, (min(kept2), 520 - BUDGET)

    # CONTROL: with no landing_frame recorded, fall back to the old
    # anchor rather than crashing or keeping everything.
    row3 = _Row([{"idx": 0, "green_track": track(descent + rollout)}])
    got3 = _saved_green_track(row3, 0, FPS)
    kept3 = sorted(p["frame"] for p in got3)
    print(f"  no landing_frame: anchored on the last point, kept "
          f"f{kept3[0]}-f{kept3[-1]}")
    assert min(kept3) >= max(kept3) - BUDGET

    # CONTROL: a bad landing_frame must not take the reader down.
    row4 = _Row([{"idx": 0, "landing_frame": "nonsense",
                  "green_track": track(descent + rollout)}])
    got4 = _saved_green_track(row4, 0, FPS)
    assert got4, "a malformed landing_frame lost the whole track"
    print(f"  malformed landing_frame: still returned {len(got4)} points")

    print("\nALL PASS")


if __name__ == "__main__":
    main()
