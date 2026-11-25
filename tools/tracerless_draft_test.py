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

print("\n4. the renderer accepts a swing with no flight")
# THE BUG THIS SECTION EXISTS FOR, and the reason every check above
# it passed while nothing worked: the substitution was built, the
# "cutting a tracerless draft" line was logged -- and then the
# renderer's own swing filter, which required a flight
# unconditionally, dropped the swing and returned "no swing with a
# flight to produce". Sections 1-3 read the source; this one asks the
# predicate.
from app.routers.admin import _d3_producible_swing as _ok  # noqa: E402

_traced = {"ball": [900, 700], "launch_frame": 1930, "impact_frame": 1920,
           "flight": [{"frame": 1930, "x": 900.0, "y": 690.0},
                      {"frame": 1936, "x": 905.0, "y": 600.0}]}
check("a traced swing is producible", _ok(_traced))
check("a swing with only an impact frame is producible",
      _ok({"impact_frame": 1920, "flight": [], "no_tracer": "nothing found"}))
check("...with no ball at all", _ok({"impact_frame": 1920}))
check("...and no launch frame", _ok({"impact_frame": 1920, "flight": []}))
check("a swing with nothing to cut around is NOT",
      _ok({"ball": [900, 700], "flight": []}), False)
check("...nor one that is not a swing record at all", _ok(None), False)
check("an impact frame of f0 still counts",
      _ok({"impact_frame": 0}), True)

print("\n5. no chain is not no swing")
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
# A CHAIN PER SWING, POSITIONALLY. `_asc` holds only the chains that
# were FOUND, so indexing it by swing number paired an earlier swing
# with a later swing's line the moment any swing came back empty --
# a clip cut at one strike with another strike's tracer on it.
check("the per-swing chain list exists", "_asc_by_swing" in _run)
check("...and gets a slot even where nothing was found",
      "_asc_by_swing.append(None)" in _run)
_run_code = "\n".join(
    _l.split("#", 1)[0] for _l in _run.splitlines())
check("nothing pairs swings against the found-only list",
      "_asc[" not in _run_code, True)
# AND THE RUN CARRIES ON. This returned the moment no chain was
# found anywhere, which threw away the strikes along with the lines.
check("an empty search no longer ends a produce",
      "if not _asc and not (produce and rep[\"swings\"]):" in _run)
check("the tee-spot row is built per swing, not per chain",
      'for i, _swing in enumerate(rep["swings"]):' in _run)
check("...and says why a swing has no line",
      '_entry["no_tracer"]' in _run)
check("step 4 no longer skips a swing with no ball",
      'if not c.get("ball"):\n            continue' not in _run, True)
check("...it skips only one with no impact frame",
      'if c.get("impact_frame") is None:' in _run)
check("and hands the reason to the job", "no_tracer=_tracerless," in _run)

print("\n6. nothing is invented to fill the gap")
# A ball nobody measured, written onto the record, is the worst
# possible repair: `_d3_save_swing` merges with update(), so it would
# outlive the run and the wizard would open on a placed ball.
check("the job refuses a missing ball unless it is deliberate",
      'if ball_xy is None and not no_tracer:' in _job)
check("the swing it builds carries None, not [None, None]",
      '"ball": ([_bx, _by] if _bx is not None else None),' in _job)
check("the saved record omits the ball rather than nulling it",
      'if ball:\n                _sw_rec["ball"] =' in _fp)
check("...and the ascent run's record does too",
      'if c.get("ball"):\n                _rec["ball"] =' in _run)
# max() of an empty flight raises, several hundred lines before the
# render branch that knows what to do with one.
check("the timing block survives an empty flight",
      'if pts\n                           else int(launch_f))' in _fp)

print("\n7. a normal produce is untouched")
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
