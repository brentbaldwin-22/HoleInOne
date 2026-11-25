#!/usr/bin/env node
/*
 * Every measurement the operator asked to gate on is reachable, and
 * each gate is applied the right way round.
 *
 * THE ARITHMETIC MOVED. This file used to carry the sandbox's counting
 * rules, extracted out of the .jsx with esbuild because they lived
 * inside the component. They now live in Audio1Rules.js with their own
 * test -- tools/audio1_proposal_test.mjs -- which covers the ways a
 * proposed gate set flatters itself, recovery included. What is left
 * here is the part that test cannot see: that the LIST of gates is
 * complete, and that min and max are not transposed.
 *
 * WHY THE LIST NEEDS GUARDING. The armable gates are built from the
 * server's own table, so a gate added there appears here without an
 * edit -- which is the point, and also means a gate RENAMED there
 * silently disappears from the page rather than breaking it. A missing
 * gate is a bar nobody can set, shown as a sandbox that simply does
 * not offer it.
 *
 *   node tools/audio1_thresholds_test.mjs
 */
import { buildRules, evaluate } from
  "../frontend/src/components/Audio1Rules.js";

const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(60)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

// The server's table, verbatim from debug3.AUDIO_GATE_FIELDS. Kept
// here deliberately: if the two disagree, this test is the place that
// says so.
const FIELDS = {
  audio_min_hf_snr_db: { cmp: "min", reads: "hf_snr_db", label: "HF SNR (dB)" },
  audio_min_crest: { cmp: "min", reads: "crest_factor", label: "crest factor" },
  audio_max_attack_ms: { cmp: "max", reads: "detector_attack_ms",
                         label: "detector attack (ms)" },
  audio_min_ratio: { cmp: "min", reads: "peak_ratio", label: "peak ratio" },
  audio_min_amplitude: { cmp: "min", reads: "peak_amplitude",
                         label: "minimum amplitude" },
  audio_max_raw_attack_ms: { cmp: "max", reads: "attack_ms",
                             label: "raw 10-90 attack (ms)" },
  audio_min_centroid_hz: { cmp: "min", reads: "spectral_centroid_hz",
                           label: "spectral centroid (Hz)" },
  // A FLOOR AND A CEILING ON ONE MEASUREMENT. Both read
  // `spectral_centroid_hz`; together they are a band, and a gate
  // table that allowed only one of them could express "bright
  // enough" but never "bright, but not THAT bright".
  audio_max_centroid_hz: { cmp: "max", reads: "spectral_centroid_hz",
                           label: "spectral centroid (Hz)" },
  audio_min_hf_concentration: { cmp: "min", reads: "hf_concentration",
                                label: "HF energy concentration" },
  audio_min_isolation_db: { cmp: "min", reads: "isolation_db",
                            label: "isolation (dB)" },
  audio_max_decay_ms: { cmp: "max", reads: "decay_ms", label: "decay (ms)" },
  audio_min_confidence: { cmp: "min", reads: "confidence_score",
                          label: "confidence score" },
};
const RULES = buildRules(FIELDS);

console.log("\n0. the table above still matches the server's");
{
  // THE DRIFT THIS FILE CLAIMS TO CATCH. A copy of the server's table
  // that nothing compares is not a guard, it is a second opinion --
  // so read the real one and diff it.
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const { dirname, join } = await import("node:path");
  const src = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)),
         "..", "backend/app/services/debug3.py"), "utf8");
  const body = src.slice(src.indexOf("AUDIO_GATE_FIELDS = {"));
  const block = body.slice(0, body.indexOf("\n}\n"));
  const got = {};
  const re = /"(audio_[a-z0-9_]+)":\s*\(\s*"(min|max)"\s*,\s*"([a-z0-9_]+)"/g;
  let m;
  while ((m = re.exec(block)) !== null) got[m[1]] = { cmp: m[2], reads: m[3] };
  check("the same gates, no more and no fewer",
        Object.keys(got).sort(), Object.keys(FIELDS).sort());
  for (const k of Object.keys(got)) {
    if (!FIELDS[k]) continue;
    check(`  ${k} reads ${got[k].reads} as ${got[k].cmp}`,
          [FIELDS[k].cmp, FIELDS[k].reads], [got[k].cmp, got[k].reads]);
  }
}

const byKey = Object.fromEntries(RULES.map((r) => [r.key, r]));
const on = (k, v) => ({ [k]: { on: true, value: String(v) } });
const peak = (gv, flat) => ({ peak_time_sec: 1, passed_gate: true,
                              gate_values: gv || {}, ...(flat || {}) });

console.log("\n1. every gate the server defines is offered");
for (const k of Object.keys(FIELDS)) {
  check(`  ${k}`, !!byKey[k]);
}
check("  and each is armable", RULES.filter((r) => r.gate).length,
      Object.keys(FIELDS).length);

console.log("\n2. the measurements with no gate are still argued with");
for (const k of ["x_snr_db", "x_c20_200", "x_c50_200", "x_c10_100",
                 "x_c50_200_hf"]) {
  check(`  ${k}`, !!byKey[k]);
}
check("  and none of them claims to be armable",
      RULES.filter((r) => !r.gate).every((r) => r.gate === null));

console.log("\n2b. two gates may read one measurement, in opposite directions");
{
  const pair = RULES.filter((r) => r.gate
    && /centroid/.test(r.gate));
  check("both centroid gates are offered", pair.length, 2);
  check("...and they are distinct rules", new Set(pair.map((r) => r.key)).size,
        2);
  // THE LABEL IS NOT THE KEY. Both say "spectral centroid (Hz)"; the
  // direction is what separates them, and a sandbox keyed on the
  // label would let one overwrite the other.
  check("...sharing a label", new Set(pair.map((r) => r.label)).size, 1);
  check("...but opposite directions",
        pair.map((r) => r.cmp).sort(), ["max", "min"]);
}

console.log("\n3. min and max are applied the right way round");
check("min HF SNR 20 keeps 30",
      evaluate(peak({ hf_snr_db: 30 }), on("audio_min_hf_snr_db", 20),
               RULES, false).pass, true);
check("min HF SNR 20 drops 10",
      evaluate(peak({ hf_snr_db: 10 }), on("audio_min_hf_snr_db", 20),
               RULES, false).pass, false);
check("max attack 20 keeps 8",
      evaluate(peak({ detector_attack_ms: 8 }), on("audio_max_attack_ms", 20),
               RULES, false).pass, true);
check("max attack 20 drops 40",
      evaluate(peak({ detector_attack_ms: 40 }), on("audio_max_attack_ms", 20),
               RULES, false).pass, false);
check("a bar met exactly is not a failure",
      evaluate(peak({ peak_ratio: 3.5 }), on("audio_min_ratio", 3.5),
               RULES, false).pass, true);

console.log("\n3b. a floor and a ceiling together make a band");
{
  const band = { ...on("audio_min_centroid_hz", 1000),
                 ...on("audio_max_centroid_hz", 6000) };
  const at = (hz) => evaluate(peak({ spectral_centroid_hz: hz }), band,
                              RULES, false);
  check("inside the band passes", at(3453).pass, true);
  check("below it fails the floor", at(400).failed.length, 1);
  check("above it fails the ceiling", at(9000).failed.length, 1);
  check("and far outside fails only the one it broke",
        at(9000).failed[0].key, "audio_max_centroid_hz");
}

console.log("\n4. a gate reads the server's resolved value, not a flat key");
// `hf_concentration` is nested under `concentration_hf` on a
// measurement row; the server resolves it onto `gate_values` so this
// page never has to know that.
check("the resolved value decides",
      evaluate(peak({ hf_concentration: 0.42 }),
               on("audio_min_hf_concentration", 0.3), RULES, false).pass, true);
check("...and a bar above it fails",
      evaluate(peak({ hf_concentration: 0.42 }),
               on("audio_min_hf_concentration", 0.5), RULES, false).pass, false);

console.log("\n5. a bench rule reads the measurement row directly");
check("broadband SNR is off the row, not the gate view",
      evaluate(peak({}, { snr_db: 30 }), on("x_snr_db", 20),
               RULES, false).pass, true);
check("a nested ratio likewise",
      evaluate(peak({}, { concentration: { e20_over_e200: 0.42 } }),
               on("x_c20_200", 0.5), RULES, false).pass, false);

console.log("\n6. every failure is named, with what was read");
const multi = evaluate(peak({ hf_snr_db: 5, crest_factor: 2 }),
                       { ...on("audio_min_hf_snr_db", 20),
                         ...on("audio_min_crest", 7) }, RULES, false);
check("both named", multi.failed.length, 2);
check("with the measured value in each",
      multi.failed.map((f) => f.read).sort(), [2, 5]);
check("and the bar it was judged against",
      multi.failed.map((f) => f.want).sort(), [20, 7].sort());

console.log(`\n${failed.length
  ? `${failed.length} FAILED:\n  ${failed.join("\n  ")}` : "all ok"}`);
process.exit(failed.length ? 1 : 0);
