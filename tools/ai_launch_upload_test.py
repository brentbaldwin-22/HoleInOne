#!/usr/bin/env python3
"""Stage 1 of a find must not decline just because there is no rig event.

`_ascent_ai_launch` finds the golfer, measures the ball at rest and
works out the impact frame -- and every picture the operator checks on
the Find ascents screen comes from it. It opened with:

    _rig = _rig_impact_sec(db, row, ...)
    if _rig is None:
        out["reason"] = "...not cut around an impact the rig heard..."
        return out

and `_rig_impact_sec` returns None the instant `camera_event_id` is
unset. A MANUALLY UPLOADED clip never has one. So on every upload the
entire stage declined -- no golfer, no ball, no impact frame, none of
the pictures -- while the run carried on and reported "golfer: not
placed | ball and impact: not measured | not timed".

Three absences and no cause, because `reason` was set and the stage
line never printed it. That is how this survived: the stage that did
nothing looked exactly like a stage that found nothing.

An uploaded clip does have a heard strike -- the audio cutter finds
it, stage 2 of the same run narrows onto it, and Test Cut measures the
ball off that very number. So the fallback is not new information, it
is information the run already had.

    python3 tools/ai_launch_upload_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def check_in(label, needle, hay):
    ok = needle in (hay or "")
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (hay,)))
    if not ok:
        FAILED.append(label)


from app.routers import admin  # noqa: E402


class _Row:
    """An upload with no camera event -- i.e. a manual upload."""
    id = 1487
    camera_event_id = None


def _run(cut_times, lag, cutter=("audio", "set on cam:1-2"),
         event_id=None):
    """Call the real function with only the clip reads stubbed out.

    Everything up to and including the impact decision is the real
    code; `probe_video_info` then reports no frame size, which is an
    early return the function already has. So this measures exactly
    one thing: whether the stage got past the rig check at all.
    """
    saved = {}
    for name, val in [
        ("_rig_impact_sec", lambda *a, **k: None),
        ("_cutter_for", lambda *a, **k: cutter),
        ("_cutter_times", lambda *a, **k: list(cut_times)),
        ("_trigger_zone", lambda *a, **k: None),
        ("_gate_tuning_for", lambda *a, **k: ({"audio_lag_sec": lag},
                                              None, None)),
        ("probe_video_info", lambda *a, **k: {"width": 0, "height": 0}),
    ]:
        saved[name] = getattr(admin, name, None)
        setattr(admin, name, val)
    _row = _Row()
    _row.camera_event_id = event_id
    _called = []
    _ct = getattr(admin, "_cutter_times")

    def _spy(*a, **k):
        _called.append(a[:1])
        return list(cut_times)
    admin._cutter_times = _spy
    try:
        out = admin._ascent_ai_launch(
            object(), _row, "/nonexistent.mp4", 30.0, {}, None)
        out["_cutter_called"] = bool(_called)
        return out
    finally:
        admin._cutter_times = _ct
        for name, val in saved.items():
            if val is not None:
                setattr(admin, name, val)


print("an upload with no rig event, cutter hears a strike at 24.80s")
out = _run([24.80], 0.0)
# THE BUG: this used to be the rig sentence, and impact_frame None.
check("got past the rig check (impact_frame set)",
      out.get("impact_frame"), int(24.80 * 30.0))
check_in("declines for the frame size, not the missing rig",
         "no frame size", out.get("reason"))
check_in("says where the impact came from", "audio cutter",
         out.get("impact_from"))

print()
print("the operator's tuned audio lag is applied here too")
out = _run([24.80], 0.60)
check("impact frame is the strike, not the sound",
      out.get("impact_frame"), int((24.80 - 0.60) * 30.0))
check_in("names the lag it applied", "0.60s audio lag",
         out.get("impact_from"))

print()
print("nothing heard at all -- still declines, and says so")
out = _run([], 0.0)
check("no impact frame", out.get("impact_frame"), None)
check_in("blames the cutter, not the rig", "heard no strike",
         out.get("reason"))

print()
print("a hole set to sweep the whole clip")
out = _run([24.80], 0.0, cutter=("none", "set on cam:1-2"))
check("no impact frame", out.get("impact_frame"), None)
check_in("says it was a choice", "set to sweep the whole clip",
         out.get("reason"))

print()
print("A DWELL CLIP GETS THE SAME TREATMENT")
# It has a camera event and no heard strike, and this used to refuse
# it on the reasoning `_ascent_windows` gives for refusing the audio
# pass there -- a missed swing narrows the search onto the wrong two
# seconds instead of widening, which costs a swing.
#
# That reasoning is about THE SWEEP WINDOW. Stage 1 does not set one:
# it chooses a frame to zoom into and measure the ball at rest on, and
# `_win` is untouched either way. The worst a wrong instant does here
# is aim the pictures at the wrong moment, which is visible in the
# pictures. No pictures at all is not visible in anything.
out = _run([24.80], 0.0, event_id=42)
check("the cutter is asked anyway", out.get("_cutter_called"), True)
check("it gets an impact frame", out.get("impact_frame"),
      int(24.80 * 30.0))
check_in("and the picture says which clock it came from",
         "audio cutter", out.get("impact_from"))
check_in("and that the rig had nothing", "no rig impact",
         out.get("impact_from"))

print()
print("a manual upload, the same")
out = _run([24.80], 0.0, event_id=None)
check("the cutter is asked", out.get("_cutter_called"), True)

print()
print("nothing heard anywhere still names the fallback")
# `ai_launch_plot_test` holds this sentence: a stage that declines
# must say what starts the tracer line instead of it.
out = _run([], 0.0, event_id=42)
check("no impact frame", out.get("impact_frame"), None)
check_in("says what happens instead", "back to the tee line",
         out.get("reason"))

print()
print("the reason is PRINTED, not just stored")
src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
_i = src.index('"ball(s) measured at rest"')
_stage = src[max(0, _i - 4000):_i]
check('stage 1 prints ai_launch["reason"]',
      'DID NOT RUN: ' in _stage, True)
check('stage 1 prints ai_launch["impact_from"]',
      'impact from: ' in _stage, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok")
