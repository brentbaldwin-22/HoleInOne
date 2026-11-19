// FILTERING THE SURPLUS OUT OF THE PRODUCTION LIST, BY LENGTH.
//
// A dwell clip with nobody in it is short, and its length is the only
// thing about it a machine can read. The trigger fires, the tee box is
// empty from the first frame, and the recorder stops as soon as the
// no-person timeout runs out -- so a run of 18-second clips is a run
// of surplus, and they were being picked off one at a time by eye.
//
// THE REASON THIS HAS A TEST AT ALL: what the filter returns is what
// "Select all loaded" selects, and what the Delete button then
// deletes. A predicate that is merely approximate here destroys
// footage. The case that matters is a row whose duration never
// probed -- `null` is not "short", and must not be swept up by
// "under 20s".
//
//   node tools/clip_length_filter_test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../frontend/src/pages/AdminProduction.jsx", import.meta.url),
  "utf8");

// SKIP THE SIGNATURE BEFORE COUNTING BRACES. This function takes a
// destructured options object, so a naive brace counter starting at
// the name closes the "body" on `{ on, op, sec }` and hands back half
// a line. Walk the parameter list by PARENTHESES first, then count
// braces from the body's opening one.
function grab(name) {
  const i = src.indexOf(`export function ${name}(`);
  if (i < 0) throw new Error(`${name} not found in AdminProduction.jsx`);
  let p = 0, body = -1;
  for (let j = src.indexOf("(", i); j < src.length; j++) {
    if (src[j] === "(") p++;
    else if (src[j] === ")") { p--; if (p === 0) { body = src.indexOf("{", j); break; } }
  }
  if (body < 0) throw new Error(`${name}: no body found`);
  let d = 0;
  for (let j = body; j < src.length; j++) {
    if (src[j] === "{") d++;
    else if (src[j] === "}") { d--; if (d === 0) return src.slice(i, j + 1); }
  }
  throw new Error(`${name} did not close`);
}

const mod = await import(
  "data:text/javascript," + encodeURIComponent(grab("filterByTeeLength")));

const failed = [];
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(56)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)} want ${JSON.stringify(want)}`}`);
  if (!ok) failed.push(label);
}
const ids = (rows) => rows.map((r) => r.id);

// A realistic page: three surplus dwell clips at 18s, one real swing,
// and one row whose duration would not probe.
const ROWS = [
  { id: 1, tee_duration_sec: 18 },
  { id: 2, tee_duration_sec: 18 },
  { id: 3, tee_duration_sec: 47.5 },
  { id: 4, tee_duration_sec: 18 },
  { id: 5, tee_duration_sec: null },
];

console.log("off, it changes nothing:");
check("the list comes back whole",
      ids(mod.filterByTeeLength(ROWS, { on: false, op: "under", sec: 20 })),
      [1, 2, 3, 4, 5]);

console.log();
console.log("under 20s finds the surplus:");
check("the three short ones",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: 20 })),
      [1, 2, 4]);
check("  and the real swing is left alone",
      mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: 20 })
        .some((r) => r.id === 3), false);

console.log();
console.log("AN UNMEASURED LENGTH IS NOT A SHORT ONE:");
// This list feeds a bulk delete. A row that could not be probed is
// unknown, not short, and sweeping it into "under 20s" would delete a
// clip nobody has looked at.
check("null is not under 20",
      mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: 20 })
        .some((r) => r.id === 5), false);
check("  nor over 20 — unknown is excluded both ways",
      mod.filterByTeeLength(ROWS, { on: true, op: "over", sec: 20 })
        .some((r) => r.id === 5), false);
check("  and a non-numeric duration is treated the same",
      ids(mod.filterByTeeLength(
        [{ id: 9, tee_duration_sec: "n/a" }],
        { on: true, op: "under", sec: 20 })), []);

console.log();
console.log("over, for the other direction:");
check("only the long one",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "over", sec: 20 })),
      [3]);

console.log();
console.log("the boundary is exclusive, and says so:");
check("exactly 20 is not under 20",
      ids(mod.filterByTeeLength([{ id: 7, tee_duration_sec: 20 }],
        { on: true, op: "under", sec: 20 })), []);
check("  nor over 20",
      ids(mod.filterByTeeLength([{ id: 7, tee_duration_sec: 20 }],
        { on: true, op: "over", sec: 20 })), []);

console.log();
console.log("and it refuses to narrow on nonsense:");
// A half-typed number in the box must not blank the page and invite a
// select-all over a list that means nothing.
check("an empty seconds box returns everything",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: "" })),
      [1, 2, 3, 4, 5]);
// THE DANGEROUS HALF OF THAT. "under 0" merely blanks the page, which
// is confusing; "over 0" matches every measured row -- and that list
// is what Select-all-loaded hands to the Delete button. A blank box
// mid-type must not arm a select-all over the whole page.
check("  an empty box in OVER mode does not select everything",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "over", sec: "" })),
      [1, 2, 3, 4, 5]);
check("so does a non-number",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: "abc" })),
      [1, 2, 3, 4, 5]);
check("a null list stays null",
      mod.filterByTeeLength(null, { on: true, op: "under", sec: 20 }), null);
check("zero seconds still filters",
      ids(mod.filterByTeeLength(ROWS, { on: true, op: "under", sec: 0 })), []);

console.log();
if (failed.length) {
  console.log(`FAILED: ${failed.join(", ")}`);
  process.exit(1);
}
console.log("ALL PASS — and an unmeasured clip is never swept into a delete");
