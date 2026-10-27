// How the events page renders a trigger's provenance.
//
// The two helpers under test turn machine fields into the words an
// operator reads: a Pi epoch into a wall clock, and a zone index into
// the name they drew on the screen. Both are places where "absent" and
// "zero" can be confused -- zone 0 is a real tee, and an event at the
// epoch is not "no time" -- and both get rendered beside a clip that
// somebody is deciding whether to trust.
//
// Pulled out of the page rather than copied, so the test cannot pass
// while the page moves.
//
//   node tools/trigger_line_js_test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../frontend/src/pages/AdminCameras.jsx", import.meta.url), "utf8");

function grab(name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found in AdminCameras.jsx`);
  let d = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { d++; started = true; }
    else if (src[j] === "}") { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  throw new Error(`${name} did not close`);
}

const mod = await import(
  "data:text/javascript," + encodeURIComponent(
    `${grab("clockOf")}\n${grab("zoneOf")}\nexport { clockOf, zoneOf };`));

const failed = [];
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(54)} ${String(got).padEnd(14)} ${ok ? "ok" : `FAIL (want ${want})`}`);
  if (!ok) failed.push(label);
}

// A fixed instant, read back in whatever zone this machine runs in --
// asserting a literal clock would make the test fail in another
// timezone and tell us nothing about the code.
const EPOCH = 1761580937.34;
const d = new Date(EPOCH * 1000);
const p2 = (n) => String(n).padStart(2, "0");
const expect = `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}.3`;

console.log("a Pi's epoch becomes a wall clock, to a tenth:");
check("the tenth is kept", mod.clockOf(EPOCH), expect);
check("missing time is not midnight", mod.clockOf(null), null);
check("undefined is not midnight", mod.clockOf(undefined), null);
// THE EPOCH IS A REAL TIME. Returning null for it would be the same
// mistake as treating zone 0 as absent, and `if (!epochSec)` makes it.
check("the epoch itself is a time, not nothing",
      mod.clockOf(0) !== null, true);
check("nonsense is refused", mod.clockOf(NaN), null);
check("infinity is refused", mod.clockOf(Infinity), null);

console.log("");
console.log("a zone is named the way the operator named it:");
check("label and index together",
      mod.zoneOf({ zone: 1, zone_label: "Middle" }), "Middle (zone 1)");
// ZONE 0 IS THE BACK TEE on this course -- the one the microphone can
// actually hear -- so a falsy check here hides the most important row.
check("ZONE 0 IS A ZONE, not a missing one",
      mod.zoneOf({ zone: 0, zone_label: "Back" }), "Back (zone 0)");
check("index 0 alone still shows", mod.zoneOf({ zone: 0 }), "zone 0");
check("an unlabelled zone falls back to its index",
      mod.zoneOf({ zone: 2 }), "zone 2");
check("a blank label falls back too",
      mod.zoneOf({ zone: 2, zone_label: "" }), "zone 2");
check("a label with no index still reads",
      mod.zoneOf({ zone_label: "Back" }), "Back");
check("nothing to say", mod.zoneOf({}), null);
check("no detail at all", mod.zoneOf(null), null);

console.log("");
if (failed.length) {
  console.log(`FAILED: ${failed.join(", ")}`);
  process.exit(1);
}
console.log("ALL PASS");
