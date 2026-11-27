#!/usr/bin/env python3
"""Two produces on one recording, and a page that is told about both.

ONE CLIP IS BUILT AT A TIME, site-wide: `workload.heavy_gate` is a
single lock, and every produce takes it. So plotting a second swing
while the first is rendering is QUEUED, not refused -- which is the
answer to "can I do that while I wait?" and worth saying on the page.

BUT THE STATUS IS ONE SLOT PER RECORDING. Both jobs wrote
`_debugx_state[("produce", upload_id)]`, and the first to finish set
`running=False` while the second was still waiting for the gate. The
page believed it, stopped polling, and the second clip landed in the
database with nothing watching for it: produced, saved, and in no
column. Work done and invisible.

AND A CLIP IS FILED BY THE SERVER, not by the browser. The flow's
columns show clips stamped `flow_run_at`; a wizard produce writes a
new row with no stamp, and the page used to add it from its own poll.
That works until the operator closes the tab -- so the produce that
was started from the flow page says so, and the job claims it when it
finishes.

    python3 tools/produce_queue_notice_test.py
"""
import inspect
import re
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


print(__doc__.strip().splitlines()[0])

print("\n1. the flag survives the first of two finishing")
A._debugx_set("produce", 4242, running=True, stage="Queued")
A._produce_began(4242)
check("one is out", A.produce_inflight(4242), 1)
A._produce_began(4242)
check("...two", A.produce_inflight(4242), 2)
# THE JOB SETS running=False ON ITS WAY OUT. It has no idea it has a
# sibling -- so without the count, this is where the page stopped
# watching.
A._debugx_set("produce", 4242, running=False, stage="done")
A._produce_ended(4242)
check("the first finishing leaves one", A.produce_inflight(4242), 1)
_st = A._debugx_get("produce", 4242) or {}
check("...and the recording still reads as producing", _st.get("running"))
check("...saying how many are left",
      "1 more to produce" in (_st.get("stage") or ""))
A._debugx_set("produce", 4242, running=False, stage="done")
A._produce_ended(4242)
check("the last one clears it", A.produce_inflight(4242), 0)
check("...and does not put the flag back",
      bool((A._debugx_get("produce", 4242) or {}).get("running")), False)

print("\n2. the count is never negative, however it is called")
A._produce_ended(4242)
A._produce_ended(4242)
check("an unmatched end is harmless", A.produce_inflight(4242), 0)

print("\n3. the status says how many are queued")
_src = inspect.getsource(F.flow_status)
check("flow_status reports the queue depth",
      'out["produce_queued"] = produce_inflight(upload_id)' in _src)

print("\n4. a produce from the flow page is claimed by the SERVER")
_wiz = inspect.getsource(A.wizard_produce)
check("the endpoint takes the flag",
      'payload.get("claim_for_flow")' in _wiz)
check("...and registers the recording",
      "_produce_claim_for_flow.add(int(upload_id))" in _wiz)
_end = inspect.getsource(A._produce_ended)
check("the last produce to finish claims it",
      "claim_produced_clips(int(upload_id))" in _end)
# ONLY WHEN THE FLOW ASKED. Producing the same recording from the
# Production page is not the flow's output, and claiming it would make
# `flow_run_at` mean "some clip of a recording the flow has touched".
check("...and only for a recording that asked",
      "_produce_claim_for_flow" in _end)
check("...which is forgotten once it is done",
      "_produce_claim_for_flow.discard(int(upload_id))" in _end)
# IT OPENS ITS OWN SESSION. The request's session is long gone: this
# runs on the produce thread, after the endpoint returned.
_claim = inspect.getsource(F.claim_produced_clips)
check("the claim has a session of its own", "SessionLocal()" in _claim)
check("...and closes it", "db.close()" in _claim)
check("...and is the same claim the page's button makes",
      "_claim_clips(db, row, cuts)" in _claim
      and "_one_clip_per_cut(db, upload_id)" in _claim)
# A FAILED CLAIM MUST NOT LOSE THE CLIP.
check("a claim that fails does not fail the produce",
      "could not claim upload" in _end)

print("\n5. and the page says all of it")
page = (ROOT / "frontend" / "src" / "pages"
        / "AdminProductionFlow.jsx").read_text()
prod = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text()
check("the wizard is told this is the flow", "claimForFlow" in page)
check("...and passes it to the produce",
      "claim_for_flow: !!claimForFlow," in prod)
# ── SAVE & CLOSE IS NOT A CANCEL ────────────────────────────────
# The wizard calls `onBackground` and then `onClose`. Reading that
# close as a cancel wiped the swing the save had just named, which is
# why the page fell back to the notice for a produce it knew nothing
# about -- on a produce it had started itself.
check("a save is told from a cancel", "backgrounded.current" in page)
check("...and only a cancel clears the producing swing",
      re.search(r"if \(!_saved\) \{\s*\n(\s*//[^\n]*\n)*\s*"
                r"setProducingCut\(null\);", page) is not None)
# ── AND IT SAYS SO ON THE CLICK, NOT ON THE NEXT POLL ───────────
# Everything else here waits for a status poll: a second of silence at
# best, five while the queue answers. Silence after pressing Save is
# indistinguishable from a save that did not register.
check("the note is set when the work is handed over",
      "onBackground={(msg) => {" in page)
check("...naming the swing and what happens next",
      "queued — " in page and "One clip is built at a time" in page)
check("...and what may be done meanwhile",
      "they queue behind this one" in page)
check("the producing tile says it too",
      "Keep working: plot another cut now and it" in page)
check("...and that closing the page does not lose it",
      "whether or not this page is open" in page)
# THE OLD COPY WAS A GUESS FROM ONE FACT -- no swing number in this
# tab -- and it was usually wrong.
check("nothing claims to know where a produce came from",
      "started somewhere other than this page" in page, False)
check("the editor names the swing it is about to rebuild",
      re.search(r"openEditor[\s\S]{0,900}?swing: clip\.cut_idx != null",
                page) is not None)

print("\n6. a watch does not end before the work has begun")
page = (ROOT / "frontend" / "src" / "pages"
        / "AdminProductionFlow.jsx").read_text()
# ── "WORKING, DONE, WORKING, DONE" FOR ONE CLIP ─────────────────
#
# "Nothing is running" is the state BEFORE a job as well as after it,
# and the watch read the first as the second. Save & close hands the
# work over several server calls before the produce is queued -- a
# tracer re-render, a finalize -- so the first status came back idle,
# the watch announced the produce finished, claimed nothing, and
# reloaded the OLD draft as though it were the new one. Then the
# produce started and said so all over again.
check("an idle status does not end a watch that saw nothing",
      "if (!(st?.busy ?? st?.running) && !seen) {" in page)
check("...it is only the end once something has been seen running",
      "if (st?.busy ?? st?.running) seen = true;" in page)
check("...and the wait for a start is bounded",
      "waitedMs < START_GRACE_MS" in page)
# AND WHEN IT RUNS OUT, IT SAYS SO. Reporting a clip that was never
# produced is the failure this whole watch exists to prevent.
check("...with nothing claimed if it never starts",
      "nothing started producing within" in page
      and "Nothing has been claimed." in page)
check("the grace is long enough for the calls that come first",
      "const START_GRACE_MS = 30000;" in page)

print("\n" + ("all ok" if not FAILED
               else "%d FAILED:\n  %s" % (len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
