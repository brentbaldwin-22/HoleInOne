#!/usr/bin/env node
/*
 * The flow page reads the fields the server actually writes.
 *
 * THE BUG THIS EXISTS FOR, twice over in one week. The page read
 * `run.result` and the background runner stores its report under
 * `report`. Before that, the same page read localStorage["adminPassword"]
 * and the console writes "golfreelz.adminPassword". Both are the same
 * shape of mistake: a field nobody writes reads as absent, absence
 * renders as "nothing to show", and the screen looks like a run that
 * did nothing rather than like a page looking in the wrong place.
 * There is no error anywhere to find.
 *
 * Checked by reading both sides, because that is the only place the
 * two names appear together.
 *
 *   node tools/flow_run_test.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(62)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

const page = readFileSync(
  join(ROOT, "frontend/src/pages/AdminProductionFlow.jsx"), "utf8");
const admin = readFileSync(
  join(ROOT, "backend/app/routers/admin.py"), "utf8");
const flow = readFileSync(
  join(ROOT, "backend/app/routers/production_flow.py"), "utf8");

console.log("\n1. the run's status fields are the runner's own");
// The shape the background runner initialises and maintains.
const init = /_debugx_state\[\(kind, upload_id\)\] = \{([\s\S]{0,260}?)\}/
  .exec(admin);
check("the runner's state shape was found", !!init);
const fields = [...(init?.[1] || "").matchAll(/"([a-z_]+)":/g)].map((m) => m[1]);
for (const f of ["running", "stage", "done", "total", "report", "error"]) {
  check(`  it carries ${f}`, fields.includes(f));
}
// What the page reads off a run.
const read = new Set(
  [...page.matchAll(/\brun\??\.([a-zA-Z_]+)/g)].map((m) => m[1]));
check("the page reads only fields the runner writes",
      [...read].filter((f) => !fields.includes(f)), []);
check("...and it does read the report", read.has("report"));
check("...and surfaces an error rather than swallowing it",
      read.has("error"));

console.log("\n2. the report's own fields are the ones the page prints");
const repKeys = new Set(
  [...page.matchAll(/run\.report\.([a-zA-Z_]+)/g)].map((m) => m[1]));
check("the page prints something off the report", repKeys.size > 0);
for (const k of repKeys) {
  // Every one must be assigned somewhere in the runner's rep.
  const written = new RegExp(`rep\\[["']${k}["']\\]|["']${k}["']:`)
    .test(flow);
  check(`  rep carries ${k}`, written);
}

console.log("\n3. the phase rows carry what the table renders");
for (const f of ["name", "title", "secs", "count", "units"]) {
  check(`  ${f}`, new RegExp(`"${f}":`).test(flow));
}
check("the page renders each of them",
      ["title", "secs", "count", "units"].every(
        (f) => new RegExp(`p\\.${f}`).test(page)));

console.log("\n3b. the produce's stages are read by the names it writes");
{
  // THE THIRD FIELD-NAME MISS ON THIS PAGE IN A WEEK. The produce
  // writes `seconds` and `counts`; this file's own phases use `secs`
  // and `units`. Reading the local names off a produce stage dropped
  // every one of them through an `is None` guard, so a four-minute
  // produce reported as a single unbroken row.
  // EVERY append, not the first. They do not all carry the same keys
  // -- one names its step `name` and the rest `title` -- and slicing
  // to the first reported `name` as a key the produce never writes.
  const written = [];
  for (const m of admin.matchAll(/rep\["stages"\]\.append\(\{/g)) {
    const blk = admin.slice(m.index, m.index + 600);
    const end = blk.indexOf("})");
    for (const k of (end > 0 ? blk.slice(0, end) : blk)
      .matchAll(/"([a-z_]+)":/g)) {
      if (!written.includes(k[1])) written.push(k[1]);
    }
  }
  check("the produce's stage shapes were found", written.length > 4);
  for (const f of ["n", "detail", "count", "counts", "seconds"]) {
    check(`  it writes ${f}`, written.includes(f));
  }
  // What this file reads off one.
  const read = [...flow.matchAll(/\bst\.get\("([a-z_]+)"\)/g)]
    .map((m) => m[1]);
  check("the flow reads something off a stage", read.length > 0);
  check("and reads only names the produce writes",
        read.filter((f) => !written.includes(f)), []);
  check("...including the clock", read.includes("seconds"));
  check("...and the unit", read.includes("counts"));
  check("it does NOT read this file's own spelling",
        /st\.get\("secs"\)|st\.get\("units"\)/.test(flow), false);
}

console.log("\n4. the cuts column prints fields the cut row serves");
const cutKeys = new Set(
  [...page.matchAll(/\bk\.([a-z_]+)/g)].map((m) => m[1]));
for (const k of cutKeys) {
  check(`  ${k}`, new RegExp(`"${k}":`).test(flow));
}

console.log("\n5. the raw column prints fields the raw row serves");
const rawKeys = new Set(
  [...page.matchAll(/\br\.([a-z_]+)/g)].map((m) => m[1]));
for (const k of rawKeys) {
  check(`  ${k}`, new RegExp(`"${k}":`).test(flow));
}

console.log("\n6. and the deep link matches what Production listens for");
const to = /\/admin\/production\?edit=\$\{[^}]+\}`\s*\+\s*`&clip=/.test(page);
check("the flow links with edit= and clip=", to);
const prod = readFileSync(
  join(ROOT, "frontend/src/pages/AdminProduction.jsx"), "utf8");
check("Production reads edit=", /q\.get\("edit"\)/.test(prod));
check("...and clip=", /q\.get\("clip"\)/.test(prod));
check("...and opens the same plot map an Edit button does",
      /setPlotModal\(\{ row, swingPos/.test(prod));
check("...and clears the query so a refresh does not reopen it",
      /replaceState/.test(prod));

console.log(`\n${failed.length
  ? `${failed.length} FAILED:\n  ${failed.join("\n  ")}` : "all ok"}`);
process.exit(failed.length ? 1 : 0);
