#!/usr/bin/env node
/*
 * Editing or adding a swing from the flow page is wired end to end.
 *
 * THE SHAPE OF BOTH BUGS THIS GUARDS: a step that is present and a
 * step that is missing, with nothing on screen to tell them apart.
 *
 * The wizard's produce returns as soon as the job is QUEUED. A page
 * that reloads its columns on that return reads the clip that is
 * still being replaced, and then never asks again -- so the edit
 * looks like it did nothing. And the produce writes a NEW clip row
 * with no flow stamp on it, so even once it finishes, a column
 * filtered on that stamp does not show it -- which looks like the
 * edit having deleted the clip.
 *
 * Both are wiring, in three files, and the only place the names
 * appear together is here.
 *
 *   node tools/flow_edit_wiring_test.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(58)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

const page = readFileSync(
  join(ROOT, "frontend/src/pages/AdminProductionFlow.jsx"), "utf8");
const apijs = readFileSync(join(ROOT, "frontend/src/api.js"), "utf8");
const flow = readFileSync(
  join(ROOT, "backend/app/routers/production_flow.py"), "utf8");
const prod = readFileSync(
  join(ROOT, "frontend/src/pages/AdminProduction.jsx"), "utf8");

console.log("\n1. the editor closing starts a watch, not just a reload");
// `load()` alone is the bug: it reads the draft mid-render.
check("onDone polls the recording", /onDone=\{\([\s\S]{0,900}?poll\(_id\)/
  .test(page));
check("onBackground does too",
      /onBackground=\{\(\)[\s\S]{0,400}?poll\(_id\)/.test(page));
// A SAVE CAN BE REFUSED, and the modal is gone by then. The arguments
// carried the reason all along and the page dropped them.
check("a refused save surfaces its reason",
      /onDone=\{\(ok, msg\)/.test(page) && /setEditErr\(msg/.test(page));
check("...and does not then wait for a render that is not coming",
      /if \(ok === false\) \{[\s\S]{0,200}?return;/.test(page));

console.log("\n2. the status the page polls knows about a wizard produce");
check("the endpoint reads the produce's own state",
      /_debugx_get\("produce", upload_id\)/.test(flow));
// IT USED TO FOLD THE TWO INTO ONE `running`, which is how a
// hand-made produce greyed out stage 1 and put RUNNING on the raw
// card — the page appearing to go backwards for work that belongs at
// stage 3. There are three flags now and `busy` is the folded one,
// which is what the poller reads.
check("...and folds them into `busy` for the poller",
      /out\["busy"\] = bool\(out\.get\("running"\)\) or _busy/.test(flow));
check("...while leaving the flow's report alone",
      /out\["report"\]\s*=/.test(flow), false);
check("...and reports no stale stage once it stops",
      /out\["produce_stage"\] = _p\.get\("stage"\) if _busy else None/
        .test(flow));

console.log("\n3. the new clip is adopted, and only when asked for");
check("the flow has a claim endpoint",
      /@router\.post\("\/raw\/\{upload_id\}\/claim"\)/.test(flow));
check("the api can call it", /flowClaimClips:/.test(apijs));
check("the page calls it when the produce it started finishes",
      /adopting\.current\.has\(uploadId\)[\s\S]{0,400}?flowClaimClips/
        .test(page));
// NOT ON EVERY POLL. A clip produced from the Production page is not
// this flow's output, and claiming it would make the stamp mean
// "some clip of a recording the flow has touched".
check("...and marks the recording only from the editor",
      /adopting\.current\.add\(_id\)/.test(page));
check("...once, not again on the next tick",
      /adopting\.current\.delete\(uploadId\)/.test(page));
// A FAILED CLAIM IS NOT SILENT: the clip exists either way, it is
// just not in this column, and that needs saying.
check("a claim that fails says so", /could not[\s\S]{0,40}?claim it/
  .test(page));
// AND THE STAMP IS NOT RE-DATED. Its value answers "when did the flow
// produce this".
check("an existing stamp is kept",
      /if c\.flow_run_at is None:/.test(flow));

console.log("\n4. adding a swing uses the same map, pointed past the end");
check("the page can open it blank", /function openAdder\(/.test(page));
check("...one past the last swing", /swingPos: null, addNew: true/
  .test(page));
check("...and the modal is told", /addNew=\{!!editing\.addNew\}/.test(page));
// The modal's own rule, read from its source: a flag, not a length
// comparison made at click time.
check("the modal treats that as new",
      /const isNew = !!addNew \|\| swingPos == null/.test(prod));
check("the drafts column offers it", /\+ add a swing/.test(page));
// IN THE FOOTER, NOT AS CONTENT. Counted as a child, the button would
// make an empty column claim to have something in it and the "no
// drafts" line would never appear.
// ── AND AN EMPTY COLUMN STILL SAYS IT IS EMPTY ─────────────────
// A control in a column is not content in it: counted as a child,
// the "add a swing" button would make an empty drafts column claim
// to hold something, and "no drafts" — the line that tells an empty
// column from one still loading — would never appear. That is why
// the footer sits outside the empty test.
check("...without an add button making an empty column look full",
      /function Cell\(\{ children, empty, live, center, footer \}\)/
        .test(page)
        && /\{\/\* OUTSIDE THE EMPTY TEST[\s\S]{0,500}?\{footer\}/
          .test(page));
check("...and the add button is in that footer",
      /footer=\{r \? \([\s\S]{0,600}?\+ add a swing/.test(page));

console.log("\n5. and the produce's own work is shown, not re-run");
// THE FLOW'S PRODUCE *IS* PRODUCTION'S PRODUCE -- one function, with
// only the progress key differing -- so the panel that explains it
// must be the same panel. A second one would drift from it, and the
// drift would show up as two screens disagreeing about one run.
check("Production exports its panel",
      /export function AscentProduceModal/.test(prod));
check("...and the flow page imports that one",
      /import \{ AscentProduceModal, ClickToPlotModal \}/.test(page));
check("...rather than defining its own",
      /function AscentProduceModal/.test(page), false);
check("the flow serves the kept report",
      /@router\.get\("\/raw\/\{upload_id\}\/produce-report"\)/.test(flow));
check("the api can read it", /flowProduceReport:/.test(apijs));
check("the button is on the recording", /show work/.test(page));
// ONLY WHEN THERE IS SOMETHING TO SHOW. A button that opens an empty
// panel is worse than no button.
check("...and only when a report exists",
      /\{r\.produce_report_at && \(/.test(page));
check("...which the list says", /"produce_report_at":/.test(flow));
// THE PANEL READS `running` TO DECIDE WHETHER TO NARRATE PROGRESS.
// A kept report is never running.
check("the panel is told the run has ended",
      /running: false, uploadId: work\.uploadId/.test(page));
// ABSENT IS NOT BROKEN: a recording produced before the report was
// kept has nothing to show, and the endpoint says so.
check("an absent report is explained, not failed",
      /no produce report is kept/.test(flow)
        && /got\?\.reason/.test(page));

console.log("\n6. the five things asked for on this page");
// ── THE DRAFT IS A LINK, NOT A PLAYER ───────────────────────────
// An inline <video> per draft is a decode and a poster fetch per
// card, four cards to a recording, and it answers nothing the
// numbers under it do not: the question at this column is "is this
// the right swing", which the frames and the impact answer.
check("the draft opens in the viewer like the other angles",
      /Draft Production/.test(page));
// SCOPED TO THE DRAFT CARD. The FINAL column still has its player,
// which was not part of this and is a different question: an
// approved clip is something you watch, a draft is something you
// check against the frames under it.
const draftCard = page.slice(page.indexOf("const draftCard = "),
                             page.indexOf("const finalCard = "));
check("the draft card was found", draftCard.length > 200);
check("...with no inline player left on it",
      /poster=\{c\.thumbnail_url/.test(draftCard), false);
check("...and the final card keeps its own",
      /poster=\{c\.thumbnail_url/.test(page));

// ── THE ZONE BY ITS NAME ────────────────────────────────────────
// "Zone 2" is the rig's count; the operator drew the box and typed
// "middle" on it. The index stays in brackets because the rig counts
// from 0 and the editor labels from 1, which is the whole reason
// zones.js exists.
check("the raw card names the zone", /zoneName\(r\.trigger_zone, r\.trigger_zone_label\)/
  .test(page));
check("...and so does the cut card",
      /zoneName\(k\.trigger_zone, k\.trigger_zone_label\)/.test(page));
check("the server resolves the name", /_trigger_zone_label/.test(flow));
const adminpy = readFileSync(
  join(ROOT, "backend/app/routers/admin.py"), "utf8");
const labelFn = adminpy.slice(
  adminpy.indexOf("def _trigger_zone_label("),
  adminpy.indexOf("def _trigger_zone(db, row):"));
check("...from the event's own label first",
      labelFn.indexOf('_d.get("zone_label")') > 0
        && labelFn.indexOf('_d.get("zone_label")')
           < labelFn.indexOf("tee_box_roi_at_trigger"));
// A BOX REDRAWN SINCE IS A DIFFERENT PIECE OF GRASS, so the drawing
// the event kept beats the camera's current one.
check("...then the boxes as they were at the trigger",
      labelFn.indexOf("tee_box_roi_at_trigger")
        < labelFn.indexOf('getattr(cam, "tee_box_roi"'));

// ── FILTER BY DAY AND TEE BOX ───────────────────────────────────
check("the page filters by day", /setDay\(/.test(page));
check("...and by tee box", /setZone\(/.test(page));
check("...server-side, because the list is paginated",
      /api\.flowRaw\(adminPassword, \{ limit: 40, \.\.\._f \}\)/
        .test(page));
// THE DAY IS THE COURSE'S OWN, not UTC: an evening round is one day
// to everybody standing there and two in UTC. Worked out with
// zoneinfo, so the two days a year that are 23 or 25 hours long come
// out right without this having to know about them.
check("the day is the course's own, not UTC",
      /ZoneInfo\(GROUP_TZ\)/.test(flow));
// ZONE 0 IS A ZONE. A falsy test drops the box printed "Zone 1" and
// silently widens the filter to everything.
check("zone 0 is sent, not treated as 'all'",
      /zone != null && zone !== ""/.test(apijs));
check("the day list comes from every recording, not the page",
      /"days": \[\{"day": k, "n": v\}/.test(flow));
// ── THE FILTER NARROWS THE QUERY, NOT THE PAGE ──────────────────
// Run over the page already taken, choosing any day older than the
// newest forty found nothing while the dropdown went on offering it.
check("the day narrows the query before the limit",
      /_flow_filter\(db, course_id, day, zone\)[\s\S]{0,400}?\.offset\(/
        .test(flow));
check("...in UTC bounds worked out from the local day",
      /_day_bounds_utc/.test(flow));
check("...and the zone resolves its ids in two queries, not per row",
      /def _zone_upload_ids/.test(flow)
        && /CameraEvent\.id\.in_\(_ev_ids\)/.test(flow));
// ── AND ALL FOUR COLUMNS TAKE IT ────────────────────────────────
// Sent on the raw list alone, the page said "No recordings" beside a
// cut card and a draft from a fortnight earlier.
check("the cuts endpoint takes the filter",
      /AudioCut\.long_upload_id\.in_\(_ids\)/.test(flow));
check("...the clips endpoint too",
      /VideoClip\.long_upload_id\.in_\(_ids\)/.test(flow));
check("...and the header counts",
      /LongVideoUpload\.id\.in_\(_ids\)/.test(flow));
check("the page sends it on every one of them",
      (page.match(/\.\.\._f/g) || []).length >= 4, true);
check("...and the live tick depends on it",
      /\}, \[adminPassword, day, zone\]\);/.test(page));
check("the zone options accumulate across loads",
      /zoneSeen/.test(page));

// ── DELETE A RECORDING ──────────────────────────────────────────
check("the server can delete a recording",
      /@router\.delete\("\/raw\/\{upload_id\}"\)/.test(flow));
check("...taking its drafts through the delete that knows their keys",
      /delete_clip\(c\.id, db=db\)/.test(flow));
check("...and its cuts and their files",
      /_clear_previous\(db, row\)/.test(flow));
check("...refusing when a clip has gone out",
      /been broadcast/.test(flow));
check("the button arms before it deletes", /setArmed\(r\.id\)/.test(page));
check("...and says what is about to go",
      /This removes \{nCuts\[r\.id\] \|\| 0\} cut/.test(page));

// ── PRODUCE A CLIP FROM A CUT NOTHING WAS MADE FROM ─────────────
// Either the producer failed on a real swing or the sound was never
// one, and settling it used to mean re-running the whole flow.
check("an unproduced cut offers it", /Produce Clip/.test(page));
check("...only when nothing was produced", /\{!k\.produced && \(/.test(page));
check("...opening the add wizard on that recording",
      /openAdder\(\s*\n?\s*k\.long_upload_id/.test(page));
check("...seeded with the frame the strike was heard on",
      /impact_frame: k\.impact_frame/.test(page));
// ── AND BOUNDED TO THE CUT, NOT THE RECORDING ───────────────────
// It opened on the whole video — "1–9239" in the frame box — and
// asked the operator to find a strike the microphone had located.
check("...and the window the cut covers",
      /start_frame: k\.start_frame/.test(page)
        && /end_frame: k\.end_frame/.test(page));
check("the server gives the window in recording frames",
      /"start_frame"\] = max\(0, int\(round\(float\(k\.start_sec\)/
        .test(flow));
check("...and none at all without a frame rate",
      /"start_frame": None, "end_frame": None\}/.test(flow));
check("the stepper is bounded by the swing it was given",
      /frameLo=\{swing\.start_frame/.test(prod)
        && /frameHi=\{swing\.end_frame/.test(prod));
// A SEEDED CUT OPENS ON ITS STRIKE, and failing that on its own
// window's edge — frame 1 is thousands of frames outside it.
check("...and opens on the strike, not the edge",
      /startFrame=\{teeViewFrame \?\? impactF\s*\n?\s*\?\? \(isNew \? \(swing\.start_frame \?\? 1\)/
        .test(prod));
check("the modal takes a seed", /row, swingPos, addNew, seed,/.test(prod));
check("...and uses it only for a new swing",
      /const swing = isNew \? \(seed \|\| \{\}\) :/.test(prod));

console.log("\n7. a missing angle says why");
// A DISABLED BUTTON IS NOT AN ANSWER. Four reasons, four different
// next moves, one identical-looking button.
check("the server works out why", /def _cut_why_no_file/.test(flow));
check("...and the row carries it", /"tee_why"/.test(flow)
  && /"green_why"/.test(flow));
check("the card prints it", /no tee angle: \{k\.tee_why\}/.test(page));
// AND NO LINK IS BUILT FROM A NAME WHOSE FILE IS GONE: a 404 looks
// like footage, where an absence with a reason does not.
check("a url is only built where the file is there",
      /\(CLIPS_DIR \/ fn\)\.exists\(\)/.test(flow));
check("...so the dead 'tee only' label is gone",
      /tee only/.test(page), false);

console.log("\n8. a recording with no video is not listed");
check("the source index is built in bulk, not per row",
      /def _source_index/.test(flow)
        && /storage\.list_names\(\)/.test(flow));
// NOT ONE `ensure_local` PER ROW -- that would try to DOWNLOAD every
// clip on the course to find out whether it is there.
check("...without rehydrating anything",
      /_have_source\(_fn, _local, _bucket\)/.test(flow));
check("the rows are dropped before the limit, not after",
      /_have\.append\(_uid\)[\s\S]{0,400}?\.offset\(/.test(flow));
check("the day counts only count footage that is there",
      /_have_source\(_fn, _local, _bucket\)[\s\S]{0,200}?_days\[_k\]/
        .test(flow));
check("...and so does the header count",
      /n_raw = sum\(1 for _, _fn in _raw_q\.all\(\)/.test(flow));
check("the page says how many it left out",
      /hidden — no/.test(page));
check("...and a band with cuts but no row says why",
      /its source video is gone/.test(page));

console.log("\n9. the cuts, without the produce after them");
check("the server can stop after the cuts",
      /def flow_start_cuts/.test(flow) && /cuts_only=True/.test(flow));
// THE SAME RUN, not a second cutter: two would be two answers to
// "which strikes cleared this hole's gates", and they could disagree
// while both looked authoritative.
check("...through the same run, with a stop in it",
      /if cuts_only:/.test(flow));
check("...and the same status key, so one recording is one run",
      /_debugx_start\("flow", upload_id, _flow_runner_cuts\)/.test(flow));
check("the api can ask for it", /flowStartCuts:/.test(apijs));
check("the button is under Start production flow",
      /Audio cuts only/.test(page));
// BY `anyWork`, NOT `busy`: starting a flow on top of a hand-made
// produce is wrong too — the flow clears the cuts while the produce
// writes clips that name them.
check("...and is disabled while any work is going",
      /disabled=\{anyWork \|\| acting === `cutsonly-\$\{r\.id\}`\}/
        .test(page));
// NOT `cut-${id}`: that key belongs to Produce Clip on a CUT card,
// and an upload and a cut can share a number — which would grey out
// an unrelated button in another column.
check("...keyed apart from a cut's own Produce Clip",
      /cutsonly-\$\{uploadId\}/.test(page) || /"cutsonly"/.test(page));
check("the produce phase says it did not run, rather than showing 0",
      /not run — audio cuts only/.test(flow));

console.log("\n10. producing from a cut, with the operator told what is going on");
// ── THE CUT NEVER GOES AWAY ─────────────────────────────────────
// It is the thing being worked ON. Watching it vanish mid-produce
// reads as the produce having consumed it, and when the column
// settles there is no way to tell what happened.
check("the cut being produced from is greyed, not removed",
      /opacity: making \? 0\.55 : 1/.test(page));
check("...and says so on the tile", /producing swing \{k\.idx \+ 1\}…/
  .test(page));
// ── AND THE SWING IS NAMED, WITH WHETHER IT REPLACES ONE ────────
check("the drafts column names the swing being made",
      /Swing \{making\.swing\} is/.test(page));
check("...and tells producing from re-producing",
      /making\.had \? " re-producing" : " producing"/.test(page));
check("...which it knows from whether a draft of it exists",
      /had: \(drafts \|\| \[\]\)\.some\(/.test(page));
check("the stale tile is pulled while it is replaced",
      /c\.cut_idx \+ 1 === making\.swing/.test(page));
// ── A STALE CLOSURE WOULD STRAND IT ─────────────────────────────
// `poll` is memoised without this in its deps, so the copy that runs
// captured `null` — the note would never name the swing and the cut
// would stay greyed out for good.
check("the poll reads it from a ref, not from the closure",
      /producingRef\.current/.test(page));
// ── AND EVERY WAY OUT CLEARS IT ─────────────────────────────────
// Cancel, a refused save, or a wizard that would not open: nothing
// was produced, so nothing is producing.
check("cancelling clears it", /setProducingCut\(null\)/.test(page));
check("...on at least three paths",
      (page.match(/setProducingCut\(null\)/g) || []).length >= 3, true);
// ── ONE DRAFT PER SWING ─────────────────────────────────────────
check("the server supersedes older drafts of a swing",
      /def _one_clip_per_cut/.test(flow));
check("...through the delete that knows the four keys",
      /delete_clip\(c\.id, keep_swing=True, db=db\)/.test(flow));
check("...but never a broadcast one",
      /if c\.broadcast_at is not None:/.test(flow));
check("...and reports what it removed",
      /"superseded": _superseded/.test(flow));
check("the page says what the run did, by swing",
      /re-produced" : "produced"/.test(page));
check("...including the drafts it removed",
      /older draft\$\{_sup === 1 \? "" : "s"\} of the same/.test(page));

console.log("\n11. producing a swing produces THAT swing");
// ── THE BUG ─────────────────────────────────────────────────────
// Produce Clip on swing 1's cut opened the wizard as "a new swing",
// so the save appended one past the highest index: swing 1's cut
// produced a SWING 5, and the old draft stayed beside the new one.
// A caller that knows which swing this is now says so.
{
  const rule = (isNew, swing, swings, swingPos) => isNew
    ? (swing.idx != null ? swing.idx
      : (swings.length ? Math.max(...swings.map((x) => x.idx ?? 0)) + 1 : 0))
    : (swing.idx ?? swingPos);
  // The rule as the modal actually writes it, so this cannot drift.
  check("the modal honours a slot the caller names",
        /const newIdx = isNew\s*\n\s*\? \(swing\.idx != null \? swing\.idx/
          .test(prod));
  const four = [{ idx: 0 }, { idx: 1 }, { idx: 2 }, { idx: 3 }];
  check("Produce Clip on swing 1 produces slot 0",
        rule(true, { idx: 0 }, four, null), 0);
  check("...and on swing 3, slot 2", rule(true, { idx: 2 }, four, null), 2);
  check("+ add a swing still appends", rule(true, {}, four, null), 4);
  check("...and is 0 on a recording with none",
        rule(true, {}, [], null), 0);
  check("editing an existing swing patches it",
        rule(false, { idx: 1 }, four, 1), 1);
  // ONE PAST THE HIGHEST INDEX, NOT THE ARRAY LENGTH, so a deleted
  // swing cannot make two share a number.
  check("...and a deleted swing does not make two share a number",
        rule(true, {}, [{ idx: 0 }, { idx: 1 }, { idx: 3 }], null), 4);

  check("the flow tells it which swing", /idx: k\.idx,/.test(page));
  // AND WHERE THAT SWING ALREADY HAS A RECORD, the map opens ON it
  // and the save patches it: re-producing a swing replaces it.
  check("...and opens on the existing record where there is one",
        /_arr\.findIndex\(\(sw\) => \(sw\?\.idx \?\? -1\) === seed\.idx\)/
          .test(page));
  check("...patching rather than appending",
        /setEditing\(\{ row, swingPos: _pos, addNew: false/.test(page));
}

console.log("\n12. the status belongs to the column doing the work");
// Producing one swing by hand is stage 3 work. Folded into the
// recording's own `running` it greyed out stage 1 and said RUNNING
// on the raw card, which reads as the page going backwards.
check("the flow's own flag is separate", /out\["flow_running"\]/.test(flow));
check("...from a hand-made produce", /out\["produce_running"\] = _busy/
  .test(flow));
check("...and `running` is the flow's alone",
      /out\["running"\] = bool\(out\.get\("running"\)\)$/m.test(flow));
// THE POLLER STILL WAITS FOR BOTH, or it stops while a produce is
// writing and never claims the clip it made.
check("but the poller waits for either", /out\["busy"\]/.test(flow)
  && /st\?\.busy \?\? st\?\.running/.test(page));
check("the raw card's label follows the flow only",
      /\{busy \? "running…" : "Start production flow"\}/.test(page));
// STARTING A FLOW ON TOP OF EITHER IS STILL WRONG: the flow clears
// the cuts while the produce writes clips naming them.
check("...while its buttons lock for either",
      /const anyWork = busy \|\| !!run\?\.produce_running/.test(page)
        && /disabled=\{anyWork\}/.test(page));
check("and a produce this page did not start is still announced",
      /a draft is being produced…/.test(page));

console.log();
if (failed.length) {
  console.log(`${failed.length} FAILED:\n  ${failed.join("\n  ")}`);
  process.exit(1);
}
console.log("all ok");
