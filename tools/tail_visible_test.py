#!/usr/bin/env python3
"""A tee shot leaves the picture. That is not a reason to refuse it.

`_APEX_MAX_ABOVE_FRAC` was 0.6 of a frame: any continuation whose apex
sat higher than that was thrown away and the next model tried, ending
at `_arc_tail` -- a cubic bounded by the chord it spans, which is to
say an arc bent to stay in shot. On a 145-yard par 3 whose chain
climbs straight out of the top of the frame, that drew a line visibly
averted from the chain it was continuing.

THE COMMENT DEFENDING IT HAD THE RIGHT RULE AND THE WRONG MEASURE.
Upload 826 really did fail: 363 frames drawn, nearly all of them
off-screen, so the tracer went up and the clip ended before it came
back. But that is a VISIBILITY failure, not a height one -- 826's apex
was 1.55 frames up and a perfectly good tall shot can be higher.

So height stays only as a runaway bound, and the question that decides
it is how much of the tail is in shot.

    python3 tools/tail_visible_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.ai_tracer import (  # noqa: E402
    _APEX_MAX_ABOVE_FRAC,
    _TAIL_MIN_VISIBLE_FRAC,
    _apex_above_frame_px,
    _tail_refused,
    _tail_visible_frac,
)

W, H = 1920, 1080
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def arc(n, apex_y, x0=1100, x1=1500, y0=300, y1=736):
    """A parabola from (x0,y0) up to EXACTLY apex_y and down to (x1,y1).

    A quadratic Bezier does not pass through its control point: it
    peaks at (y0 + 2c + y1)/4. The first version of this helper passed
    the wanted apex straight in as c, so every fixture came out about a
    third as tall as it claimed -- and the test then "proved" that a
    355px apex would have failed a 648px rule, which it would not.
    Solve for c instead.
    """
    c = (4.0 * apex_y - y0 - y1) / 2.0
    out = []
    for i in range(n):
        t = i / float(n - 1)
        x = x0 + (x1 - x0) * t
        y = (1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * c + t * t * y1
        out.append((i, x, y))
    return out


print("the shot in front of us — up, out of frame, and back:")
# Apex a frame and a bit above the picture: the ball is genuinely gone
# from shot for the middle of its flight.
tall = arc(240, apex_y=-1200)
_vis = _tail_visible_frac(tall, W, H)
print("    apex %.0fpx above, %.0f%% of it in shot"
      % (_apex_above_frame_px(tall), _vis * 100))
check("it is not refused", _tail_refused(tall, W, H, "test"), False)
check("...and the OLD 0.6 rule would have refused it",
      _apex_above_frame_px(tall) > 0.6 * H, True)

print()
print("upload 826 — up, and the clip ends before it returns:")
# Nearly the whole tail above the frame: the viewer sees a line leave
# and nothing come back.
runaway = arc(363, apex_y=-1676, y0=120, y1=-60)
_vis826 = _tail_visible_frac(runaway, W, H)
print("    apex %.0fpx above, %.0f%% of it in shot"
      % (_apex_above_frame_px(runaway), _vis826 * 100))
check("still refused", _tail_refused(runaway, W, H, "test"), True)
check("...on visibility, not height",
      _apex_above_frame_px(runaway) < _APEX_MAX_ABOVE_FRAC * H, True)

print()
print("an ordinary low tracer is untouched:")
flat = arc(120, apex_y=200)
check("fully in shot", round(_tail_visible_frac(flat, W, H), 2), 1.0)
check("...and kept", _tail_refused(flat, W, H, "test"), False)

print()
print("the runaway bound still exists:")
absurd = arc(240, apex_y=-3000)   # ~2.8 frames up
check("an apex four frames up is refused",
      _tail_refused(absurd, W, H, "test"), True)
check("...and that IS the height rule",
      _apex_above_frame_px(absurd) > _APEX_MAX_ABOVE_FRAC * H, True)

print()
print("the measure itself:")
check("a tail with no frame size is all visible",
      _tail_visible_frac(flat, 0, 0), 1.0)
check("an empty tail is nothing", _tail_visible_frac([], W, H), 0.0)
check("a tail entirely off the side is nothing",
      _tail_visible_frac([(0, -50.0, 500.0)], W, H), 0.0)
check("the floor is a backstop, not the rule", _TAIL_MIN_VISIBLE_FRAC, 0.05)

print()
print("the rule that actually separates them — does it come back:")
# A tail that ends inside the picture has come back, whatever happened
# in the middle. One that ends outside it has not been drawn.
_up_only = arc(240, apex_y=-1200, y1=-400)
check("a tail ending off the top is refused",
      _tail_refused(_up_only, W, H, "test"), True)
check("...even though its apex is legal",
      _apex_above_frame_px(_up_only) < _APEX_MAX_ABOVE_FRAC * H, True)
check("the same arc landing on the target is kept",
      _tail_refused(arc(240, apex_y=-1200, y1=736), W, H, "test"), False)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
