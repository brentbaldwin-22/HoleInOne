#!/usr/bin/env python3
"""A measured tee spot is not an estimate, and is not clamped.

WHY THIS EXISTS. Stage 1 of the production run now measures two things
off the pixels -- where the ball was sitting and the frame it left --
and hands them down as an ascent entry. Everything after it reads those
through `_ascent_spot_from_chain`, which was written for the OTHER kind
of entry: a chain found over the whole band, whose origin is a run back
along its own heading and whose frame is a fitted deceleration. That
path corrects for a linear run back overshooting, by clamping the frame
to the first frame the chain was seen on.

Applied to a measurement, that correction is wrong in a way nothing
would notice: find_ascents starts its search at impact-2, so a real
chain's first point can sit BEFORE the measured impact, and the clamp
would quietly drag the measured frame backwards to fit it. The tracer's
line would then start before the ball moved -- which is the symptom the
clamp exists to prevent, arrived at from the other side.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    from app.routers.admin import _ascent_spot_from_chain

    # ── a MEASURED entry: stage 1's column search ────────────────────
    _measured = {
        "from_x": 870, "from_y": 612, "from_frame": 1536,
        "from_is_approx": False, "from_model": "measured",
        "first_frame": 1534,
    }
    out = _ascent_spot_from_chain(
        {"ball": None, "impact_frame": None, "reason": None},
        _measured, _measured["first_frame"])
    ok("a measured spot comes through as measured", out["ball"], [870, 612])
    ok("  and the frame is the measured one, not the chain's first",
       out["impact_frame"], 1536)
    ok("  and it does not call itself an estimate", out["estimated"], False)
    ok("  and says the ball was seen, not traced",
       "seen sitting" in out["reason"], True)
    ok("  and names the model that produced it",
       out["from_model"], "measured")

    # ── the SWEEP's entry: a run back, which is still clamped ────────
    _swept = {
        "from_x": 870, "from_y": 612, "from_frame": 1540,
        "from_is_approx": True, "from_model": "drag",
        "first_frame": 1536,
    }
    out2 = _ascent_spot_from_chain(
        {"ball": None, "impact_frame": None, "reason": None},
        _swept, _swept["first_frame"])
    ok("a run back is still clamped to the frame it was seen on",
       out2["impact_frame"], 1536)
    ok("  and still calls itself an estimate", out2["estimated"], True)
    ok("  and says it was traced back",
       "traced back" in out2["reason"], True)

    # ── and an entry with no origin at all still refuses ─────────────
    out3 = _ascent_spot_from_chain(
        {"ball": None, "impact_frame": None, "reason": None},
        {"from_is_approx": False}, None)
    ok("no origin is refused, not invented", out3["ball"], None)
    ok("  and says why", "no origin" in out3["reason"], True)

    # ── AND THE MICROPHONE DOES NOT OVERWRITE A MEASUREMENT ─────────
    #
    # `_ascent_pin_rig_impact` was written to replace a frame RUN BACK
    # along a chain's heading with the instant the rig heard, which is
    # the better of those two. It is not better than a watched
    # departure: the heard instant is the strike minus a fixed lead,
    # and that lead is why the ball is still visible at 0.0 on every
    # Test Cut sequence. Applied to a measurement it would put impact
    # a few frames before the ball left, on every swing.
    from app.routers import admin as _ad

    _calls = {}

    def _fake_rig(db, row, rid):
        _calls["asked"] = True
        return {"sec": 51.20, "heard_sec": 51.35, "lead_sec": 0.15}

    _real = _ad._rig_impact_sec
    _ad._rig_impact_sec = _fake_rig
    try:
        # f1536 at 30fps, and the rig's instant is 51.20s = f1536.
        # Move the measurement two frames off it so the two can be
        # told apart in the result.
        _rep = {"clips": [{"impact_frame": 1534, "estimated": False,
                           "ball": [870, 612]}]}
        _n = _ad._ascent_pin_rig_impact(None, None, _rep, 30.0)
        ok("a measured impact is not replaced by the heard instant",
           _rep["clips"][0]["impact_frame"], 1534)
        ok("  and the pin says it did not apply", _n["applied"], False)
        ok("  and reports the gap as this rig's audio lag",
           _rep["clips"][0]["audio_lag_frames"], 2)
        ok("  and keeps the heard frame beside it",
           _rep["clips"][0]["impact_frame_heard"], 1536)

        # AND AN ESTIMATE IS STILL REPLACED, which is the behaviour
        # every swing before stage 1 depended on.
        _rep2 = {"clips": [{"impact_frame": 1525, "estimated": True,
                            "ball": [870, 612]}]}
        _n2 = _ad._ascent_pin_rig_impact(None, None, _rep2, 30.0)
        ok("an estimated impact is still taken from the rig",
           _rep2["clips"][0]["impact_frame"], 1536)
        ok("  and the pin says it applied", _n2["applied"], True)
    finally:
        _ad._rig_impact_sec = _real

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
