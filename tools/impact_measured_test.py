#!/usr/bin/env python3
"""A measured impact frame beats one run back from the chain.

THE BUG THIS PREVENTS, reported after the first clean produce: the
edit wizard showed the impact at f827 while the strike was near f796.

Produce gets that number from `_ascent_spot_from_chain`, which runs
the chain back along its own heading to the tee line. It is an
estimate and says so, and its own docstring states the safety
property it relies on:

    "it lands EARLY -- measured on real ascents, 8 to 23 frames
     before the strike... Early is the harmless direction: it is
     where the drawn line begins, and a line that starts a fraction
     before the ball moves reads as a tracer, while one that starts
     after it has gone reads as a bug."

On that swing it landed 31 frames LATE -- the direction the same
comment calls a bug. The estimate is clamped never to exceed the
chain's first detected point, and f827 is that point: the first frame
the ball was seen in the band ABOVE the skyline, half a second after
it left the tee. The run back had nothing to give and the clamp stood
in for it.

`_ascent_apply_ai_launch` already fixes the other half of exactly this
problem, and writes the rule down: the ball at address is an
OBSERVATION of the thing the chain only ESTIMATES, so it becomes the
tee spot. The frame was never taken, although stage 1 measures it the
same way -- watching the ball sit, and watching it go.

Only the matched swing may take it: stage 1 measures around ONE heard
instant, so its frame means nothing for any other chain on the clip.

    python3 tools/impact_measured_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def rep(measured=796, est=827, n_clips=1, measured_anything=True):
    """`measured_anything` False means stage 1 attached NOTHING --
    no ball at rest, no frame, no points. That, and not a leftover
    `ran` flag, is what must stop the merge: the flag was false on
    every run for months because the model that set it was deleted,
    and it silently switched the whole merge off."""
    _ai = {"ran": measured_anything, "points": []}
    if measured_anything:
        _ai["rest"] = {"x": 605, "y": 727}
        _ai["measured_impact_frame"] = measured
    else:
        _ai["rest"] = None
        _ai["measured_impact_frame"] = None
        _ai["reason"] = "stage 1 measured nothing"
    return {
        "ai_launch": _ai,
        "clips": [{"ball": [600, 720], "impact_frame": est}
                  for _ in range(n_clips)],
        "ascents": [{"points": []} for _ in range(n_clips)],
    }


print("the measured frame replaces the estimate:")
r = rep()
out = admin._ascent_apply_ai_launch(r, 0)
c = r["clips"][0]
check("it applied", out.get("applied"), True)
check("  the clip carries the MEASURED frame", c["impact_frame"], 796)
check("  the estimate is kept beside it", c.get("impact_frame_est"), 827)
check("  and it says where the number came from",
      "measured" in (c.get("impact_source") or ""))
check("  recording how far it moved",
      out.get("impact_moved_frames"), 31)

print()
print("ONLY the swing the strike was matched to:")
r = rep(n_clips=3)
admin._ascent_apply_ai_launch(r, 1)
check("the matched clip takes it", r["clips"][1]["impact_frame"], 796)
check("  the one before keeps its own estimate",
      r["clips"][0]["impact_frame"], 827)
check("  and so does the one after",
      r["clips"][2]["impact_frame"], 827)

print()
print("and it never invents one:")
r = rep(measured=None)
r["ai_launch"]["measured_impact_frame"] = None
admin._ascent_apply_ai_launch(r, 0)
check("no FRAME measured -> the estimate stands",
      r["clips"][0]["impact_frame"], 827)
check("  and nothing claims to be measured",
      r["clips"][0].get("impact_source"), None)
r = rep(measured_anything=False)
out = admin._ascent_apply_ai_launch(r, 0)
check("stage 1 measured nothing -> the estimate stands",
      r["clips"][0]["impact_frame"], 827)
check("  and it says so", out.get("applied"), False)

# THE DEAD FLAG ITSELF. `ran` was false on every run while the stage
# was measuring perfectly well, so the merge must not consult it.
r = rep()
r["ai_launch"]["ran"] = False
admin._ascent_apply_ai_launch(r, 0)
check("a false `ran` does NOT block a real measurement",
      r["clips"][0]["impact_frame"], 796)
r = rep()
out = admin._ascent_apply_ai_launch(r, None)
check("no swing matched -> nothing applied", out.get("applied"), False)
check("  and the estimate is untouched",
      r["clips"][0]["impact_frame"], 827)

print()
print("the estimate still says it is one:")
src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
_i = src.index("def _ascent_spot_from_chain")
_j = src.index("\ndef ", _i + 10)
check("the chain run-back marks itself estimated",
      '"estimated"' in src[_i:_j])

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the watched frame wins over the extrapolated one")

# ── THE FLAG THAT WAS NEVER SET ─────────────────────────────────────
#
# A dead flag is invisible: it does not raise, does not log, and reads
# as "that stage had nothing to say". This one switched off a merge
# that had been working, and the only symptom was a tracer starting
# half a second late. So the flag is checked directly -- it must be
# set by something, on some path, or it means nothing again.
print()
print("the `ran` flag is actually set, not just initialised:")
_s1 = src[src.index("def _ascent_ai_launch"):]
_s1 = _s1[:_s1.index("\ndef ", 10)]
_sets = [ln.strip() for ln in _s1.split("\n")
         if 'out["ran"]' in ln and "=" in ln]
print("   assignments: %s" % " | ".join(_sets))
check("it is assigned more than once", len(_sets) > 1)
check("  and at least one sets it from a real measurement",
      any("measured" in ln for ln in _sets))

print()
print("and the merge does not depend on the flag at all:")
_m = src[src.index("def _ascent_apply_ai_launch"):]
_m = _m[:_m.index("\ndef ", 10)]
check("the gate asks for the values it uses",
      'if not (_ai.get("rest")' in _m)
check("  not for `ran`", 'not _ai.get("ran")' in _m, False)

if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — and the flag is alive")

# ── AND WHEN THE RIG HEARD NOTHING ──────────────────────────────────
#
# THE SECOND GATE, which was the one actually closing. The merge takes
# a clip index from `_ascent_pin_rig_impact`, and that returns None on
# every clip the rig did not hear -- a dwell trigger, or ANY manual
# upload. So the merge attached nothing on those clips whatever the
# flag above said, and the operator saw a correct f797 on the panel
# beside an f824 on the clip.
#
# Stage 1 knows which swing it measured. It does not need the
# microphone to say so.
print()
print("the swing is matched from the measurement when the rig is silent:")


def _rep_multi(measured, frames):
    return {"ai_launch": {"measured_impact_frame": measured,
                          "rest": {"x": 605, "y": 727}, "points": []},
            "clips": [{"impact_frame": f, "ball": [600, 720]}
                      for f in frames],
            "ascents": [{"points": []} for _ in frames]}


r = _rep_multi(797, [824])
i, why = admin._match_swing_to_measurement(r, 60.0)
check("one swing, matched", i, 0)
check("  and it says how", "27 frame(s)" in (why or ""))

r = _rep_multi(797, [400, 824, 2000])
i, _ = admin._match_swing_to_measurement(r, 60.0)
check("the NEAREST swing in time wins", i, 1)

# A swing ninety seconds away is still the nearest one. It is not this
# swing, and "nearest" must not be allowed to say that it is.
r = _rep_multi(797, [6000])
i, why = admin._match_swing_to_measurement(r, 60.0)
check("too far -> no match rather than the nearest", i, None)
check("  and says why", "too far" in (why or ""))

r = _rep_multi(None, [824])
check("no measurement -> no match",
      admin._match_swing_to_measurement(r, 60.0)[0], None)
check("no swings -> no match",
      admin._match_swing_to_measurement(
          {"ai_launch": {"measured_impact_frame": 797}, "clips": []},
          60.0)[0], None)

print()
print("and the produce path uses it when the rig gave nothing:")
check("the rig's index is tried first",
      '_ci = (_pin or {}).get("clip_index")' in src)
check("  and the measurement is the fallback",
      "_match_swing_to_measurement(rep, fps)" in src)
check("  with how it matched recorded",
      'rep["ai_launch_match"]' in src)

if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the measurement finds its own swing")
