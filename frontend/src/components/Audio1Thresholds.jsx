import { useMemo, useState } from "react";

/*
 * A threshold sandbox: try a rule, see what it would have kept.
 *
 * NOTHING HERE TOUCHES PRODUCTION. It runs entirely in the browser
 * over the peaks already measured and the labels already stored.
 * There is no endpoint behind it, nothing is saved, and no number set
 * here can reach the cutter — which is the point: a rule should be
 * argued with on the bench before it is allowed near a clip.
 *
 * "RETAINED" MEANS THE DETECTOR CUT IT AND EVERY ENABLED THRESHOLD
 * ALSO PASSED IT. These thresholds can only ever take clips away: a
 * peak the detector already refused cannot be brought back by one, so
 * a strike among those is missed no matter what is set here. That is
 * the honest arithmetic and it is why the recall figure can never
 * reach 100% on a clip whose strike the gate dropped.
 *
 * AN UNMEASURABLE VALUE IS NOT A FAILURE BY DEFAULT. A quiet strike
 * from a far tee is exactly the case where HF SNR or a decay comes
 * back blank, and letting a threshold reject what it could not
 * measure would quietly select against distance. The behaviour is a
 * visible switch rather than a hidden convention, and the count of
 * rows it decided is shown either way.
 */

export const RULES = [
  { key: "ratio", label: "min peak ratio", cmp: "min", step: 0.5,
    get: (p) => p.peak_ratio,
    help: "peak envelope over the background median" },
  { key: "snr", label: "min SNR (dB)", cmp: "min", step: 1,
    get: (p) => p.snr_db,
    help: "20·log10(peak / background), broadband amplitude" },
  { key: "atk0", label: "max original attack (ms)", cmp: "max", step: 1,
    get: (p) => p.detector_attack_ms,
    help: "the detector's own rise time, measured on 1500 Hz "
          + "high-passed audio at 22050 Hz" },
  { key: "hfsnr", label: "min HF SNR (dB)", cmp: "min", step: 1,
    get: (p) => p.hf_snr_db,
    help: "10·log10(energy above 1500 Hz, peak vs background)" },
  { key: "iso", label: "min isolation (dB)", cmp: "min", step: 1,
    get: (p) => p.isolation_db,
    help: "how far the loudest thing in the 200 ms after sits below "
          + "the peak" },
  { key: "c20", label: "min E20/E200", cmp: "min", step: 0.05,
    get: (p) => p.concentration?.e20_over_e200,
    help: "how much of 200 ms of energy is in the middle 20 ms" },
  { key: "c50", label: "min E50/E200", cmp: "min", step: 0.05,
    get: (p) => p.concentration?.e50_over_e200,
    help: "the same over 50 ms" },
  { key: "c10", label: "min E10/E100", cmp: "min", step: 0.05,
    get: (p) => p.concentration?.e10_over_e100,
    help: "the tightest of the three" },
  { key: "c20hf", label: "min E20/E200 (HF)", cmp: "min", step: 0.05,
    get: (p) => p.concentration_hf?.e20_over_e200,
    help: "the same, above 1500 Hz only" },
  { key: "c50hf", label: "min E50/E200 (HF)", cmp: "min", step: 0.05,
    get: (p) => p.concentration_hf?.e50_over_e200,
    help: "the same, above 1500 Hz only" },
];

const STRIKE = "strike";
const NOISE = ["ground", "wind", "voice", "other"];

/** Does this peak clear every rule that is switched on? */
export function evaluate(peak, rules, blankFails) {
  const failed = [];
  let blanks = 0;
  for (const r of RULES) {
    const st = rules[r.key];
    if (!st?.on) continue;
    const v = r.get(peak);
    if (v === null || v === undefined || Number.isNaN(v)) {
      blanks += 1;
      if (blankFails) failed.push(`${r.label} (not measured)`);
      continue;
    }
    const want = Number(st.value);
    if (!Number.isFinite(want)) continue;
    if (r.cmp === "min" ? Number(v) < want : Number(v) > want) {
      failed.push(`${r.label} ${Number(v).toFixed(2)}`);
    }
  }
  return { pass: failed.length === 0, failed, blanks };
}

export default function Audio1Thresholds({ peaks, labels }) {
  const [rules, setRules] = useState(() =>
    Object.fromEntries(RULES.map((r) => [r.key, { on: false, value: "" }])));
  const [blankFails, setBlankFails] = useState(false);

  const set = (k, patch) =>
    setRules((s) => ({ ...s, [k]: { ...s[k], ...patch } }));

  const result = useMemo(() => {
    const byTime = {};
    (peaks || []).forEach((p) => {
      byTime[p.peak_time_sec.toFixed(2)] = p;
    });
    const r = {
      trueRetained: [], trueMissedByRule: [], trueMissedByGate: [],
      trueNoPeak: [], fpRetained: [], fpEliminated: [],
      fpAlreadyRefused: [], uncertain: 0, blanksDecided: 0,
      nLabelled: 0,
    };
    Object.values(labels || {}).forEach((lab) => {
      if (lab.kind === "missed") {
        if (lab.label === STRIKE) r.trueNoPeak.push(lab);
        return;
      }
      const key = lab.peak_time_sec.toFixed(2);
      const p = byTime[key];
      if (!p) return;
      r.nLabelled += 1;
      if (lab.label === "uncertain") { r.uncertain += 1; return; }
      const ev = evaluate(p, rules, blankFails);
      r.blanksDecided += ev.blanks;
      const retained = !!p.passed_gate && ev.pass;
      if (lab.label === STRIKE) {
        if (retained) r.trueRetained.push(p);
        else if (!p.passed_gate) r.trueMissedByGate.push(p);
        else r.trueMissedByRule.push({ p, why: ev.failed });
      } else if (NOISE.includes(lab.label)) {
        if (retained) r.fpRetained.push(p);
        else if (!p.passed_gate) r.fpAlreadyRefused.push(p);
        else r.fpEliminated.push({ p, why: ev.failed });
      }
    });
    const strikes = r.trueRetained.length + r.trueMissedByRule.length
      + r.trueMissedByGate.length + r.trueNoPeak.length;
    const cuts = r.trueRetained.length + r.fpRetained.length;
    return {
      ...r,
      strikes,
      recall: strikes ? (100 * r.trueRetained.length) / strikes : null,
      precision: cuts ? (100 * r.trueRetained.length) / cuts : null,
    };
  }, [peaks, labels, rules, blankFails]);

  const anyOn = RULES.some((r) => rules[r.key]?.on);
  const pc = (v) => (v === null ? "—" : `${v.toFixed(0)}%`);

  return (
    <div style={{ marginTop: 10, paddingTop: 8,
                  borderTop: "1px solid var(--line)" }}>
      <div className="small"><b>Threshold sandbox</b>{" "}
        <span className="muted tiny">
          · try a rule against what you have labelled · nothing here is
          saved and nothing reaches the cutter
        </span>
      </div>

      <div style={{ display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fill, minmax(230px, 1fr))",
                    gap: "2px 14px", marginTop: 6 }}>
        {RULES.map((r) => (
          <label key={r.key} className="tiny" title={r.help}
                 style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={!!rules[r.key]?.on}
                   onChange={(e) => set(r.key, { on: e.target.checked })}
                   style={{ margin: 0 }} />
            <span style={{ flex: 1, opacity: rules[r.key]?.on ? 1 : 0.6 }}>
              {r.label}
            </span>
            <input type="text" inputMode="decimal"
                   value={rules[r.key]?.value ?? ""}
                   disabled={!rules[r.key]?.on}
                   step={r.step}
                   onChange={(e) => set(r.key, { value: e.target.value })}
                   style={{ width: 58, fontSize: 11, padding: "1px 4px" }} />
          </label>
        ))}
      </div>

      <label className="tiny" style={{ display: "inline-flex", gap: 5,
                                       alignItems: "center",
                                       marginTop: 6 }}
             title={"A quiet strike from a far tee is exactly where HF "
                    + "SNR or a decay comes back blank. Letting a "
                    + "threshold reject what it could not measure "
                    + "selects against distance, so OFF is the default."}>
        <input type="checkbox" checked={blankFails}
               onChange={(e) => setBlankFails(e.target.checked)}
               style={{ margin: 0 }} />
        a value that could not be measured FAILS its threshold
        {result.blanksDecided > 0 && (
          <span className="muted">
            {" "}· {result.blanksDecided} blank value
            {result.blanksDecided === 1 ? "" : "s"} decided this way
          </span>
        )}
      </label>

      {result.nLabelled === 0 && result.trueNoPeak.length === 0 ? (
        <div className="tiny muted" style={{ marginTop: 8 }}>
          Nothing is labelled on this upload yet, so there is nothing to
          score a rule against. Classify a few peaks above first.
        </div>
      ) : (
        <div style={{ marginTop: 8 }}>
          <table className="tiny" style={{ borderCollapse: "collapse" }}>
            <tbody>
              {[
                ["True strikes retained", result.trueRetained.length,
                 "labelled a strike, cut by the detector, and clears "
                 + "every rule you have switched on"],
                ["True strikes missed",
                 result.trueMissedByRule.length
                 + result.trueMissedByGate.length
                 + result.trueNoPeak.length,
                 `${result.trueMissedByRule.length} by your rules, `
                 + `${result.trueMissedByGate.length} the detector `
                 + `already refused, ${result.trueNoPeak.length} that `
                 + "made no peak at all"],
                ["False positives retained", result.fpRetained.length,
                 "labelled not-a-strike and still cut"],
                ["False positives eliminated", result.fpEliminated.length,
                 `by your rules · ${result.fpAlreadyRefused.length} more `
                 + "were already refused by the detector, which your "
                 + "rules cannot take credit for"],
              ].map(([k, v, why]) => (
                <tr key={k}>
                  <td className="muted" style={{ paddingRight: 12 }}>{k}</td>
                  <td style={{ fontWeight: 600, paddingRight: 12,
                               fontVariantNumeric: "tabular-nums" }}>{v}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{why}</td>
                </tr>
              ))}
              <tr style={{ borderTop: "1px solid var(--line)" }}>
                <td className="muted" style={{ paddingRight: 12 }}>
                  Strikes retained
                </td>
                <td style={{ fontWeight: 600, paddingRight: 12 }}>
                  {pc(result.recall)}
                </td>
                <td className="muted" style={{ fontSize: 11 }}>
                  of {result.strikes} labelled strike
                  {result.strikes === 1 ? "" : "s"}, including any with no
                  peak
                </td>
              </tr>
              <tr>
                <td className="muted" style={{ paddingRight: 12 }}>
                  Retained cuts that are strikes
                </td>
                <td style={{ fontWeight: 600, paddingRight: 12 }}>
                  {pc(result.precision)}
                </td>
                <td className="muted" style={{ fontSize: 11 }}>
                  {result.uncertain > 0
                    ? `${result.uncertain} "uncertain" left out of both`
                    : "uncertain labels are left out of both"}
                </td>
              </tr>
            </tbody>
          </table>

          {!anyOn && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              No rule is switched on, so this is the detector on its own —
              the baseline every rule has to beat.
            </div>
          )}
          {result.trueMissedByRule.length > 0 && (
            <div className="tiny" style={{ marginTop: 4,
                                           color: "var(--rose-700, #b4254b)" }}>
              <b>strikes your rules would have thrown away:</b>{" "}
              {result.trueMissedByRule.map(({ p, why }) => (
                <span key={p.peak_time_sec} style={{ marginRight: 10 }}>
                  {p.peak_time_sec.toFixed(2)}s ({why.join(", ")})
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
