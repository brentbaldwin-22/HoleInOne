"""A tracer whose apex leaves the picture is not a drawing.

Upload 826, a 145-yard par 3: the tracked flight moved 18px sideways,
the ballistic fit put its apex at y=-1676 on a 1080-tall frame, and it
drew 363 frames nearly all of which were above the top of the picture.
On screen the tracer went straight up and never came back.

The arc model does not fail that way -- it is a cubic between two known
points, so its apex is bounded by the chord. The gate has to prefer it
HERE without rejecting the ballistic model everywhere, so every
assertion that a runaway is caught is paired with one that an ordinary
high shot still draws.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ai_tracer import (
    _apex_above_frame_px, _APEX_MAX_ABOVE_FRAC)

H = 1080


def tail(ys):
    """Tail points are (frame, x, y)."""
    return [(600 + i, 1400.0, float(y)) for i, y in enumerate(ys)]


def main():
    limit = _APEX_MAX_ABOVE_FRAC * H
    print(f"frame {H}px, apex may sit up to {limit:.0f}px above it")

    # 826's actual fit.
    runaway = tail([800, 400, 0, -800, -1676, -800, 0, 400, 740])
    above = _apex_above_frame_px(runaway)
    print(f"  826's ballistic fit: apex {above:.0f}px above the frame")
    assert above > limit, (
        f"the fit that drew 363 off-screen frames passes the gate "
        f"({above:.0f}px vs {limit:.0f}px)")

    # CONTROL: an ordinary high shot leaves the frame and must STILL be
    # drawn. A gate that rejects this rejects every towering shot, which
    # is the shot this whole feature exists for.
    high = tail([800, 500, 200, -120, -300, -120, 200, 500, 740])
    above_h = _apex_above_frame_px(high)
    print(f"  an ordinary high shot: apex {above_h:.0f}px above the frame")
    assert above_h <= limit, (
        f"a normal high shot was rejected ({above_h:.0f}px vs "
        f"{limit:.0f}px) — the gate is too tight to draw a towering shot")

    # CONTROL: an arc that never leaves the frame reports zero, not a
    # negative number that would sneak past a sloppy comparison.
    inside = tail([800, 700, 600, 550, 600, 700, 740])
    assert _apex_above_frame_px(inside) == 0.0, _apex_above_frame_px(inside)
    print(f"  a tail entirely in frame: {_apex_above_frame_px(inside):.0f}px")

    # CONTROL: an empty tail must not throw.
    assert _apex_above_frame_px([]) == 0.0
    assert _apex_above_frame_px(None) == 0.0
    print("  empty tail: 0px, no exception")

    # The limit has to sit BETWEEN the two real cases, or it is not
    # separating anything.
    print(f"\n  {above_h:.0f}px (keep)  <  {limit:.0f}px  <  "
          f"{above:.0f}px (reject)")
    assert above_h < limit < above

    print("\nALL PASS")


if __name__ == "__main__":
    main()
