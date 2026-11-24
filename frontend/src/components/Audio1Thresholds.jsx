import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { zoneName } from "../zones";
import {
  buildRules, clampedTo, classify, seedFromPreset,
} from "./Audio1Rules.js";

/*
 * A gate sandbox: try a tee box's gates, see what they would have cut,
 * then arm them for that tee box if you believe the numbers.
 *
 * THE ARITHMETIC LIVES IN Audio1Rules.js and is tested without a
 * browser, because it is what decides whether a tee box gets armed.
 * This file is the screen around it.
 *
 * WHAT CHANGED WHEN RECOVERY ARRIVED. This panel used to AND its rules
 * with the detector's own decision, so a rule could only ever take
 * clips away and the honest answer to "what would a lower gate
 * recover?" was "this cannot tell you". It can now, because the
 * measurement pass carries WHY the detector refused each peak -- and
 * the three reasons do not behave alike. See Audio1Rules.js.
 *
 * NOTHING IS LIVE UNTIL IT IS ARMED. Applying a preset writes the
 * gates onto this hole's tuning for one tee box, and arming is a
 * value in that same tuning. Both are data: no deploy, and a gate
 * with a bar set and arming off changes nothing that gets cut, which
 * is how a tee box is tuned on real traffic before it is trusted
 * with it.
 */

const BUCKETS = [
  ["retained", "True strikes retained", true,
   "labelled a strike, the detector cut it, and it clears every gate "
   + "you have switched on"],
  ["recovered", "Refused strikes recovered", true,
   "the detector refused it, the gate that refused it is in your "
   + "proposal, and your proposal admits it"],
  ["lost", "True strikes LOST", false,
   "the detector cut it and your gates would throw it away"],
  ["blockedUpstream", "Strikes your gates cannot reach", false,
   "your proposal would admit these, but they were suppressed by a "
   + "louder peak nearby, or refused for a reason no gate controls. "
   + "No value set here recovers them."],
  ["stillMissed", "Strikes still refused", false,
   "refused now, and your proposal refuses them too"],
  ["noPeak", "Strikes that made no peak at all", false,
   "marked by hand because nothing was detected there. In the recall "
   + "denominator and nowhere else."],
  ["eliminated", "False cuts eliminated", true,
   "labelled not-a-strike, cut now, and your gates would drop them"],
  ["stillCut", "False cuts remaining", false,
   "labelled not-a-strike and your gates still admit them"],
  ["admitted", "NEW false cuts admitted", false,
   "refused now, and your loosening would let them in. The cost of a "
   + "recovery, counted on the same screen as the recovery."],
];

function Pct({ v }) {
  return <>{v === null || v === undefined ? "—" : `${v.toFixed(0)}%`}</>;
}

export default function Audio1Thresholds({
  peaks, labels, uploadId, adminPassword, zoneOptions,
}) {
  const [fields, setFields] = useState(null);
  const [presets, setPresets] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [rules, setRules] = useState({});
  const [blankFails, setBlankFails] = useState(false);
  const [zone, setZone] = useState("");
  const [arm, setArm] = useState(false);
  const [applied, setApplied] = useState(null);
  const [busy, setBusy] = useState(false);
  const [seeded, setSeeded] = useState(null);
  const [showRows, setShowRows] = useState(false);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const got = await api.audioGatePresets(adminPassword);
        if (dead) return;
        setFields(got.fields || {});
        setPresets(got.presets || {});
      } catch (e) {
        if (!dead) setLoadErr(e.message);
      }
    })();
    return () => { dead = true; };
  }, [adminPassword]);

  const ruleList = useMemo(() => buildRules(fields), [fields]);

  // Every rule off until a preset is chosen, so the first thing shown
  // is the detector on its own -- the baseline a proposal has to beat.
  useEffect(() => {
    if (!ruleList.length) return;
    setRules((s) => (Object.keys(s).length ? s
      : Object.fromEntries(ruleList.map((r) => [r.key,
                                                { on: false, value: "" }]))));
  }, [ruleList]);

  const set = (k, patch) =>
    setRules((s) => ({ ...s, [k]: { ...(s[k] || {}), ...patch } }));

  const seed = useCallback((key) => {
    const p = presets?.[key];
    if (!p) return;
    setRules(seedFromPreset(ruleList, p.gates));
    setSeeded(key);
  }, [presets, ruleList]);

  const r = useMemo(
    () => classify(peaks, labels, rules, ruleList, blankFails),
    [peaks, labels, rules, ruleList, blankFails]);

  // WHAT WOULD BE WRITTEN: every armable gate, with its value where it
  // is switched on and null where it is not. Null rather than absent,
  // because a gate left out of the payload keeps whatever was stored
  // before -- so switching one off and saving would not switch it off.
  const proposalGates = useMemo(() => {
    const out = {};
    ruleList.forEach((q) => {
      if (!q.gate) return;
      const st = rules[q.key];
      const v = st?.on ? Number(st.value) : NaN;
      out[q.gate] = Number.isFinite(v) ? v : null;
    });
    return out;
  }, [rules, ruleList]);
  const gatesOn = Object.entries(proposalGates).filter(([, v]) => v !== null);

  const anyOn = ruleList.some((q) => rules[q.key]?.on);
  const extrasOn = ruleList.some((q) => !q.gate && rules[q.key]?.on);

  async function apply() {
    setBusy(true);
    setApplied(null);
    try {
      const got = await api.applyAudioGatePreset(adminPassword, uploadId, {
        preset: seeded, zone, arm, gates: proposalGates });
      setApplied({ ok: true, got });
    } catch (e) {
      setApplied({ ok: false, why: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (loadErr) {
    return (
      <div className="tiny err-text" style={{ marginTop: 10 }}>
        could not load the gate definitions: {loadErr}
      </div>
    );
  }
  if (!fields) {
    return <div className="tiny muted" style={{ marginTop: 10 }}>loading
      the gate definitions…</div>;
  }

  return (
    <div style={{ marginTop: 10, paddingTop: 8,
                  borderTop: "1px solid var(--line)" }}>
      <div className="small"><b>Gate sandbox</b>{" "}
        <span className="muted tiny">
          · score a tee box's gates against what you have labelled,
          then arm them for that tee box
        </span>
      </div>

      {/* ── START FROM A TEE BOX ──────────────────────────────── */}
      <div className="row" style={{ gap: 8, marginTop: 6,
                                    flexWrap: "wrap", alignItems: "center" }}>
        <span className="tiny muted">start from:</span>
        {Object.entries(presets || {}).map(([key, p]) => (
          <button key={key} type="button"
                  className={seeded === key ? "btn small" : "btn ghost small"}
                  title={p.why}
                  onClick={() => seed(key)}>
            {p.label}
          </button>
        ))}
        <button type="button" className="btn ghost small"
                onClick={() => {
                  setRules(Object.fromEntries(
                    ruleList.map((q) => [q.key, { on: false, value: "" }])));
                  setSeeded(null);
                }}>
          all off
        </button>
      </div>
      {seeded && presets?.[seeded]?.why && (
        <div className="tiny muted" style={{ marginTop: 3 }}>
          {presets[seeded].label}: {presets[seeded].why}
        </div>
      )}

      {/* ── EVERY GATE, ONE AT A TIME ─────────────────────────── */}
      <div style={{ display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fill, minmax(236px, 1fr))",
                    gap: "2px 14px", marginTop: 8 }}>
        {ruleList.map((q) => {
          const moved = rules[q.key]?.on
            ? clampedTo(q, rules[q.key]?.value) : null;
          return (
            <label key={q.key} className="tiny" title={q.help}
                   style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <input type="checkbox" checked={!!rules[q.key]?.on}
                     onChange={(e) => set(q.key, { on: e.target.checked })}
                     style={{ margin: 0 }} />
              <span style={{ flex: 1, opacity: rules[q.key]?.on ? 1 : 0.6 }}>
                {q.cmp === "max" ? "max " : "min "}{q.label}
                {!q.gate && (
                  <span className="muted" title="no gate exists for this
                    measurement, so it can be argued with here but not
                    armed"> ·&nbsp;bench</span>
                )}
                {/* THE CLAMP, SAID OUT LOUD. Stored as typed, enforced
                    at the edge of the range, and silent about it. */}
                {moved !== null && (
                  <span style={{ color: "var(--rose-700, #b4254b)" }}
                        title={`this gate runs from ${q.lo} to ${q.hi}; `
                               + `anything outside is moved to the edge `
                               + `without warning, so this bar would be `
                               + `enforced at ${moved}`}>
                    {" "}· runs as {moved}
                  </span>
                )}
              </span>
              <input type="text" inputMode="decimal"
                     value={rules[q.key]?.value ?? ""}
                     disabled={!rules[q.key]?.on}
                     step={q.step}
                     onChange={(e) => set(q.key, { value: e.target.value })}
                     style={{ width: 58, fontSize: 11, padding: "1px 4px",
                              borderColor: moved !== null
                                ? "var(--rose-700, #b4254b)" : undefined }} />
            </label>
          );
        })}
      </div>

      <label className="tiny" style={{ display: "inline-flex", gap: 5,
                                       alignItems: "center", marginTop: 6 }}
             title={"A quiet strike from a far tee is exactly where HF "
                    + "SNR or a decay comes back blank. Letting a gate "
                    + "reject what it could not measure selects against "
                    + "distance, so OFF is the default -- and it is what "
                    + "the armed gates do."}>
        <input type="checkbox" checked={blankFails}
               onChange={(e) => setBlankFails(e.target.checked)}
               style={{ margin: 0 }} />
        a value that could not be measured FAILS its gate
        {r.blanksDecided > 0 && (
          <span className="muted">
            {" "}· {r.blanksDecided} blank value
            {r.blanksDecided === 1 ? "" : "s"} decided this way
          </span>
        )}
      </label>

      {r.nLabelled === 0 && r.noPeak.length === 0 ? (
        <div className="tiny muted" style={{ marginTop: 8 }}>
          Nothing is labelled on this upload yet, so there is nothing to
          score a gate against. Classify a few peaks above first.
        </div>
      ) : (
        <>
          {/* ── THE TWO DECISIONS, SIDE BY SIDE ──────────────── */}
          <table className="tiny" style={{ marginTop: 10,
                                           borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th />
                <th style={{ textAlign: "right", padding: "0 10px" }}>
                  detector now
                </th>
                <th style={{ textAlign: "right", padding: "0 10px" }}>
                  your gates
                </th>
              </tr>
            </thead>
            <tbody>
              {[
                ["clips cut", r.now.cuts, r.proposed.cuts, null],
                ["of those, real strikes", r.now.strikes,
                 r.proposed.strikes, null],
                ["strikes retained (recall)", <Pct v={r.now.recall} />,
                 <Pct v={r.proposed.recall} />,
                 `of ${r.strikes} labelled strike`
                 + `${r.strikes === 1 ? "" : "s"}, including any that `
                 + "made no peak"],
                ["cuts that are strikes (precision)",
                 <Pct v={r.now.precision} />,
                 <Pct v={r.proposed.precision} />,
                 r.uncertain > 0
                   ? `${r.uncertain} "uncertain" left out of both`
                   : "uncertain labels are left out of both"],
              ].map(([k, a, b, why]) => (
                <tr key={k} style={{ borderTop: "1px solid var(--line)" }}>
                  <td className="muted" style={{ paddingRight: 10 }}>{k}</td>
                  <td style={{ textAlign: "right", padding: "1px 10px",
                               fontVariantNumeric: "tabular-nums" }}>{a}</td>
                  <td style={{ textAlign: "right", padding: "1px 10px",
                               fontWeight: 600,
                               fontVariantNumeric: "tabular-nums" }}>{b}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{why}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* ── AND WHERE EVERY LABELLED PEAK WENT ───────────── */}
          <table className="tiny" style={{ marginTop: 8,
                                           borderCollapse: "collapse" }}>
            <tbody>
              {BUCKETS.map(([key, label, good, why]) => {
                const n = r[key].length;
                return (
                  <tr key={key}>
                    <td className="muted" style={{ paddingRight: 12 }}>
                      {label}
                    </td>
                    <td style={{ fontWeight: 600, paddingRight: 12,
                                 fontVariantNumeric: "tabular-nums",
                                 color: n === 0 ? "inherit"
                                   : (good ? "var(--green-700, #197a4b)"
                                     : "var(--rose-700, #b4254b)") }}>
                      {n}
                    </td>
                    <td className="muted" style={{ fontSize: 11 }}>{why}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {!anyOn && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              No gate is switched on, so both columns are the detector on
              its own — the baseline every proposal has to beat.
            </div>
          )}
          {r.reasonsUnknown > 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {r.reasonsUnknown} refused peak
              {r.reasonsUnknown === 1 ? "" : "s"} came back without a
              recorded reason, so no gate is credited with recovering
              {r.reasonsUnknown === 1 ? " it" : " them"}.
            </div>
          )}
          {r.lost.length > 0 && (
            <div className="tiny" style={{ marginTop: 4,
                                           color: "var(--rose-700, #b4254b)" }}>
              <b>strikes your gates would throw away:</b>{" "}
              {r.lost.map(({ peak, failed }) => (
                <span key={peak.peak_time_sec} style={{ marginRight: 10 }}>
                  {peak.peak_time_sec.toFixed(2)}s (
                  {failed.map((f) => (f.read === null
                    ? `${f.label} not measured`
                    : `${f.label} ${f.read}`)).join(", ")})
                </span>
              ))}
            </div>
          )}

          <button type="button" className="btn ghost small"
                  style={{ marginTop: 6 }}
                  onClick={() => setShowRows((v) => !v)}>
            {showRows ? "hide" : "show"} every labelled peak
          </button>
          {showRows && (
            <table className="tiny" style={{ marginTop: 6,
                                             borderCollapse: "collapse" }}>
              <thead>
                <tr className="muted">
                  {["at", "labelled", "detector now", "your gates",
                    "which gates failed"].map((h) => (
                    <th key={h} style={{ textAlign: "left",
                                         padding: "0 10px 2px 0" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.rows.map((row) => (
                  <tr key={row.peak.peak_time_sec}
                      style={{ borderTop: "1px solid var(--line)" }}>
                    <td style={{ paddingRight: 10,
                                 fontVariantNumeric: "tabular-nums" }}>
                      {row.peak.peak_time_sec.toFixed(2)}s
                    </td>
                    <td style={{ paddingRight: 10 }}>{row.label}</td>
                    <td style={{ paddingRight: 10 }}>
                      {row.cut ? "cut" : `refused (${row.why})`}
                    </td>
                    <td style={{ paddingRight: 10, fontWeight: 600 }}>
                      {row.bucket === "recovered" ? "recovered"
                        : row.bucket === "blockedUpstream" ? "out of reach"
                          : row.pass ? "cut" : "refused"}
                    </td>
                    <td className="muted">
                      {row.failed.length === 0 ? "—"
                        : row.failed.map((f) => (f.read === null
                          ? `${f.label} (not measured)`
                          : `${f.label} ${f.read} vs ${f.want}`)).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* ── ARM IT, FOR ONE TEE BOX ───────────────────────────── */}
      <div style={{ marginTop: 10, paddingTop: 8,
                    borderTop: "1px dashed var(--line)" }}>
        <div className="row" style={{ gap: 8, flexWrap: "wrap",
                                      alignItems: "center" }}>
          <span className="tiny muted">write these to:</span>
          <select value={zone} onChange={(e) => setZone(e.target.value)}
                  style={{ fontSize: 11 }}>
            <option value="">the whole hole</option>
            {(zoneOptions || []).map(([idx, label]) => (
              <option key={idx} value={idx}>{zoneName(idx, label)}</option>
            ))}
          </select>
          <label className="tiny" style={{ display: "inline-flex", gap: 5,
                                           alignItems: "center" }}
                 title={"Off: the gates are worked out on every cut and "
                        + "reported, and nothing is refused by them. On: "
                        + "they decide. Either way it is a stored value, "
                        + "not a deploy."}>
            <input type="checkbox" checked={arm}
                   onChange={(e) => setArm(e.target.checked)}
                   style={{ margin: 0 }} />
            and act on them
          </label>
          <button type="button" className="btn small"
                  disabled={busy}
                  title={gatesOn.length
                    ? "writes exactly the gates shown above"
                    : "every gate is off — this writes them all off, "
                      + "which is how a tee box is switched back"}
                  onClick={apply}>
            {busy ? "writing…" : (gatesOn.length
              ? `write ${gatesOn.length} gate${gatesOn.length === 1
                ? "" : "s"}`
              : "write all gates off")}
          </button>
        </div>
        <div className="tiny muted" style={{ marginTop: 4 }}>
          This writes the gates exactly as they stand above onto that tee
          box — a preset is only where you start. A gate you switched off
          is written off, so switching one off and writing turns it off.
          {extrasOn && (
            <> Gates marked <b>bench</b> have nothing behind them to arm,
              so they score here and are not written.</>
          )}
        </div>
        {gatesOn.length > 0 && (
          <div className="tiny muted" style={{ marginTop: 2 }}>
            will be written:{" "}
            {gatesOn.map(([g, v]) => `${g}=${v}`).join(", ")}
          </div>
        )}
        {applied && (
          <div className={`tiny ${applied.ok ? "" : "err-text"}`}
               style={{ marginTop: 4 }}>
            {applied.ok
              ? `written${arm ? " and armed" : " — reporting only, not acting"}`
              + `: ${JSON.stringify(applied.got?.wrote || applied.got)}`
              : applied.why}
          </div>
        )}
      </div>
    </div>
  );
}
