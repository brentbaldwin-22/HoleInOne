#!/usr/bin/env python3
"""When was the ball struck? Measured against a known answer.

#768's ball is struck at f1536. The sweep said f1525 -- eleven frames
early, and all of it the model: the detector had the chain right.

WHY ELEVEN. The back-trace took the chain's speed across the band and
ran it back over the 351px below the band to the tee line. A struck
ball is fastest the instant it leaves and slows the whole way up, so
the speed down there is much higher than the speed up here, and using
the slow one buys too many frames. It also gets worse with frame rate,
which is why it showed up when these cameras went to 60fps: the same
gap takes twice as many frames to cross.

    python3 tools/impact_frame_test.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import debug3 as d3  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def pts(rows):
    return [{"frame": f, "x": x, "y": y, "area": 100} for f, x, y in rows]


# THE TEE LINE AND THE ANSWER, both read off the operator's own screen:
# "tee spot 1334, 842" and an impact at f1536.
TEE_Y, TRUE = 842.0, 1536

# The chain for #768 as the sweep built it on two separate builds. Same
# flight, different point sets -- which is the only independent check
# available on one swing.
RUN2 = pts([(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
            (1549, 1366, 440), (1550, 1368, 416), (1551, 1372, 371),
            (1552, 1372, 362), (1554, 1378, 313), (1555, 1378, 296),
            (1556, 1380, 285), (1558, 1384, 245), (1559, 1385, 235),
            (1560, 1386, 215), (1563, 1391, 176), (1564, 1391, 168),
            (1565, 1391, 163), (1566, 1395, 138), (1569, 1398, 108),
            (1570, 1400, 102), (1571, 1401, 98)])
RUN1 = pts([(1546, 1362, 491), (1547, 1364, 465), (1548, 1366, 441),
            (1549, 1366, 440), (1550, 1368, 416), (1551, 1370, 393),
            (1552, 1372, 362), (1554, 1378, 313), (1556, 1380, 285),
            (1558, 1384, 245), (1559, 1385, 235), (1560, 1386, 215),
            (1563, 1391, 176), (1566, 1395, 138), (1569, 1398, 108),
            (1570, 1400, 102)])


def test_it_lands_on_the_frame_the_ball_was_struck():
    for name, chain in (("run 2", RUN2), ("run 1", RUN1)):
        f, model = d3._impact_frame(chain, TEE_Y)
        check(f"{name}: within a frame of the truth",
              abs(f - TRUE) <= 1.0, True)
        check(f"{name}: and it says how it got there", model, "drag")


def test_the_straight_line_is_what_was_wrong():
    """The control: the same chains, the model that was there before.

    Eleven frames on one of them. This is not a tuning difference.
    """
    for name, chain in (("run 2", RUN2), ("run 1", RUN1)):
        _k = min(4, len(chain)) - 1
        _df = max(1, chain[_k]["frame"] - chain[0]["frame"])
        _vy = (float(chain[_k]["y"]) - float(chain[0]["y"])) / _df
        old = chain[0]["frame"] + (TEE_Y - float(chain[0]["y"])) / _vy
        check(f"control, {name}: the old model runs early",
              round(TRUE - old), 11)


def test_both_halves_of_the_error_are_fixed():
    """Six frames of it was the speed, four was the model.

    Worth pinning separately: fixing only the model would have left
    most of the error, and a later change that quietly goes back to
    reading speed off the first few points would put it back without
    failing anything else here.
    """
    chain = RUN2
    _k = min(4, len(chain)) - 1
    _vy = (float(chain[_k]["y"]) - float(chain[0]["y"])) / 3.0
    four = chain[0]["frame"] + (TEE_Y - float(chain[0]["y"])) / _vy
    whole = _straight_from_fitted_speed(chain)
    drag, _ = d3._impact_frame(chain, TEE_Y)
    check("four points at the bottom of the chain run 11 early",
          round(four - TRUE), -11)
    check("the chain's own speed gets 7 of them back",
          round(whole - four), 7)
    check("and the drag term the last 4", round(drag - whole), 4)
    check("leaving it on the frame", round(drag), TRUE)


def _straight_from_fitted_speed(chain):
    """Distance over speed, over the speed this module fits."""
    rate = _rate_samples(chain)
    n = float(len(rate))
    sx = sum(t for t, _ in rate)
    sy = sum(1.0 / u for _, u in rate)
    sxx = sum(t * t for t, _ in rate)
    sxy = sum(t / u for t, u in rate)
    k = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    u0 = 1.0 / ((sy - k * sx) / n)
    return chain[0]["frame"] - (TEE_Y - float(chain[0]["y"])) / u0


def _rate_samples(chain):
    span = d3.IMPACT_RATE_SPAN_FRAMES
    rate = []
    j = 0
    for i in range(len(chain)):
        j = max(j, i + 1)
        while j < len(chain) and chain[j]["frame"] - chain[i]["frame"] < span:
            j += 1
        if j >= len(chain):
            break
        _df = chain[j]["frame"] - chain[i]["frame"]
        _u = (chain[i]["y"] - chain[j]["y"]) / _df
        if _u > 0.5:
            rate.append(((chain[i]["frame"] + chain[j]["frame"]) / 2.0
                         - chain[0]["frame"], _u))
    return rate


def test_the_fit_is_of_something_real_but_not_tight():
    """1/speed against time, which is what drag does to a ball.

    AND IT IS LOOSE, which is the honest finding and is asserted here
    so nobody reads the result as a precise fit: R-squared 0.70, worst
    residual a quarter of the range. The scatter is the smear. The
    trend is the ball.
    """
    rate = _rate_samples(RUN2)
    n = float(len(rate))
    sx = sum(t for t, _ in rate)
    sy = sum(1.0 / u for _, u in rate)
    sxx = sum(t * t for t, _ in rate)
    sxy = sum(t / u for t, u in rate)
    k = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    c = (sy - k * sx) / n
    res = [1.0 / u - (c + k * t) for t, u in rate]
    ybar = sy / n
    r2 = 1.0 - (sum(r * r for r in res)
                / sum((1.0 / u - ybar) ** 2 for _, u in rate))
    check("the ball is slowing, not speeding up", k > 0, True)
    check("the trend is real", round(r2, 2), 0.70)
    check("and it is not a tight fit", r2 < 0.9, True)
    check("the speed it implies at the tee is a driven ball's",
          20 < 1.0 / (c + k * (TRUE - RUN2[0]["frame"])) < 80, True)


def test_it_cannot_run_away_backwards():
    """The property a straight line does not have.

    Back in time the model approaches a limit of 1/(k*u0) frames and
    stops. A straight line through a slow chain will happily report a
    strike a minute ago, which is how the old one got to f1525 -- and
    with a slower chain, very much further.
    """
    far = d3._impact_frame(RUN2, TEE_Y + 4000.0)
    near = d3._impact_frame(RUN2, TEE_Y)
    check("a tee line ten times further down",
          far[0] is not None, True)
    check("is not ten times further back",
          (RUN2[0]["frame"] - far[0]) < 3 * (RUN2[0]["frame"] - near[0]),
          True)
    # CONTROL: the straight line IS ten times further back.
    _vy = (RUN2[3]["y"] - RUN2[0]["y"]) / 3.0
    _f_far = RUN2[0]["frame"] + (TEE_Y + 4000.0 - RUN2[0]["y"]) / _vy
    _f_near = RUN2[0]["frame"] + (TEE_Y - RUN2[0]["y"]) / _vy
    check("control, a straight line is",
          (RUN2[0]["frame"] - _f_far) > 10 * (RUN2[0]["frame"] - _f_near),
          True)


def test_no_deceleration_falls_back_rather_than_blowing_up():
    """A chain that shows no slowing, or none the noise did not eat.

    Distance over speed is the right answer then -- over the speed
    fitted to the whole chain, not the first four points.
    """
    steady = pts([(100 + i, 500, 600 - 20 * i) for i in range(10)])
    f, model = d3._impact_frame(steady, 800.0)
    check("it still answers", model, "constant speed")
    check("and 200px at 20px/frame is ten frames back",
          round(f), 90)


def test_a_chain_that_speeds_up_is_not_extrapolated():
    """The guard that stops the model running away.

    Fitted the other way round -- 1/u falling with time -- the
    exponential goes backwards into a number with no physical meaning
    at all, and it does it quietly. A ball that appears to accelerate
    is noise or something that is not a ball; either way the answer is
    distance over speed, not a projection.
    """
    # Rates of 8, 10, 12, 14, 16, 18 px/frame: speeding up.
    y, rows = 600, []
    for i, u in enumerate((8, 10, 12, 14, 16, 18)):
        rows.append((100 + i, 500, y))
        y -= u
    rows.append((106, 500, y))
    f, model = d3._impact_frame(pts(rows), 900.0)
    check("it does not claim to have fitted drag", model, "constant speed")
    check("and the answer stays in this century",
          0 < (rows[0][0] - f) < 200, True)
    # CONTROL: the fit really does go the wrong way on this chain, so
    # the guard is what produced the answer above.
    _r = _rate_samples(pts(rows))
    _n = float(len(_r))
    _sx = sum(t for t, _ in _r)
    _sy = sum(1.0 / u for _, u in _r)
    _k = ((_n * sum(t / u for t, u in _r) - _sx * _sy)
          / (_n * sum(t * t for t, _ in _r) - _sx * _sx))
    check("control, the fitted slope is negative", _k < 0, True)


def test_two_speed_samples_are_not_a_trend():
    """A line through two points is not evidence that there is one."""
    # Three points three frames apart give exactly two speed samples.
    three = pts([(100, 500, 600), (103, 500, 540), (106, 500, 490)])
    check("control, that is two samples", len(_rate_samples(three)), 2)
    check("and two is not enough",
          d3._impact_frame(three, 900.0), (None, None))
    # CONTROL: one more point is, and then it answers.
    four = pts([(100, 500, 600), (103, 500, 540), (106, 500, 490),
                (109, 500, 450)])
    check("control, three samples is", len(_rate_samples(four)), 3)
    check("and then it answers",
          d3._impact_frame(four, 900.0)[0] is not None, True)


def test_it_refuses_what_it_cannot_measure():
    check("two points are not a flight",
          d3._impact_frame(pts([(10, 5, 90), (11, 5, 70)]), 300.0),
          (None, None))
    check("nor is a chain already below the tee line",
          d3._impact_frame(RUN2, 100.0), (None, None))


for fn in (test_it_lands_on_the_frame_the_ball_was_struck,
           test_the_straight_line_is_what_was_wrong,
           test_both_halves_of_the_error_are_fixed,
           test_the_fit_is_of_something_real_but_not_tight,
           test_it_cannot_run_away_backwards,
           test_no_deceleration_falls_back_rather_than_blowing_up,
           test_a_chain_that_speeds_up_is_not_extrapolated,
           test_two_speed_samples_are_not_a_trend,
           test_it_refuses_what_it_cannot_measure):
    print(f"\n-- {fn.__name__}")
    fn()

print("\n" + ("FAILED: " + ", ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
