#!/usr/bin/env python3
"""A produce that fails must not look like one that worked.

IT STOPS THE SAME WAY. The job sets `running=False`, puts "failed" in
the stage and the reason in `error`, and none of that reached the
page: `flow_status` passed on the stage and the counter WHILE RUNNING
and nothing at all once it stopped. So the watch saw the work end,
claimed nothing -- there was nothing to claim -- reloaded a column
that had not changed, and printed "produced."

What the operator saw was the notice disappear and no clip anywhere,
with the reason sitting in a dict two functions away. That is the
worst shape a failure can take: it reads as a result.

AND "WHAT TAKES SO LONG?" HAD NO ANSWER EITHER. Every ffmpeg and
render helper is wrapped in a timer, and every other produce path
resets the clock and keeps the breakdown. This one -- the one an
operator runs by hand and waits five minutes on -- threw it away.

    python3 tools/produce_outcome_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.admin as A  # noqa: E402
import app.routers.production_flow as F  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def clear():
    with A._debugx_lock:
        A._debugx_state.clear()


print(__doc__.strip().splitlines()[0])

print("\n1. a failed produce says so")
clear()
A._debugx_set("produce", 1762, running=False, stage="failed",
              error="no swing with a flight to produce")
st = F.flow_status(1762)
check("the page is told it failed", st.get("produce_failed"))
check("...with the reason", st.get("produce_error"),
      "no swing with a flight to produce")
check("...and is not told it is running",
      bool(st.get("produce_running")), False)
check("...nor that the FLOW is", bool(st.get("running")), False)
check("...and nothing is busy, so the watch ends",
      bool(st.get("busy")), False)

print("\n2. a produce still going is not a failure")
clear()
A._debugx_set("produce", 1762, running=True, stage="Rendering the clip",
              error=None)
st = F.flow_status(1762)
check("not failed", bool(st.get("produce_failed")), False)
check("...and busy, so the watch keeps asking", st.get("busy"))
check("...with the stage to show", st.get("produce_stage"),
      "Rendering the clip")

print("\n3. a produce that finished cleanly is not a failure either")
clear()
A._debugx_set("produce", 1762, running=False, stage="done", error=None)
st = F.flow_status(1762)
check("not failed", bool(st.get("produce_failed")), False)
check("...and no error invented", st.get("produce_error"), None)
# THE LAST STAGE SURVIVES THE RUN. "done" and "failed" are the two
# things worth knowing after the fact, and the live stage field is
# deliberately blanked when nothing is running.
check("the last stage is still readable",
      st.get("produce_last_stage"), "done")
check("...while the live one is blank", st.get("produce_stage"), None)
clear()

print("\n4. and the page acts on it")
page = (ROOT / "frontend" / "src" / "pages"
        / "AdminProductionFlow.jsx").read_text()
check("the watch checks before it claims",
      "if (st?.produce_failed) {" in page)
check("...says what failed and why",
      "the produce failed — " in page and "st.produce_error" in page)
# AND DOES NOT LEAVE THE PAGE CLAIMING SOMETHING IS COMING.
check("...drops the producing tile", "setProducingCut(null);" in page)
check("...and takes the recording off the adoption list",
      "adopting.current.delete(uploadId);" in page)
check("...and says nothing was written",
      "Nothing was written" in page)
# A RUN THAT WROTE NOTHING AND FAILED NOTHING IS STILL NOT "produced."
check("an empty success is not reported as a clip",
      "No new clip arrived" in page)

print("\n5. where the time went is measured, not guessed")
job = inspect.getsource(A.run_wizard_produce_job)
check("the clock is reset for this run", "_pt_reset()" in job)
check("...and the breakdown kept on the row",
      "_keep_produce_report(db, upload_id, {" in job)
check("...with seconds", '"phases": _secs,' in job)
# SECONDS AND COUNTS ANSWER DIFFERENT QUESTIONS. 62s of overlay is a
# mystery; 62s over six calls on three clips names an encode that is
# happening twice per clip.
check("...and call counts", '"phase_calls"' in job)
check("...and what was not a timed helper", '"untimed_sec"' in job)
# WAITING IS NOT WORKING. One clip is built at a time site-wide, so a
# produce behind another can sit for minutes doing nothing -- a
# different problem from a slow encode, wanting a different fix.
wiz = inspect.getsource(A.wizard_produce)
check("the queue wait is measured too",
      "_produce_clock.queued_sec = round(time.monotonic() - _t_queued, 2)"
      in wiz)
check("...and reported", '"queued_sec": getattr(_produce_clock' in job)
# `n_clips`, NOT `clips`: the panel reads `rep.clips` as a LIST and
# maps over it, so a count under that name is a TypeError in the one
# place an operator goes to find out what went wrong.
check("the count does not collide with the panel's clip list",
      '"n_clips": _n_ok,' in job and '"clips": _n_ok,' not in job)

print("\n6. and the panel shows it")
prod = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text()
check("there is a breakdown block", "Where the time went" in prod)
check("...only when there is something to show",
      "rep?.phases && Object.keys(rep.phases).length > 0" in prod)
check("...with the call count beside the seconds",
      "rep.phase_calls?.[k] ?? 1" in prod)
check("...and the waiting called what it is",
      "waiting its turn" in prod)
check("Fragment is imported for it", "Component, Fragment," in prod)

print("\n7. the preview overlay cannot cancel the clip")
prodsrc = prod
# THE LOG FROM 1762, TWICE OVER:
#   [POST] 499 .../1762/render-tracer-fast responseTimeMS=300013
# 300,013ms is the CLIENT's own five-minute abort. `saveAndClose`
# awaited that call bare, so when it did not come back the save threw
# and `wizardProduce` -- the call that actually writes the clip -- was
# never made. Five minutes of waiting, no clip, and no error anywhere
# the operator was looking.
check("the render is not awaited bare any more",
      "const fast = teeChanged\n        ? await api.renderWizardTracerFast"
      in prodsrc, False)
check("...it is caught", "fast = fallbackFast;" in prodsrc
      and "} catch (e) {" in prodsrc)
check("...and says so on the line being watched",
      "the preview overlay could not be rendered" in prodsrc)
check("...then produces anyway",
      prodsrc.index("producing the clip anyway")
      < prodsrc.index("await api.wizardProduce("))
# AND THE WORK JUST PLOTTED SURVIVES IT. The produce builds its
# flight from the MERGED track the preview hands back, so falling
# back to the saved one would quietly produce the old line.
check("the fallback merges the overrides in",
      "const mergedTrack = (() => {" in prodsrc)
check("...dropping what was cleared",
      "for (const f of cleared) byFrame.delete(Number(f));" in prodsrc)
check("...and the fallback uses it, not the saved track",
      "ball_track_frames: mergedTrack," in prodsrc)
# NINETY SECONDS, NOT FIVE MINUTES: this is a preview, and the
# produce draws its own tracer from the same points.
api_js = (ROOT / "frontend" / "src" / "api.js").read_text()
check("the wait is bounded to something a person will sit through",
      "timeoutMs: 90_000," in api_js)
check("...and not the five minutes that timed out",
      "timeoutMs: 5 * 60_000,\n    }),\n  finalizeWizardVideo" in api_js,
      False)

print("\n8. and the clip is not drawn twice")
# MEASURED, FROM THE PANEL THIS ADDED: 114.1s and 162.0s for one
# clip, against a wall clock of about 210s from Save & close. The
# gap, and most of the rest, was work that did not need doing.
#
# THE TRACER WAS RENDERED TWICE. The preview render draws the overlay
# across the clip (39-51s of those runs), and the produce then draws
# its own from the SAME points and overwrites the preview's
# `tracer_url` when it lands. It used to matter -- this path was
# render-tracer-fast, then finalize, then commit-clip, and finalize
# composited what the preview made. It has been one call to the
# produce for a while, and nothing composites it now.
check("the preview render is off", "const PRODUCE_PREVIEW_RENDER = false;"
      in prod)
check("...and the call is behind that switch",
      "if (PRODUCE_PREVIEW_RENDER && teeChanged) {" in prod)
# THE MERGE IT WAS FETCHED FOR IS DONE HERE INSTEAD. Same base track,
# same frames cleared, same last-write-per-frame.
check("...so the produce still gets the plotted track",
      "ball_track_frames: mergedTrack," in prod)

# AND THE CUT STOPS COSTING WHAT THE SWING'S POSITION COSTS. `-ss`
# after `-i` decodes from the start of the file: 91.1s in one of
# those runs for ONE call, 32.9s in the other, the difference being
# how far into the round the swing was. `cut_segment` has had the
# two-stage seek that fixes it for a while and the produce never
# passed it.
admin_src = inspect.getsource(A)
check("the produce's cuts pass a seek pad",
      admin_src.count("seek_pad_sec=SEEK_PAD_SEC"), 2)
from app.services.video import SEEK_PAD_SEC  # noqa: E402
check("the pad is long enough to clear a GOP", SEEK_PAD_SEC >= 2.0)
# ONE CONSTANT. The manual cutter had its own copy with its own
# comment; two numbers for one decision drift in opposite directions.
import app.routers.production_flow as _F  # noqa: E402
check("...and the hand cutter uses the same one",
      _F.MANUAL_SEEK_PAD_SEC, SEEK_PAD_SEC)

print("\n9. a throw in the wizard does not take the page")
# "THE ADD/EDIT WIZARD JUST WENT AWAY." React unmounts the whole tree
# when a render throws and nothing catches it. The Production page
# has caught this around the same modal for a while; the Production
# Flow page rendered it bare, so a single bad value inside took the
# route with it and left nothing to report.
check("the flow page wraps it", '<Boundary name="Click-to-plot"' in page)
check("...in the same boundary, not a second one",
      "export class Boundary extends Component {" in prod)
check("...and closes it", "</Boundary>" in page)
check("...with a way out that clears the edit",
      "onClose={() => { setEditing(null); setProducingCut(null); }}" in page)

print("\n" + ("all ok" if not FAILED
               else "%d FAILED:\n  %s" % (len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
