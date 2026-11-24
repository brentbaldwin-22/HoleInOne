/*
 * What a proposed gate set would do -- and the three ways that number
 * flatters itself if nobody checks.
 *
 *   A SUPPRESSED PEAK IS NOT RECOVERABLE. Non-maximum suppression is
 *   not a bar to be lowered; no gate value undoes it. Counted as
 *   "potentially recovered" it promises back strikes that no setting
 *   will bring.
 *
 *   A RECOVERY NEEDS THE GATE THAT REFUSED IT IN THE PROPOSAL. A peak
 *   the attack gate threw out comes back only if the proposal sets a
 *   looser attack gate. Score it on everything else and leave that
 *   gate off and the detector keeps the value it has.
 *
 *   A LOOSER GATE ADMITS NOISE. Recovered strikes without the false
 *   positives the same loosening lets in is half an answer.
 *
 *   node tools/audio1_proposal_test.mjs
 */
import {
  buildRules, classify, evaluate, seedFromPreset,
} from "../frontend/src/components/Audio1Rules.js";

const FAILED = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log("  %s %s", label.padEnd(62),
              ok ? "ok" : `FAIL got ${JSON.stringify(got)}`);
  if (!ok) FAILED.push(label);
};

// The server's field table, as /audio-gate-presets serves it.
const FIELDS = {
  audio_min_hf_snr_db: { cmp: "min", reads: "hf_snr_db", label: "HF SNR (dB)" },
  audio_min_crest: { cmp: "min", reads: "crest_factor", label: "crest factor" },
  audio_max_attack_ms: { cmp: "max", reads: "detector_attack_ms",
                         label: "detector attack (ms)" },
  audio_min_ratio: { cmp: "min", reads: "peak_ratio", label: "peak ratio" },
  audio_min_amplitude: { cmp: "min", reads: "peak_amplitude",
                         label: "minimum amplitude" },
  audio_min_hf_concentration: { cmp: "min", reads: "hf_concentration",
                                label: "HF energy concentration" },
  audio_min_confidence: { cmp: "min", reads: "confidence_score",
                          label: "confidence score" },
};
const RULES = buildRules(FIELDS);

let n = 0;
const peak = (t, { cut = true, why = null, ...vals } = {}) => {
  n += 1;
  return {
    peak_time_sec: t, passed_gate: cut, refused_by: cut ? null : why,
    gate_values: {
      hf_snr_db: vals.hf ?? 20, crest_factor: vals.crest ?? 8,
      detector_attack_ms: vals.atk ?? 11, peak_ratio: vals.ratio ?? 5,
      peak_amplitude: vals.amp ?? 0.1,
      hf_concentration: vals.c ?? 0.6,
      confidence_score: vals.conf ?? 80,
    },
    snr_db: vals.snr ?? 30,
    concentration: { e20_over_e200: 0.7, e50_over_e200: 0.8,
                     e10_over_e100: 0.6 },
    concentration_hf: { e50_over_e200: 0.8 },
  };
};
const labelsOf = (pairs) => Object.fromEntries(
  pairs.map(([t, label, kind]) => [
    t.toFixed(2), { peak_time_sec: t, label, kind: kind || "detected" }]));

const on = (o) => {
  const st = Object.fromEntries(RULES.map((r) => [r.key,
                                                  { on: false, value: "" }]));
  for (const [k, v] of Object.entries(o)) st[k] = { on: true, value: String(v) };
  return st;
};

console.log("\n1. the baseline: no rule on is the detector on its own");
{
  const peaks = [peak(1), peak(2), peak(3, { cut: false, why: "attack" })];
  const labels = labelsOf([[1, "strike"], [2, "ground"], [3, "strike"]]);
  const r = classify(peaks, labels, on({}), RULES, false);
  check("the strike it cut is retained", r.retained.length, 1);
  check("the noise it cut is still cut", r.stillCut.length, 1);
  check("the strike it refused is not recovered by an empty proposal",
        r.recovered.length, 0);
  check("...it is still missed", r.stillMissed.length + r.blockedUpstream.length,
        1);
  check("recall now", Math.round(r.now.recall), 50);
  check("precision now", Math.round(r.now.precision), 50);
  check("and the proposal changes neither",
        [Math.round(r.proposed.recall), Math.round(r.proposed.precision)],
        [50, 50]);
}

console.log("\n2. a tighter gate takes a false positive away");
{
  const peaks = [peak(1, { crest: 9 }), peak(2, { crest: 3 })];
  const labels = labelsOf([[1, "strike"], [2, "ground"]]);
  const r = classify(peaks, labels, on({ audio_min_crest: 7 }), RULES, false);
  check("the strike survives", r.retained.length, 1);
  check("the false positive is eliminated", r.eliminated.length, 1);
  check("which gate did it", r.eliminated[0].failed[0].label, "crest factor");
  check("precision goes to 100", Math.round(r.proposed.precision), 100);
  check("recall is unchanged", Math.round(r.proposed.recall), 100);
}

console.log("\n3. a tighter gate that costs a strike says so");
{
  const peaks = [peak(1, { crest: 5 }), peak(2, { crest: 3 })];
  const labels = labelsOf([[1, "strike"], [2, "ground"]]);
  const r = classify(peaks, labels, on({ audio_min_crest: 7 }), RULES, false);
  check("the strike is lost", r.lost.length, 1);
  check("and named with its reading", r.lost[0].failed[0].read, 5);
  check("recall falls to 0", Math.round(r.proposed.recall), 0);
  check("precision is undefined with nothing cut", r.proposed.precision, null);
}

console.log("\n4. RECOVERY, and only where the refusing gate is in the proposal");
{
  // A strike the attack gate refused at 20 ms.
  const peaks = [peak(5, { cut: false, why: "attack", atk: 20 })];
  const labels = labelsOf([[5, "strike"]]);

  const noAttackRule = classify(peaks, labels, on({ audio_min_crest: 2 }),
                                RULES, false);
  check("scoring well on OTHER gates does not recover it",
        noAttackRule.recovered.length, 0);
  check("...it is reported as blocked upstream instead",
        noAttackRule.blockedUpstream.length, 1);

  const loose = classify(peaks, labels, on({ audio_max_attack_ms: 25 }),
                         RULES, false);
  check("a looser attack gate recovers it", loose.recovered.length, 1);
  check("recall goes to 100", Math.round(loose.proposed.recall), 100);
  check("and the current regime still reads 0",
        Math.round(loose.now.recall), 0);

  const tight = classify(peaks, labels, on({ audio_max_attack_ms: 14 }),
                         RULES, false);
  check("an attack gate that still refuses it recovers nothing",
        tight.recovered.length, 0);
  check("...and it counts as still missed", tight.stillMissed.length, 1);
}

console.log("\n5. a SUPPRESSED peak is never recovered, however it scores");
{
  const peaks = [peak(5, { cut: false, why: "nms" })];
  const labels = labelsOf([[5, "strike"]]);
  const r = classify(peaks, labels,
                     on({ audio_max_attack_ms: 200, audio_min_ratio: 0.1 }),
                     RULES, false);
  check("no gate value brings it back", r.recovered.length, 0);
  check("it is counted as blocked upstream", r.blockedUpstream.length, 1);
  check("and it stays in the denominator of recall", r.strikes, 1);
  check("so recall cannot be claimed", Math.round(r.proposed.recall), 0);
}

console.log("\n6. a refusal with no recorded reason is not guessed at");
{
  const peaks = [peak(5, { cut: false, why: null })];
  const labels = labelsOf([[5, "strike"]]);
  const r = classify(peaks, labels, on({ audio_min_ratio: 0.1 }), RULES, false);
  check("not recovered", r.recovered.length, 0);
  check("counted as blocked upstream", r.blockedUpstream.length, 1);
  check("and the unknown count is surfaced", r.reasonsUnknown, 1);
}

console.log("\n7. loosening admits noise, and the noise is counted");
{
  const peaks = [
    peak(1, { cut: false, why: "ratio", ratio: 1.8 }),   // a quiet strike
    peak(2, { cut: false, why: "ratio", ratio: 1.6 }),   // and a scuff
  ];
  const labels = labelsOf([[1, "strike"], [2, "ground"]]);
  const r = classify(peaks, labels, on({ audio_min_ratio: 1.3 }), RULES, false);
  check("the strike is recovered", r.recovered.length, 1);
  check("the scuff is admitted, not hidden", r.admitted.length, 1);
  check("proposed cuts counts both", r.proposed.cuts, 2);
  check("recall rises to 100", Math.round(r.proposed.recall), 100);
  check("but precision is only 50", Math.round(r.proposed.precision), 50);
  check("and the current regime cut nothing at all", r.now.cuts, 0);
}

console.log("\n8. a strike that made no peak is in recall and nowhere else");
{
  const peaks = [peak(1)];
  const labels = { ...labelsOf([[1, "strike"]]),
                   "9.00": { peak_time_sec: 9, label: "strike",
                             kind: "missed" } };
  const r = classify(peaks, labels, on({}), RULES, false);
  check("two strikes in the denominator", r.strikes, 2);
  check("one of them made no peak", r.noPeak.length, 1);
  check("recall is 50, not 100", Math.round(r.proposed.recall), 50);
  check("precision is unaffected by it", Math.round(r.proposed.precision), 100);
}

console.log("\n9. an unmeasurable value does not fail by default");
{
  const p = peak(1);
  p.gate_values.hf_snr_db = null;
  const labels = labelsOf([[1, "strike"]]);
  const lenient = classify([p], labels, on({ audio_min_hf_snr_db: 20 }),
                           RULES, false);
  check("the blank passes", lenient.retained.length, 1);
  check("and is counted as a blank decided", lenient.blanksDecided, 1);
  const strict = classify([p], labels, on({ audio_min_hf_snr_db: 20 }),
                          RULES, true);
  check("with the switch on, it fails", strict.lost.length, 1);
  check("...named as not measured", strict.lost[0].failed[0].read, null);
}

console.log("\n10. a rule switched on with no number set decides nothing");
{
  const peaks = [peak(1, { crest: 1 })];
  const labels = labelsOf([[1, "strike"]]);
  const r = classify(peaks, labels,
                     { ...on({}), audio_min_crest: { on: true, value: "" } },
                     RULES, false);
  check("a blank bar is skipped, not read as zero", r.retained.length, 1);
}

console.log("\n11. uncertain labels are in neither figure");
{
  const peaks = [peak(1), peak(2)];
  const labels = labelsOf([[1, "strike"], [2, "uncertain"]]);
  const r = classify(peaks, labels, on({}), RULES, false);
  check("counted as uncertain", r.uncertain, 1);
  check("one strike only", r.strikes, 1);
  check("and one cut only", r.proposed.cuts, 1);
}

console.log("\n12. the preset seeds exactly its own gates");
{
  const st = seedFromPreset(RULES, {
    audio_min_hf_snr_db: 20, audio_min_crest: 7, audio_max_attack_ms: 14,
    audio_min_ratio: 3.5, audio_min_amplitude: 0.05,
  });
  check("the five it names are on", RULES.filter((r) => st[r.key].on).length, 5);
  check("with its values", st.audio_min_crest.value, "7");
  check("a gate it does not name stays off",
        st.audio_min_confidence.on, false);
  check("and the exploratory rules stay off", st.x_snr_db.on, false);
}

console.log("\n13. a gate reading a NESTED measurement reads it correctly");
{
  const p = peak(1, { c: 0.2 });
  const labels = labelsOf([[1, "strike"]]);
  const r = classify([p], labels, on({ audio_min_hf_concentration: 0.5 }),
                     RULES, false);
  check("the server's resolved value is what is compared",
        r.lost[0].failed[0].read, 0.2);
  // THE POINT: not read off a flat key of the same name, which does
  // not exist on a measurement row.
  check("and the flat key it is NOT read from is absent",
        p.hf_concentration === undefined);
}

console.log("\n14. evaluate names every gate that failed, not just the first");
{
  const ev = evaluate(peak(1, { crest: 1, hf: 1 }),
                      on({ audio_min_crest: 7, audio_min_hf_snr_db: 20 }),
                      RULES, false);
  check("two failures", ev.failed.length, 2);
  check("by gate name", ev.failed.map((f) => f.key).sort(),
        ["audio_min_crest", "audio_min_hf_snr_db"]);
}

console.log("\n%s", FAILED.length
  ? `${FAILED.length} FAILED:\n  ${FAILED.join("\n  ")}` : "all ok");
process.exit(FAILED.length ? 1 : 0);
