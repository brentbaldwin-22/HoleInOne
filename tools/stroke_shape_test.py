#!/usr/bin/env python3
"""IS THIS BLUE THING A STROKE, WHICHEVER WAY IT LEANS?

Clip 1444's green came back `fat 43>18 a7141 244x43`. That rejection
was right -- 7141 pixels in a 244x43 box is 68% full, a solid object
and not a line -- but it exposed the test underneath it.

`min(bw, bh)` is the bounding box's SHORT SIDE, which equals a stroke's
thickness only when the stroke runs near vertical or near horizontal. A
3px stroke at 45 degrees across 200px has a 141x141 box and reads as
141 thick. The green tracer descends at an angle, so the test that
works on the tee throws it away on the green.

Area over length is the thickness whichever way it leans, because a
stroke of length L and thickness T covers about L*T pixels. It is what
the fill ratio and the bounding box were both reaching for: fill is
wrong for a vertical stroke (a 3x360 line fills its own 3x360 box) and
the box is wrong for a diagonal one.

This mirrors the arithmetic in `_tracer_start_x`'s component filter.
That loop cannot be imported (opencv, fastapi), so the numbers are
asserted here and the comment there carries the same worked examples.

    python3 tools/stroke_shape_test.py
"""

import pathlib
import re

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "backend/app/routers/admin.py").read_text()

# The frame the scan actually measures: 960 wide, 16:9.
H_SM = 540
LIM = max(5, int(H_SM * 0.035))


def measure(bw, bh, area):
    """The filter's own arithmetic, in one place."""
    long_ = (bw * bw + bh * bh) ** 0.5
    thin = area / max(1.0, long_)
    return long_, thin, long_ / max(0.5, thin)


def verdict(bw, bh, area, bottom_frac=0.70):
    long_, thin, ratio = measure(bw, bh, area)
    if long_ < 20:
        return "short"
    if thin > LIM:
        return "fat"
    if ratio < 4.0:
        return "squat"
    if bottom_frac < 0.45:
        return "high"
    return "stroke"


def main() -> int:
    print(f"thickness limit at {H_SM}px tall: {LIM}px\n")

    cases = [
        # name,                 bw,  bh,  area,  expected
        ("vertical stroke",       3, 360,  1080, "stroke"),
        ("diagonal stroke 45deg", 141, 141,  600, "stroke"),
        ("steep green descent",   29, 149,  450, "stroke"),
        ("clip 1444's blob",     244,  43, 7141, "fat"),
        ("a bystander's shirt",   60,  90, 4200, "fat"),
        ("noise speck",            4,   6,   20, "short"),
    ]
    for name, bw, bh, area, want in cases:
        long_, thin, ratio = measure(bw, bh, area)
        got = verdict(bw, bh, area)
        print(f"  {name:24s} {bw:3d}x{bh:3d} a{area:5d}  "
              f"len={long_:5.0f} thick={thin:5.1f} r={ratio:5.1f}  -> {got}")
        assert got == want, f"{name}: got {got}, want {want}"

    # THE REGRESSION, NAMED. The old test took the box's short side.
    # The diagonal stroke is the case it got wrong, and it must.
    old_thin = min(141, 141)
    assert old_thin > LIM, "the old measure would have passed it too"
    _, new_thin, _ = measure(141, 141, 600)
    assert new_thin <= LIM, new_thin
    print(f"\n  diagonal stroke: old measure {old_thin}px (fat), "
          f"new {new_thin:.0f}px (stroke)")

    # A vertical stroke fills its own box, which is why the fill ratio
    # was abandoned. The new measure must not reintroduce that.
    fill = 1080 / float(3 * 360)
    assert fill > 0.95, fill
    assert verdict(3, 360, 1080) == "stroke", "fill trap reintroduced"
    print(f"  vertical stroke fills {fill:.0%} of its box and still passes")

    # The shipping source must use the new measure, not the old one.
    blk = _SRC[_SRC.index("_long = (_bw * _bw"):]
    blk = blk[:blk.index("_bad = None")]
    assert "_thin = _a / max(1.0, _long)" in blk, "source is not area/length"
    assert not re.search(r"_thin\s*=\s*min\(_bw, _bh\)", _SRC), (
        "the bounding-box thickness is still in the source")
    print("  shipping source uses area/length")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
