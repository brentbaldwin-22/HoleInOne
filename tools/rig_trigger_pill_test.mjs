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
    `${grab("zoneWords")}\n${grab("rigTrigger")}\n`
    + `export { rigTrigger, zoneWords };`));

const zoneWordsOf = (d) => mod.zoneWords(d);
const failed = [];
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(56)} ${String(got).slice(0, 10).padEnd(12)} ${ok ? "ok" : `FAIL (want ${want})`}`);
  if (!ok) failed.push(label);
}

console.log("A ZONE INDEX IS NOT THE NUMBER ON THE OPERATOR'S SCREEN:");
// THE OFF-BY-ONE THAT SENDS SOMEBODY TO THE WRONG BOX. The rig counts
// zones from 0 and the zone editor labels them from 1, and the agent
// writes both on every startup:
//
//   zone 2 ("Zone 3" on screen, middle left): dwell fires
//
// An event that reads "middle left (zone 2)" is naming the index, so
// an operator goes and looks at Zone 2 -- a different piece of grass.
// Only the on-screen number belongs on this card.
check("index 2 is Zone 3 on screen",
      zoneWordsOf({ zone: 2, zone_label: "middle left" }),
      "middle left (Zone 3)");
check("index 0 is Zone 1, not absent",
      zoneWordsOf({ zone: 0, zone_label: "Back" }), "Back (Zone 1)");
// ZONE 0 IS A ZONE. `if (detail.zone)` drops it, which is the same
// class of bug the audio verdict case below guards against.
check("  so zone 0 is never treated as no zone",
      zoneWordsOf({ zone: 0, zone_label: "Back" }) === null, false);
check("an unlabelled zone still gets its number",
      zoneWordsOf({ zone: 1 }), "Zone 2");
check("a label with no index stands alone",
      zoneWordsOf({ zone_label: "middle left" }), "middle left");
check("nothing in, nothing out", zoneWordsOf({}), null);

console.log();
console.log("the dwell pill names the zone that fired it:");
// A dwell clip with no golfer in it is nearly always a zone catching
// traffic rather than golf. The operator cannot redraw the offending
// box without being told which one it was, and the pill used to say
// "Motion dwell" and stop.
const dwell = mod.rigTrigger({
  kind: "dwell", zone: 2, zone_label: "middle left",
  stood_sec: 7.2, dwell_sec: 7, restarts_total: 67 });
check("  it names the box", dwell.label.includes("middle left (Zone 3)"),
      true);
check("  and how long they stood", dwell.label.includes("stood 7.2s"),
      true);
check("  and still says why it was not judged",
      dwell.label.includes("not judged (no impact to judge)"), true);
check("  the threshold rides in the tooltip",
      dwell.title.includes("Dwell threshold 7s"), true);
check("  with the crossing count",
      dwell.title.includes("67 zone crossings"), true);
// A PI THAT SENT NO ZONE MUST NOT RENDER "undefined".
const bare = mod.rigTrigger({ kind: "dwell" });
check("a dwell with no zone still reads cleanly", bare.label,
      "Motion dwell · not judged (no impact to judge)");
check("  and invents no tooltip numbers",
      bare.title.includes("threshold"), false);

console.log();
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

// THE INSTANT THE RIG JUDGED, which is the number that makes a
// disagreement arguable. Test Cut re-derives the impact from the cut
// clip and can land half a second away; two composites half a second
// apart, each half a second wide, barely overlap.
check("it carries the instant it judged", mod.rigTrigger(
  { kind: "audio", ratio: 4.1, verdict: true, verdict_at_sec: 5.0 }).label,
      "Audio ×4.1 · judge: swing @ 5s");
check("...and omits it when the rig did not send one", mod.rigTrigger(
  { kind: "audio", verdict: true }).label.includes("@"), false);
// ZERO SECONDS IS AN INSTANT. `if (detail.verdict_at_sec)` would drop
// it, which is the zone-0 mistake again.
check("an impact at 0s is still an instant", mod.rigTrigger(
  { kind: "audio", verdict: true, verdict_at_sec: 0 }).label.includes("@ 0s"),
      true);

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
