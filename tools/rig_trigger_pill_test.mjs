// WHAT THE PRODUCTION CARD SAYS ABOUT WHY A CLIP EXISTS.
//
// Test Cut re-runs the swing judge on an upload and prints its
// verdict. The rig asked the SAME judge before it spent the link, and
// that answer lived on another page -- so "the judge says not a swing,
// why did this upload?" took two tabs and a matching-up of event
// numbers by hand.
//
// The case this exists for is the other one: A DWELL CLIP NEVER TOOK
// THE TEST. The judge runs in the audio path only, because a dwell
// recording has no impact instant to build a composite around. An
// unjudged dwell clip that says nothing looks exactly like the judge
// failing, and they call for opposite actions.
//
//   node tools/rig_trigger_pill_test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../frontend/src/pages/AdminProduction.jsx", import.meta.url),
  "utf8");

function grab(name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found in AdminProduction.jsx`);
  let d = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { d++; started = true; }
    else if (src[j] === "}") { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  throw new Error(`${name} did not close`);
}

const mod = await import(
  "data:text/javascript," + encodeURIComponent(
    `${grab("rigTrigger")}\nexport { rigTrigger };`));

const failed = [];
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(56)} ${String(got).slice(0, 10).padEnd(12)} ${ok ? "ok" : `FAIL (want ${want})`}`);
  if (!ok) failed.push(label);
}

console.log("an audio cut carries the rig's own verdict:");
check("a yes", mod.rigTrigger({ kind: "audio", ratio: 5.4, verdict: true })
  .label, "Audio ×5.4 · judge: swing");
check("...and reads as good", mod.rigTrigger(
  { kind: "audio", verdict: true }).tone, "ok");
check("a no", mod.rigTrigger({ kind: "audio", verdict: false }).label,
      "Audio · judge: NOT a swing");
check("...and reads as bad", mod.rigTrigger(
  { kind: "audio", verdict: false }).tone, "bad");
// A FALSE VERDICT IS A VERDICT. `if (detail.verdict)` would file this
// under "not asked" -- the same class of bug as treating zone 0 as no
// zone, and it would hide the exact row being chased here.
check("a no is not an absent answer", mod.rigTrigger(
  { kind: "audio", verdict: false }).label.includes("not asked"), false);
check("asked, nothing came back", mod.rigTrigger(
  { kind: "audio", verdict_asked: true, verdict_why: "no answer" }).label,
      "Audio · judge: could not tell — no answer");
check("never asked", mod.rigTrigger({ kind: "audio" }).label,
      "Audio · judge: not asked");
check("the reason rides on the tooltip, not the pill", mod.rigTrigger(
  { kind: "audio", verdict: false, verdict_reason: "no plume" }).title,
      "no plume");

console.log("");
console.log("A DWELL CLIP SAYS IT WAS NEVER JUDGED:");
check("it says so", mod.rigTrigger({ kind: "dwell" }).label,
      "Motion dwell · not judged (no impact to judge)");
// NOT "bad". A dwell clip uploading unjudged is the design, and
// colouring it as a failure would send somebody to fix the judge.
check("...and is not reported as a failure",
      mod.rigTrigger({ kind: "dwell" }).tone, "warn");
check("...with the why on the tooltip",
      mod.rigTrigger({ kind: "dwell" }).title.includes("audio-triggered"),
      true);

console.log("");
console.log("and it never invents a pill it has no detail for:");
check("a Pi from before any of this", mod.rigTrigger(null), null);
check("not a dict", mod.rigTrigger("audio"), null);
check("a kind nobody has taught it", mod.rigTrigger({ kind: "wat" }), null);

console.log("");
if (failed.length) {
  console.log(`FAILED: ${failed.join(", ")}`);
  process.exit(1);
}
console.log("ALL PASS");
