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


def rep(measured=796, est=827, n_clips=1, ran=True):
    return {
        "ai_launch": {"ran": ran, "rest": {"x": 605, "y": 727},
                      "measured_impact_frame": measured, "points": []},
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
admin._ascent_apply_ai_launch(r, 0)
check("no measurement -> the estimate stands",
      r["clips"][0]["impact_frame"], 827)
check("  and nothing claims to be measured",
      r["clips"][0].get("impact_source"), None)
r = rep(ran=False)
admin._ascent_apply_ai_launch(r, 0)
check("stage 1 did not run -> the estimate stands",
      r["clips"][0]["impact_frame"], 827)
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
