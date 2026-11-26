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
check("...and folds it into one `running`",
      /out\["running"\] = bool\(out\.get\("running"\)\) or _busy/.test(flow));
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
check("...without swallowing the empty message",
      /function Cell\(\{ children, empty, live, center, footer \}\)/
        .test(page) && /\{\/\* OUTSIDE THE EMPTY TEST[\s\S]{0,400}?\{footer\}/
        .test(page));

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
      /api\.flowRaw\(adminPassword, \{ limit: 40, day, zone \}\)/
        .test(page));
check("the day is the course's own, not UTC",
      /_local_dt\(r\.base_captured_at\)/.test(flow));
// ZONE 0 IS A ZONE. A falsy test drops the box printed "Zone 1" and
// silently widens the filter to everything.
check("zone 0 is sent, not treated as 'all'",
      /zone != null && zone !== ""/.test(apijs));
check("the day list comes from every recording, not the page",
      /"days": \[\{"day": k, "n": v\}/.test(flow));
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
check("the modal takes a seed", /row, swingPos, addNew, seed,/.test(prod));
check("...and uses it only for a new swing",
      /const swing = isNew \? \(seed \|\| \{\}\) :/.test(prod));

console.log();
if (failed.length) {
  console.log(`${failed.length} FAILED:\n  ${failed.join("\n  ")}`);
  process.exit(1);
}
console.log("all ok");
