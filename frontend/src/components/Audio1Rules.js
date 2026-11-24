/*
 * WHAT A PROPOSED GATE SET WOULD ACTUALLY DO, counted honestly.
 *
 * Separate from the component because this is arithmetic, and
 * arithmetic that decides whether a tee box gets armed should be
 * testable without a browser.
 *
 * THREE THINGS HERE ARE EASY TO GET FLATTERINGLY WRONG.
 *
 * 1. A REFUSAL IS NOT ALWAYS RECOVERABLE. "The detector did not cut
 *    this" has three causes and they do not behave alike when a gate
 *    is retuned: it failed the attack gate, it never reached the
 *    amplitude threshold, or it passed both and a louder peak within
 *    the minimum separation suppressed it. The first two a gate value
 *    can undo. The third it cannot, ever. Counting all three as
 *    "potentially recovered" promises back strikes that no setting
 *    will bring, which is the kind of number that gets a tee box
 *    armed on a false premise.
 *
 * 2. A RECOVERY REQUIRES THE GATE THAT REFUSED IT TO BE IN THE
 *    PROPOSAL. A peak the attack gate threw out is recovered only if
 *    the proposal actually sets a looser attack gate. If that gate is
 *    switched off, the detector keeps the value it has and the peak
 *    stays refused however well it scores on everything else. So
 *    recovery is checked against the specific gate that did the
 *    refusing, not against the proposal as a whole.
 *
 * 3. A LOOSER GATE ADMITS NOISE TOO. Showing recovered strikes
 *    without the false positives the same loosening lets in is a
 *    one-sided number. Both are counted, and precision is computed
 *    over the proposed regime rather than the current one.
 *
 * AND: an unmeasurable value is not a failure by default. A quiet
 * strike from a far tee is exactly where HF SNR or a decay comes back
 * blank, and rejecting what could not be measured selects against
 * distance. It is a visible switch, and the count of rows it decided
 * is reported either way.
 */

/** Which gate controls each refusal reason. Null means no gate does. */
export const CONTROLLING_GATE = {
  attack: "audio_max_attack_ms",
  ratio: "audio_min_ratio",
  nms: null,
  unknown: null,
};

/** Rules with no gate behind them: measurable, arguable, not armable. */
export const EXTRA_RULES = [
  { key: "x_snr_db", label: "min SNR (dB)", cmp: "min", step: 1,
    read: (p) => p.snr_db,
    help: "20·log10(peak / background), broadband amplitude" },
  { key: "x_c20_200", label: "min E20/E200", cmp: "min", step: 0.05,
    read: (p) => p.concentration?.e20_over_e200,
    help: "how much of 200 ms of energy is in the middle 20 ms" },
  { key: "x_c50_200", label: "min E50/E200", cmp: "min", step: 0.05,
    read: (p) => p.concentration?.e50_over_e200,
    help: "the same over 50 ms" },
  { key: "x_c10_100", label: "min E10/E100", cmp: "min", step: 0.05,
    read: (p) => p.concentration?.e10_over_e100,
    help: "the tightest of the three" },
  { key: "x_c50_200_hf", label: "min E50/E200 (HF)", cmp: "min", step: 0.05,
    read: (p) => p.concentration_hf?.e50_over_e200,
    help: "the same over 50 ms, above 1500 Hz only" },
];

const STEP_FOR = (reads) => {
  if (reads === "peak_amplitude") return 0.005;
  if (reads === "hf_concentration") return 0.05;
  if (reads === "peak_ratio" || reads === "crest_factor") return 0.5;
  if (reads === "spectral_centroid_hz") return 100;
  return 1;
};

/**
 * The full rule list: every armable gate the server defines, then the
 * exploratory ones.
 *
 * Built from the server's `fields` rather than a copy kept here, so a
 * gate added on the server appears without an edit to the page, and a
 * gate that reads a NESTED measurement cannot be read from the wrong
 * key: the server resolves every gate's value onto `gate_values` for
 * exactly that reason.
 */
export function buildRules(fields) {
  const armable = Object.entries(fields || {}).map(([gate, f]) => ({
    key: gate,
    gate,
    label: f.label,
    cmp: f.cmp,
    step: STEP_FOR(f.reads),
    read: (p) => (p.gate_values ? p.gate_values[f.reads] : p[f.reads]),
    help: `gate ${gate} · reads ${f.reads}`,
  }));
  return [...armable, ...EXTRA_RULES.map((r) => ({ ...r, gate: null }))];
}

/** Does this peak clear every rule that is switched on? */
export function evaluate(peak, rules, ruleList, blankFails) {
  const failed = [];
  let blanks = 0;
  for (const r of ruleList) {
    const st = rules[r.key];
    if (!st?.on) continue;
    const want = Number(st.value);
    if (!Number.isFinite(want)) continue;
    const v = r.read(peak);
    if (v === null || v === undefined || Number.isNaN(Number(v))) {
      blanks += 1;
      if (blankFails) failed.push({ key: r.key, label: r.label, read: null });
      continue;
    }
    if (r.cmp === "min" ? Number(v) < want : Number(v) > want) {
      failed.push({ key: r.key, label: r.label, read: Number(v), want });
    }
  }
  return { pass: failed.length === 0, failed, blanks };
}

const STRIKE = "strike";
export const NOISE = ["ground", "wind", "voice", "other"];

/**
 * What the proposal does to every labelled peak, bucket by bucket.
 *
 * `peaks` are the measurement rows, `labels` the stored classifications
 * keyed by peak time to two decimals.
 */
export function classify(peaks, labels, rules, ruleList, blankFails) {
  const byTime = {};
  (peaks || []).forEach((p) => {
    if (p?.peak_time_sec != null) byTime[p.peak_time_sec.toFixed(2)] = p;
  });

  const out = {
    // strikes
    retained: [], lost: [], recovered: [], blockedUpstream: [],
    stillMissed: [], noPeak: [],
    // not-strikes
    stillCut: [], eliminated: [], admitted: [], stayRefused: [],
    // bookkeeping
    uncertain: 0, blanksDecided: 0, nLabelled: 0, unlabelled: 0,
    reasonsUnknown: 0,
  };

  const rows = [];

  Object.values(labels || {}).forEach((lab) => {
    if (lab.kind === "missed") {
      // A STRIKE THAT MADE NO PEAK AT ALL. No gate reaches it; it is
      // in the denominator of recall and nowhere else.
      if (lab.label === STRIKE) out.noPeak.push(lab);
      return;
    }
    const p = byTime[lab.peak_time_sec?.toFixed(2)];
    if (!p) return;
    out.nLabelled += 1;
    if (lab.label === "uncertain") { out.uncertain += 1; return; }

    const ev = evaluate(p, rules, ruleList, blankFails);
    out.blanksDecided += ev.blanks;
    const cut = !!p.passed_gate;
    const why = p.refused_by || (cut ? null : "unknown");
    const controlling = cut ? null : CONTROLLING_GATE[why];
    // The gate that refused it has to be part of the proposal, or the
    // detector keeps the value it has and nothing changes.
    const retunable = !cut && !!controlling && !!rules[controlling]?.on
      && Number.isFinite(Number(rules[controlling]?.value));
    if (!cut && !controlling) out.reasonsUnknown += 1;

    let bucket;
    if (lab.label === STRIKE) {
      if (cut) bucket = ev.pass ? "retained" : "lost";
      else if (!ev.pass) bucket = "stillMissed";
      else bucket = retunable ? "recovered" : "blockedUpstream";
    } else if (NOISE.includes(lab.label)) {
      if (cut) bucket = ev.pass ? "stillCut" : "eliminated";
      else if (!ev.pass) bucket = "stayRefused";
      else bucket = retunable ? "admitted" : "stayRefused";
    } else {
      return;
    }
    const row = { peak: p, label: lab.label, bucket, cut, why,
                  failed: ev.failed, pass: ev.pass };
    out[bucket].push(row);
    rows.push(row);
  });

  (peaks || []).forEach((p) => {
    if (p?.peak_time_sec == null) return;
    if (!labels?.[p.peak_time_sec.toFixed(2)]) out.unlabelled += 1;
  });

  const strikes = out.retained.length + out.lost.length
    + out.recovered.length + out.blockedUpstream.length
    + out.stillMissed.length + out.noPeak.length;

  // THE PROPOSED REGIME, not the current one: what the detector would
  // cut with these gates armed.
  const tpNow = out.retained.length + out.lost.length;
  const fpNow = out.stillCut.length + out.eliminated.length;
  const tpNew = out.retained.length + out.recovered.length;
  const fpNew = out.stillCut.length + out.admitted.length;

  const pc = (n, d) => (d ? (100 * n) / d : null);
  out.rows = rows.sort(
    (a, b) => a.peak.peak_time_sec - b.peak.peak_time_sec);
  out.strikes = strikes;
  out.now = {
    cuts: tpNow + fpNow, strikes: tpNow,
    recall: pc(tpNow, strikes), precision: pc(tpNow, tpNow + fpNow),
  };
  out.proposed = {
    cuts: tpNew + fpNew, strikes: tpNew,
    recall: pc(tpNew, strikes), precision: pc(tpNew, tpNew + fpNew),
  };
  return out;
}

/** Seed rule state from a tee preset, leaving everything else off. */
export function seedFromPreset(ruleList, gates) {
  const st = {};
  ruleList.forEach((r) => {
    const v = r.gate ? gates?.[r.gate] : undefined;
    st[r.key] = v === undefined || v === null
      ? { on: false, value: "" }
      : { on: true, value: String(v) };
  });
  return st;
}
