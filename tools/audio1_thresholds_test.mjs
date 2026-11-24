#!/usr/bin/env node
/*
 * The threshold sandbox's arithmetic.
 *
 * THIS IS THE PART THAT WOULD FLATTER A BAD RULE. Every one of these
 * cases is a way of making a threshold look better than it is:
 *
 *   A STRIKE THE DETECTOR ALREADY REFUSED IS STILL MISSED. Thresholds
 *   can only ever TAKE CLIPS AWAY; none of them can bring back a peak
 *   the gate dropped. Counting those as "not my rule's fault" would
 *   make recall read 100% on a clip whose strike never got cut.
 *
 *   A STRIKE THAT MADE NO PEAK IS STILL MISSED. It cannot appear in
 *   the peak list at all, so a denominator built from peaks alone
 *   would never count it and recall would be a measure of the sounds
 *   we already catch.
 *
 *   A FALSE POSITIVE THE DETECTOR ALREADY REFUSED IS NOT "ELIMINATED"
 *   by your rule. It was never cut; taking credit for it is how a
 *   threshold that does nothing scores well.
 *
 *   AN UNMEASURABLE VALUE IS NOT A FAILURE BY DEFAULT. A quiet strike
 *   from a far tee is exactly where HF SNR comes back blank, and
 *   rejecting what could not be measured selects against distance.
 *
 *   node tools/audio1_thresholds_test.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(60)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

// The real module, through esbuild's JSX transform — not a copy.
const SRC = readFileSync(
  join(ROOT, "frontend/src/components/Audio1Thresholds.jsx"), "utf8");
const { transform } = await import(
  join(ROOT, "frontend/node_modules/esbuild/lib/main.js"));
// Only the exported helpers are needed; the component itself pulls in
// React, which this has no business loading.
const cut = SRC.slice(0, SRC.indexOf("export default function"));
const out = await transform(cut.replace(/^import .*$/gm, ""), {
  loader: "jsx", jsx: "transform", jsxFactory: "h",
});
const { RULES, evaluate } = await import(
  "data:text/javascript," + encodeURIComponent(
    "const useMemo=()=>{},useState=()=>{};\n" + out.code));

const peak = (o) => ({ peak_time_sec: 1, passed_gate: true, ...o });

console.log("\n1. the six the request named are all there");
for (const want of ["min peak ratio", "min SNR", "max original attack",
                    "min HF SNR", "min isolation"]) {
  check(`  ${want}`, RULES.some((r) => r.label.startsWith(want)));
}
check("  and the energy concentration ratios",
      RULES.filter((r) => r.label.includes("E20/E200")
                       || r.label.includes("E50/E200")
                       || r.label.includes("E10/E100")).length, 5);

console.log("\n2. a rule that is off does nothing at all");
const off = Object.fromEntries(RULES.map((r) => [r.key, { on: false, value: "99" }]));
check("every rule off, a hopeless peak passes",
      evaluate(peak({ snr_db: 1, peak_ratio: 1 }), off, false).pass, true);

console.log("\n3. min and max are applied the right way round");
const on = (k, v) => ({ ...off, [k]: { on: true, value: v } });
check("min SNR 20 keeps 30", evaluate(peak({ snr_db: 30 }), on("snr", 20), false).pass, true);
check("min SNR 20 drops 10", evaluate(peak({ snr_db: 10 }), on("snr", 20), false).pass, false);
check("max attack 20 keeps 8",
      evaluate(peak({ detector_attack_ms: 8 }), on("atk0", 20), false).pass, true);
check("max attack 20 drops 40",
      evaluate(peak({ detector_attack_ms: 40 }), on("atk0", 20), false).pass, false);

console.log("\n4. a nested value is read, not ignored");
const p20 = peak({ concentration: { e20_over_e200: 0.42 } });
check("E20/E200 0.42 clears a 0.30 bar",
      evaluate(p20, on("c20", 0.3), false).pass, true);
check("...and fails a 0.50 bar", evaluate(p20, on("c20", 0.5), false).pass, false);
check("the HF ratio is a separate rule",
      evaluate(peak({ concentration_hf: { e20_over_e200: 0.1 } }),
               on("c20hf", 0.3), false).pass, false);

console.log("\n5. AN UNMEASURABLE VALUE DOES NOT FAIL BY DEFAULT");
const blank = peak({ hf_snr_db: null });
check("blank HF SNR passes a rule it cannot be judged by",
      evaluate(blank, on("hfsnr", 10), false).pass, true);
check("...and is counted, so the operator can see it happened",
      evaluate(blank, on("hfsnr", 10), false).blanks, 1);
check("...and fails only when that is switched on",
      evaluate(blank, on("hfsnr", 10), true).pass, false);
check("...saying it was not measured, not that it was low",
      evaluate(blank, on("hfsnr", 10), true).failed[0].includes("not measured"),
      true);

console.log("\n6. the failures it reports are the ones that fired");
const multi = evaluate(peak({ snr_db: 5, peak_ratio: 2 }),
                       { ...on("snr", 20), ratio: { on: true, value: 10 } },
                       false);
check("both named", multi.failed.length, 2);
// Reported in RULES order, not in the order they were switched on.
check("with the measured value in each",
      multi.failed.join(" | ").includes("5.00")
      && multi.failed.join(" | ").includes("2.00"), true);

console.log("\n7. the counting rules, as the component states them");
// Read off the component source: these are the claims that keep the
// score honest, and each is a sentence somebody could quietly delete.
const body = SRC.slice(SRC.indexOf("export default function"));
check("a strike the gate refused is counted missed, separately",
      body.includes("trueMissedByGate"), true);
check("a strike with no peak at all is counted missed",
      body.includes("trueNoPeak"), true);
check("...and is in the recall denominator",
      /strikes\s*=\s*r\.trueRetained\.length[\s\S]{0,160}trueNoPeak\.length/
        .test(body), true);
check("a false positive the gate refused is NOT credited to the rules",
      body.includes("fpAlreadyRefused"), true);
check("retained means the gate cut it AND the rules passed it",
      body.includes("const retained = !!p.passed_gate && ev.pass"), true);
check("'uncertain' is excluded from both figures",
      body.includes('if (lab.label === "uncertain")'), true);
check("precision is over retained cuts only",
      body.includes("const cuts = r.trueRetained.length + r.fpRetained.length"),
      true);

console.log("\n8. and nothing here can reach the cutter");
// THE WORD "save" IN A COMMENT IS NOT A NETWORK CALL. Testing for
// the prose was testing my own comments; these are the things that
// would actually reach a server.
for (const [what, re] of [["fetch", /\bfetch\s*\(/],
                          ["an api helper", /\bapi\./],
                          ["XMLHttpRequest", /XMLHttpRequest/],
                          ["an import of the api module", /from "\.\.\/api/]]) {
  check(`no ${what}`, re.test(SRC), false);
}

console.log();
if (failed.length) {
  console.log(`FAILED (${failed.length}):`);
  failed.forEach((f) => console.log("  -", f));
  process.exit(1);
}
console.log("all ok");
