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
      ["title", "secs", "count", "units", "detail"].every(
        (f) => new RegExp(`p\\.${f}`).test(page)));
// THE AUDIO PHASE'S DETAIL IS THE ANSWER TO "which gates cut this",
// and a field the server fills that the page drops is a question with
// no answer on the screen that raised it.
check("the run records the gates it actually used",
      /dbg\["gates_used"\]/.test(admin));
check("...whether audio1 is armed", /dbg\["audio1_armed"\]/.test(admin));
check("...and which audio1 gates are set", /dbg\["audio1_on"\]/.test(admin));
check("the flow turns them into words", /_gates_sentence\(dbg\)/.test(flow));
check("...and says plainly when they are only the defaults",
      /not a tee-box preset/.test(flow));
check("...and when they are set but not armed",
      /set but NOT armed/.test(flow));

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

console.log("\n3c. every status line says which column it belongs to");
{
  // ONE STRING, FOUR COLUMNS. The background runner carries a single
  // `stage`, so the runner tags each line with its phase and the page
  // splits on it. If the two ever disagree about the separator or the
  // names, every line lands on the recording's card and the columns
  // narrate nothing -- which looks exactly like a run that is not
  // reporting progress.
  const tagged = [...flow.matchAll(/_phased\(progress, "([a-z]+)"\)/g)]
    .map((m) => m[1]);
  check("the run tags its phases", tagged.sort(), ["audio", "cut", "produce"]);
  check("the produce's own lines are tagged too",
        /_produce_run\(row, src_path, db, progress=_say\)/.test(flow));
  check("the tag is a prefix on the stage string",
        /progress\("%s\|%s" % \(phase, stage\)/.test(flow));
  // And the page splits on the same character.
  check("the page splits on it", /indexOf\("\|"\)/.test(page));
  const want = [...page.matchAll(/phase="([a-z]+)"/g)].map((m) => m[1]);
  check("and listens for exactly those phases",
        [...new Set(want)].sort(), ["audio", "cut", "produce"]);
  check("an untagged line keeps its whole text",
        /return \{ phase: null, text: stage \|\| "" \}/.test(page));
}

console.log("\n3d. the columns refresh while the run is still going");
{
  check("there is a light tick, not a full reload",
        /const loadLive = useCallback/.test(page));
  check("it refetches the cuts", /api\.flowCuts\(adminPassword[\s\S]{0,120}?\)/
    .test(page.slice(page.indexOf("const loadLive"),
                     page.indexOf("const loadLive") + 700)));
  check("...and runs on every poll while running",
        /if \(st\?\.running\) \{\s*\n\s*await loadLive\(uploadId\);/
          .test(page));
  check("...and is dropped when the run ends",
        /setLiveDrafts\(null\);/.test(page));
  // The drafts are asked for unfiltered BY UPLOAD during a run,
  // because the flow stamp is not applied until the run settles.
  check("live drafts are asked for by upload", /uploadId, flowOnly: false/
    .test(page));
  // AND ONLY SHOWN ONCE THE PRODUCE HAS STARTED. The unfiltered fetch
  // is safe only because a produce clears the upload's old clips --
  // which happens when the PRODUCE starts, not when the run does.
  // Shown during the cutting phase it would put another pipeline's
  // clips under a heading that says this run made them.
  check("...and only used once the produce has started",
        /phaseOf\(liveRun\?\.stage\)\.phase === "produce"/.test(page));
  check("...which is what gates the draft source",
        /producing && liveDrafts \? liveDrafts : drafts/.test(page));
  check("and the endpoint accepts that filter",
        /upload_id: int = 0/.test(flow));
  check("...and applies it",
        /VideoClip\.long_upload_id == upload_id/.test(flow));
}

console.log("\n3e. the measurement table says what the gates did to each row");
{
  const panel = readFileSync(
    join(ROOT, "frontend/src/components/Audio1Panel.jsx"), "utf8");
  const page2 = readFileSync(
    join(ROOT, "frontend/src/pages/AdminLongUpload.jsx"), "utf8");
  // "8 cut" over eight rows, beside four gates that are set and not
  // armed, says nothing about which rows arming would take. The gate
  // pass judged every one of them; the verdict is joined back on.
  check("the report reaches the panel",
        /gateReport=\{\s*testCutResults\[u\.id\]\.debug\.audio1_gates\}/
          .test(page2));
  check("the panel takes it", /gateReport \}\) \{|gateReport \}/.test(panel));
  check("it is keyed back onto the row by time",
        /by\[r\.peak_time_sec\.toFixed\(2\)\] = r/.test(panel));
  check("an unarmed run says what arming would leave",
        /would leave/.test(panel));
  check("...and an armed one says what it did",
        /these gates decided this run/.test(panel));
  check("a dropped row names the gate that dropped it",
        /verdicts\[key\]/.test(panel) && /f\.of/.test(panel));
  check("and the wording distinguishes did from would",
        /armed \? "dropped" : "would drop"/.test(panel));
}

console.log("\n3f. the page is a table: one row per recording");
{
  // FOUR INDEPENDENT LISTS AND NO WAY TO JOIN THEM. Three cuts and a
  // draft from #1915 sat beside five cuts from #1916 with nothing
  // lining them up, so the lineage -- the whole point of the page --
  // had to be reconstructed card by card. A row band says it without
  // anybody reading anything.
  check("the rows are built from the recordings",
        /const bands = \(\(\) => \{/.test(page));
  check("...four cells wide", (page.match(/<Cell /g) || []).length, 4);
  check("...in one grid, so the cells line up",
        /gridTemplateColumns:\s*\n?\s*"minmax/.test(page));
  check("each cell is filtered to its own recording",
        /x\.long_upload_id === uid/.test(page));
  // A RECORDING WITH NO CUTS STILL GETS A ROW, and the cell says so
  // rather than collapsing and pulling the row out of alignment.
  check("an empty cell says what is missing",
        /empty="no cuts"/.test(page) && /empty="no drafts"/.test(page));
  // AND A CLIP WHOSE RECORDING IS OFF THIS PAGE still gets a row --
  // otherwise it would simply vanish rather than be findable.
  check("uploads known only from their cuts get a row too",
        /\[cuts, drafts, finals\]\.forEach/.test(page));
  check("...with the cell saying why the recording is not shown",
        /not on this page of recordings/.test(page));
}

console.log("\n3g. the raw card says what the recording became");
{
  check("the fan-out is counted once per column",
        /const nCuts = tally\(/.test(page)
        && /const nDrafts = tally\(/.test(page)
        && /const nFinals = tally\(/.test(page));
  check("and printed on the recording it came from",
        /\{nCuts\[r\.id\] \|\| 0\} cut/.test(page));
  check("clicking it narrows to that recording",
        /setFocus\(on \? 0 : r\.id\)/.test(page));
  check("a produced cut is marked as having spawned a draft",
        /→ draft/.test(page));
  check("...and one that produced nothing says so", /no clip/.test(page));
  check("the draft leads with the swing that made it",
        /swing \$\{c\.cut_idx \+ 1\}/.test(page));
}

console.log("\n3g2. the timings are one switch, off by default");
{
  // A dozen rows of seconds under every recording is detail you want
  // twice and read past the rest of the time.
  check("Phases takes a show flag", /function Phases\(\{ phases, total, show \}\)/
    .test(page));
  check("...and renders nothing without it",
        /if \(!show \|\| !phases\?\.length\) return null/.test(page));
  check("the switch starts off",
        /useState\(false\);\n\s*\/\/ Off by default|const \[showTimings, setShowTimings\] = useState\(false\)/
          .test(page));
  // One control: the declaration plus exactly one place that flips
  // it. A per-card toggle would be several.
  check("one control, not one per card",
        (page.match(/setShowTimings\(\(v\) => !v\)/g) || []).length, 1);
  check("...and it is labelled both ways",
        /showTimings \? "hide timings" : "show timings"/.test(page));
}

console.log("\n3h. a re-run starts over rather than adding to the pile");
{
  check("the run clears what an earlier one left",
        /rep\["cleared"\] = _clear_previous\(db, row\)/.test(flow));
  check("rows and files both", /out\["files"\] \+= 1/.test(flow)
        && /out\["cuts"\] \+= 1/.test(flow));
  check("and nothing is left pointing at a deleted cut",
        /c\.audio_cut_id = None/.test(flow));
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
// However the template is split, the link must carry both params.
const to = /\/admin\/production\?edit=\$\{[\s\S]{0,60}?&clip=\$\{/.test(page);
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
