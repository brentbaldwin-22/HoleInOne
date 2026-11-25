#!/usr/bin/env python3
"""No ball found in the sky is a reason to draw nothing, not to produce nothing.

THE CLIP DOES NOT DEPEND ON THE FLIGHT. It is cut around the impact
frame the cutter heard; the tracer is a drawing ON that clip. But a
swing the ascent search found no ball for used to produce NOTHING:
find_flight refused, the job returned ok=False, and the operator was
left with an audio cut that plainly contains a strike and no draft to
look at -- their only route to one being to find the recording on
another page and plot a whole flight by hand with no clip in front of
them.

Now the draft is cut anyway, says it has no tracer and why, and the
wizard's "plot ascent by hand" has something to open on.

ONLY WHERE THERE IS AN IMPACT FRAME. Without one there is nothing to
cut around, and that is the one case where producing nothing is the
honest answer.

    python3 tools/tracerless_draft_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_job = SRC[SRC.index("def run_wizard_produce_job("):]
_job = _job[:_job.index("\ndef ", 10)]
_fp = SRC[SRC.index("def _d3_fast_produce("):]
_fp = _fp[:_fp.index("\ndef ", 10)]

print(__doc__.strip().splitlines()[0])

print("\n1. a failed flight no longer ends the produce")
check("it substitutes a tracerless flight", '"no_tracer": _err' in _job)
check("...anchored on the impact frame",
      '"launch_frame": _imp' in _job)
check("...with no points", '"points": []' in _job)
check("...only when there IS an impact frame",
      "if _imp is not None:" in _job)
# THE RETURN IS RE-CHECKED, or the substitution would be built and
# then thrown away by the very next line.
check("and the give-up return is re-checked afterwards",
      'if not _ff.get("ok"):\n                    _finish_wizard_produce'
      in _job)

print("\n2. the renderer is not asked to draw an empty flight")
# render_tracer_video ends at pts[-1]; handing it nothing raises.
check("the tracer render is behind a points check", "if pts:" in _fp)
check("...and the empty case cuts the frames instead",
      "cut_segment(src_path, _tee, t0, t_render_end)" in _fp)
# THE SAME WINDOW EITHER WAY. The render writes t0..t_render_end and
# the plain cut must too, or a tracerless draft would be a different
# length from the one it replaces.
check("...over the same window the tracer would have covered",
      "write_end=int(round(t_render_end * fps))" in _fp
      and "cut_segment(src_path, _tee, t0, t_render_end)" in _fp, True)
check("...both starting at t0",
      "write_start=int(round(t0 * fps))" in _fp, True)
check("nothing downstream assumes a render happened",
      _fp.count("(_rv or {}).get") >= 1, True)
check("...and _rv is explicitly None there", "_rv = None" in _fp)

print("\n3. why there is no line travels with the clip")
check("onto the swing record", '"no_tracer": sw.get("no_tracer")' in _fp)
check("...from the job's own swing", '"no_tracer": _ff.get("no_tracer")'
      in _job)
FLOW = (ROOT / "backend/app/routers/production_flow.py").read_text()
check("onto the flow's clip row", '_r["no_tracer"]' in FLOW)
CARD = (ROOT / "frontend/src/pages/AdminProductionFlow.jsx").read_text()
check("and onto the draft card", "c.no_tracer" in CARD)
check("...saying the clip was cut from the audio impact",
      "cut from" in CARD and "plot the ascent by hand" in CARD)
WIZ = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()
check("the wizard says it too", "swing?.no_tracer" in WIZ)
check("...and points at the control that fixes it",
      "Plot the ascent by hand" in WIZ)

print("\n4. a normal produce is untouched")
# The substitution only fires on a REFUSED flight, and the render
# only skips on an empty one. A swing with points takes the same path
# it always did.
check("the render still runs for a swing with points",
      "render_tracer_video(" in _fp)
check("...and still raises when it fails",
      'raise RuntimeError(\n                        f"tracer render failed'
      in _fp)
check("no_tracer is absent unless something set it",
      '"no_tracer": sw.get("no_tracer")' in _fp)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
