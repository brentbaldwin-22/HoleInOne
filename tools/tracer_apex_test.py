#!/usr/bin/env python3
"""What the apex ceiling is still for, now that it is not the rule.

THIS FILE USED TO ASSERT THE OPPOSITE, AND THE RULE IT ASSERTED WAS
REMOVED ON PURPOSE.

Upload 826, a 145-yard par 3: the ballistic fit put the apex at y=-1676
on a 1080-tall frame and drew 363 frames nearly all of which were above
the top of the picture. The tracer went straight up and never came
back. The gate written for it refused any continuation whose apex sat
higher than 0.6 of a frame.

But height is not what went wrong. A ball hit off a tee at a 145-yard
par 3 goes UP, out of the top of the picture, and comes back; at 0.6 of
a frame the gate refused every one of those too, fell through to the
arc model -- a cubic bounded by the chord it spans -- and drew a line
visibly averted from the chain it was continuing. Reported as exactly
that: "we don't need to avert the tracer away from the actual chain so
we can see the arc."

So the ceiling was raised to 2.5 frames and kept ONLY as a runaway
bound: an apex several frames up is a fit that has stopped describing a
golf ball. What refuses 826 now is whether the tail comes back into
shot at all, which is measured in `_tail_refused` and tested in
tools/tail_visible_test.py.

This file guards two things: the helper's arithmetic, and that the
ceiling still sits where a runaway is caught and a real tee shot is
not. The last block re-checks that something ELSE still refuses 826 --
without it this test would pass while the actual protection was gone.

    python3 tools/tracer_apex_test.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ai_tracer import (  # noqa: E402
    _APEX_MAX_ABOVE_FRAC, _apex_above_frame_px, _tail_refused)

W, H = 1920, 1080
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def arc(n, apex_y, x0=1100, x1=1500, y0=300, y1=736):
    """A parabola from (x0,y0) up to EXACTLY apex_y and down to (x1,y1).

    A quadratic Bezier does not pass through its control point -- it
    peaks at (y0 + 2c + y1)/4 -- so the apex is solved for rather than
    passed in. Getting this wrong once made every fixture a third as
    tall as it claimed and the test then proved nothing. Same helper as
    tools/tail_visible_test.py, deliberately.
    """
    c = (4.0 * apex_y - y0 - y1) / 2.0
    out = []
    for i in range(n):
        t = i / float(n - 1)
        out.append((i, x0 + (x1 - x0) * t,
                    (1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * c
                    + t * t * y1))
    return out


_limit = _APEX_MAX_ABOVE_FRAC * H
print("the ceiling: apex may sit up to %.0fpx above a %dpx frame" % (_limit, H))

print("\nthe helper's arithmetic:")
check("a tail entirely in frame is 0px above it",
      _apex_above_frame_px(arc(60, apex_y=200)), 0.0)
# NOT A NEGATIVE NUMBER. A tail well inside the frame has a positive
# minimum y, and returning -min() unclamped would hand back a negative
# that sneaks past a sloppy `> limit` comparison as easily as a small
# positive one.
check("...never a negative number",
      _apex_above_frame_px([(0, 900.0, 540.0)]) >= 0.0, True)
check("a point 400px above the top measures 400px",
      _apex_above_frame_px([(0, 900.0, -400.0), (1, 905.0, 10.0)]), 400.0)
check("an empty tail is 0px, no exception", _apex_above_frame_px([]), 0.0)
check("so is None", _apex_above_frame_px(None), 0.0)

print("\nthe ceiling is a RUNAWAY bound, not a height rule:")
# 826's own number, which this file used to require be refused.
_826 = _apex_above_frame_px(arc(363, apex_y=-1676, y0=120, y1=-60))
print("    826's ballistic fit: apex %.0fpx above the frame" % _826)
check("826's apex is INSIDE the ceiling — height does not refuse it",
      _826 < _limit, True)
# A REAL TEE SHOT IS THE CASE TO PROTECT. The whole feature exists for
# the towering one; a ceiling that refuses it refuses the shot.
_tall = _apex_above_frame_px(arc(240, apex_y=-1200))
print("    a real tall tee shot:  apex %.0fpx above the frame" % _tall)
check("a tall tee shot is inside it too", _tall < _limit, True)
check("...and the OLD 0.6-frame rule would have refused both",
      _826 > 0.6 * H and _tall > 0.6 * H, True)
# AND IT STILL CATCHES SOMETHING. A bound nothing exceeds is not a
# bound; an apex several frames up is a fit that has stopped
# describing a golf ball.
_absurd = arc(240, apex_y=-3000)
check("an apex nearly three frames up is over the ceiling",
      _apex_above_frame_px(_absurd) > _limit, True)
check("...and that is the height rule refusing it",
      _tail_refused(_absurd, W, H, "test"), True)

print("\n826 IS STILL REFUSED — by coming back, not by height:")
# Without this block the file above would pass with the real
# protection deleted. See tools/tail_visible_test.py for the rule.
check("826's tail is refused", _tail_refused(
    arc(363, apex_y=-1676, y0=120, y1=-60), W, H, "test"), True)
check("the tall tee shot that lands on target is kept",
      _tail_refused(arc(240, apex_y=-1200), W, H, "test"), False)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
