// Does the editor's lead-in preview agree with the server?
//
// The preview exists so an operator learns a refusal while plotting
// rather than from a produced clip, which is only worth anything if it
// gives the SAME answer. Two implementations of one parabola is a
// standing invitation to drift, and drift here lies in the reassuring
// direction: the panel says "lead-in will run" and the produce quietly
// refuses.
//
// So the functions are pulled out of the page itself rather than
// copied -- a copy would pass this test forever while the page moved.
//
//   node tools/green_leadin_js_test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../frontend/src/pages/AdminProduction.jsx", import.meta.url), "utf8");

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
    "const LEADIN_MAX_RMS_PX = 12.0;\nconst LEADIN_MIN_POINTS = 4;\n"
    + grab("quadFit") + "\n" + grab("leadInVerdict")
    + "\nexport { quadFit, leadInVerdict };"));

const FPS = 60.0, F0 = 2406, FL = 2421;
function flight(bumps = []) {
  const pts = [];
  for (let i = 0; i < 16; i++) {
    const u = i / 15.0;
    pts.push({ frame: F0 + i,
               x: 1541 + (1578 - 1541) * u,
               y: 396 + (830 - 396) * (0.80 * u + 0.20 * u * u) });
  }
  for (const [i, dx, dy] of bumps) { pts[i].x += dx; pts[i].y += dy; }
  return pts;
}
// Same seeded noise the python test uses, reproduced exactly.
function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// want: [ok, dropped?]  -- these are the PYTHON results, hard-coded.
const CASES = [
  ["clean flight",                       flight(),                                   true],
  ["one 65px outlier",                   flight([[7, 46.0, -46.0]]),                 true, 2413],
  ["two outliers",                       flight([[5, 46, -46], [11, -46, 46]]),      false],
  ["3 points only",                      flight().slice(0, 3),                       false],
];
{
  const r = mulberry32(99), noisy = flight();
  for (const p of noisy) { p.x += (r() * 80 - 40); p.y += (r() * 80 - 40); }
  CASES.push(["40px noise everywhere", noisy, false]);
}

let bad = 0;
for (const [label, pts, wantOk, wantDropped] of CASES) {
  const v = mod.leadInVerdict(pts, FL, FPS);
  const ok = !!(v && v.ok);
  let pass = ok === wantOk;
  if (pass && wantDropped != null) pass = v.dropped === wantDropped;
  if (!pass) bad++;
  console.log(
    `${label.padEnd(26)} ok=${String(ok).padEnd(5)} rms=${
      v && v.rms != null ? v.rms.toFixed(1).padStart(5) : "    -"}px dropped=${
      String(v && v.dropped)}  ${pass ? "ok" : "FAIL (want ok=" + wantOk + ")"}`);
}
console.log(bad ? `\n${bad} MISMATCH(ES) with the server` : "\nALL PASS");
process.exit(bad ? 1 : 0);
