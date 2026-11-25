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

console.log();
if (failed.length) {
  console.log(`${failed.length} FAILED:\n  ${failed.join("\n  ")}`);
  process.exit(1);
}
console.log("all ok");
