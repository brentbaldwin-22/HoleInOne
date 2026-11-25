import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Audio1Thresholds from "./Audio1Thresholds.jsx";

/*
 * audio1 — every number taken about every audio peak, and a place to
 * say what each one actually was.
 *
 * THIS SCREEN DECIDES NOTHING. The detector found these peaks on its
 * own thresholds and cut whatever it cut; everything here is written
 * beside that. The confidence score in particular is diagnostic: it
 * has no path to the cutter and must not get one until the labelled
 * set says whether it is worth anything.
 *
 * THE REFUSED PEAKS MATTER AS MUCH AS THE KEPT ONES. A false positive
 * shows up among the clips we cut; a false negative only ever shows
 * up among the peaks we threw away, or among the strikes that made no
 * peak at all — which is why "mark a missed strike" is here and not
 * an afterthought.
 *
 * AN EMPTY CELL MEANS NOT MEASURED. Never 0. A band above the
 * recording's Nyquist limit, a decay that never completed, an attack
 * with no rise to time: each is blank, and the row's own notes say
 * which and why.
 */

const LABELS = [
  ["strike", "True golf ball strike"],
  ["ground", "Club hitting ground or mat"],
  ["wind", "Wind"],
  ["voice", "Voice"],
  ["other", "Other noise"],
  ["uncertain", "Uncertain"],
];

const LABEL_SHORT = Object.fromEntries(
  LABELS.map(([k, v]) => [k, v.replace(/^(True|Club hitting) /, "")]));

// ── THE COLUMNS, GROUPED SO THEY ALL FIT ────────────────────────────
//
// TWENTY-SIX NUMBERS, ONE PER COLUMN, DID NOT FIT ON A SCREEN. The
// table scrolled sideways and the operator was reading five of them
// at a time, which defeats the point of a table: the comparison is
// ACROSS peaks, and a column you have to scroll to is a column you
// cannot compare.
//
// So related numbers share a cell. Nothing is dropped and nothing
// moves into the expander — every measurement is still on the row,
// just packed the way it is read: the two attacks together because
// they are two answers to one question, the four band percentages
// together because they sum to a hundred, the three concentration
// ratios together because their SHAPE is the finding and no single
// one of them is.
//
// A MISSING VALUE IS A DOT, not a gap. In a shared cell an empty
// string slides the next number into its place and the row silently
// starts reading as a different measurement.
const MISS = "·";

const GROUPS = [
  // WHERE THE ROW CAME FROM, never left to be inferred. "cut" is the
  // detector's own; "raw" it found and refused; "hf" the independent
  // high-frequency search turned up; "manual" a person typed the time
  // and the measurement was centred by hand. They are four different
  // strengths of evidence and must not read alike.
  { key: "t", head: "", subs: ["time", "found by"], w: 104,
    get: (p) => [p.peak_time_sec?.toFixed(2),
                 p.source === "cut" ? "cut"
                   : p.source === "manual" ? "manual"
                   : p.source === "hf" ? "HF"
                   : p.passed_gate ? "cut" : "refused"] },
  // TWO RATIOS, AND THEY ARE NOT THE SAME MEASUREMENT.
  //
  //   det¹  the DETECTOR's: peak envelope over the MEDIAN OF THE
  //         WHOLE CLIP, on 22050 Hz audio high-passed at 1500 Hz with
  //         a 10 ms envelope. This is the one `min peak ratio`
  //         enforces -- it is the argument the detector is given.
  //   a1    audio1's: peak envelope over a LOCAL floor, the median of
  //         the 500 ms ending 50 ms before the peak, on the camera's
  //         own rate, unfiltered, with a 2 ms envelope.
  //
  // They disagree for four separate reasons at once, so one cannot be
  // read as a correction of the other: different band, different
  // sample rate, different envelope length, and -- the big one -- a
  // whole-clip denominator against a local one. On a clip that is
  // quiet apart from a few strikes the whole-clip median is the
  // ambience and both are similar; on a clip with a running mower the
  // local floor rises next to the mower and the clip median does not.
  { key: "pk", head: "peak", subs: ["amp", "det¹", "a1"], w: 96,
    get: (p) => [fx(p.peak_amplitude, 3), fx(p.detector_ratio, 1),
                 fx(p.peak_ratio, 1)] },
  // det¹ IS BLANK WHEN THE DETECTOR COULD NOT MEASURE ONE. It returns
  // a sentinel — (lookback+1)/22050, three times the attack gate —
  // and printing it made a whole clip read as though every candidate
  // rose in the same 53.9 ms. Expand the row for the reason.
  { key: "atk", head: "attack ms", subs: ["det¹", "10-90"], w: 70,
    get: (p) => [p.detector_attack_measured === false
                   ? undefined : fx(p.detector_attack_ms, 1),
                 fx(p.attack_ms, 1)] },
  // A DAGGER MEANS THE DECAY IS ABOUT SOMETHING ELSE — a later sound,
  // or a floor sitting inside the ambience. Expand the row for which.
  { key: "dec", head: "decay ms", subs: ["decay", "trans"], w: 76,
    get: (p) => {
      const d = fx(p.decay_ms, 0);
      const clean = (p.decay_end_reason || "").startsWith("fell and stayed");
      return [d === undefined ? undefined : (clean ? d : d + "†"),
              fx(p.transient_duration_ms, 0)];
    } },
  { key: "bands", head: "band %", subs: ["lo", "mid", "imp", "shrp"],
    w: 104,
    get: (p) => [fx(band(p, "low"), 0), fx(band(p, "mid"), 0),
                 fx(band(p, "impact"), 0), fx(band(p, "sharp"), 0)] },
  { key: "above", head: "above %", subs: ["1.5k", "3k"], w: 62,
    get: (p) => [fx(p.pct_above?.pct_above_1500, 0),
                 fx(p.pct_above?.pct_above_3000, 0)] },
  { key: "snr", head: "SNR dB", subs: ["all", "HF"], w: 66,
    get: (p) => [fx(p.snr_db, 1), fx(p.hf_snr_db, 1)] },
  { key: "sp", head: "spectrum", subs: ["cent", "flux", "crest"],
    w: 100,
    get: (p) => [fx(p.spectral_centroid_hz, 0), fx(p.spectral_flux, 1),
                 fx(p.crest_factor, 1)] },
  { key: "aft", head: "after", subs: ["2nd", "iso"], w: 62,
    get: (p) => [p.n_secondary_peaks ?? undefined, fx(p.isolation_db, 1)] },
  // A RATIO OF 1 IS NOT A CONCENTRATION READING — it means every ring
  // past the inner window was at or under the background, so the
  // sound was over before the outer window opened. Marked with ‡ so
  // it is not read as "98% of the energy is in the middle".
  { key: "conc", head: "concentration", subs: ["10/100", "20/200", "50/200"],
    w: 96,
    get: (p) => {
      const sat = new Set(p.concentration_saturated || []);
      return [["e10_over_e100", 2], ["e20_over_e200", 2],
              ["e50_over_e200", 2]].map(([k, dp]) => {
        const v = fx(p.concentration?.[k], dp);
        return v === undefined ? undefined : (sat.has(k) ? v + "‡" : v);
      });
    } },
  { key: "conch", head: "…above 1.5 kHz",
    subs: ["10/100", "20/200", "50/200"], w: 96,
    get: (p) => [fx(p.concentration_hf?.e10_over_e100, 2),
                 fx(p.concentration_hf?.e20_over_e200, 2),
                 fx(p.concentration_hf?.e50_over_e200, 2)] },
  // THE SHAPE IS THE POINT. A strike empties out — measured at
  // 1 · 0.57 · 0.21 · 0.03. A voice holds up or climbs: 1 · 1.36 ·
  // 1.45 · 0.27. A number that comes back up late is usually a
  // second sound rather than this one still ringing.
  { key: "post", head: "after the peak",
    subs: ["0-20", "-50", "-100", "-200"], w: 110,
    get: (p) => ["ms0_20", "ms20_50", "ms50_100", "ms100_200"]
      .map((k) => fx(p.post_shape?.[k], 2)) },
  { key: "sc", head: "score", subs: [""], w: 46,
    get: (p) => [fx(p.confidence?.score, 0)] },
];

function band(p, name) {
  return (p.bands || []).find((b) => b.name === name)?.pct;
}

/* A number, or nothing at all. NEVER a zero standing in for one. */
function fx(v, dp) {
  if (v === null || v === undefined || Number.isNaN(v)) return undefined;
  return Number(v).toFixed(dp);
}

const BAND_COLOUR = {
  high: "var(--emerald-700, #0b8a4a)",
  medium: "var(--amber-700, #a16207)",
  low: "var(--muted, #888)",
};

export default function Audio1Panel({ uploadId, data, adminPassword,
                                      zoneOptions, gateReport }) {
  const [labels, setLabels] = useState({});
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [missAt, setMissAt] = useState("");
  const [missLabel, setMissLabel] = useState("strike");
  const [note, setNote] = useState({});

  const refresh = useCallback(async () => {
    if (!adminPassword || !uploadId) return;
    try {
      const rows = await api.listAudioLabels(adminPassword, uploadId);
      const by = {};
      rows.forEach((r) => { by[r.peak_time_sec.toFixed(2)] = r; });
      setLabels(by);
    } catch (e) {
      setErr(e.message);
    }
  }, [adminPassword, uploadId]);

  useEffect(() => { refresh(); }, [refresh]);

  const peaks = useMemo(() => data?.peaks || [], [data]);

  // WHAT THE GATES DID, OR WOULD DO, ROW BY ROW.
  //
  // "8 cut" over a table of eight rows, next to four gates that are
  // set and not armed, left the operator to work out for themselves
  // which rows the arming would take -- by reading a crest column
  // against a bar in another panel. The gate pass already judged
  // every one of them; this is it, joined back onto the row it
  // judged.
  const verdicts = useMemo(() => {
    const by = {};
    (gateReport?.rejected || []).forEach((r) => {
      by[r.peak_time_sec.toFixed(2)] = r;
    });
    return by;
  }, [gateReport]);
  const armed = !!gateReport?.armed;
  const wouldDrop = !armed && (gateReport?.rejected || []).length;

  async function classify(peak, label) {
    const key = peak.peak_time_sec.toFixed(2);
    setBusy(key); setErr(null);
    try {
      await api.saveAudioLabel(adminPassword, uploadId, {
        peakTimeSec: peak.peak_time_sec,
        label,
        kind: "detected",
        note: note[key] || "",
        // The whole measurement dict, plus the probe, so the frozen
        // copy says what recording it was taken from.
        measurements: { ...peak, probe: data?.probe },
      });
      await refresh();
    } catch (e) { setErr(e.message); } finally { setBusy(null); }
  }

  async function addMissed() {
    const t = parseFloat(missAt);
    if (!Number.isFinite(t) || t < 0) {
      setErr("Give the time in seconds, e.g. 28.4");
      return;
    }
    setBusy("missed"); setErr(null);
    try {
      // MEASURE IT, don't just record the time. A missed strike with
      // no numbers is the finding but not a comparable one — it can
      // never be put beside the true positives it ought to resemble.
      // The measurement is centred by hand and says so; if it cannot
      // be taken, the label still goes in, because the person
      // listening is the scarce part.
      let meas = null;
      let snapped = null;
      try {
        const r = await api.audioMeasureAt(adminPassword, uploadId, t);
        if (r?.ok && r.measurement) {
          meas = { ...r.measurement, probe: r.probe };
          snapped = r.measurement.snapped_by_ms;
        }
      } catch (e) {
        console.warn("could not measure the missed strike", e);
      }
      await api.saveAudioLabel(adminPassword, uploadId, {
        peakTimeSec: meas?.peak_time_sec ?? t, label: missLabel,
        kind: "missed",
        note: [note.missed || "",
               snapped != null && Math.abs(snapped) >= 1
                 ? `(typed ${t.toFixed(2)}s, measured ${snapped > 0
                     ? "+" : ""}${snapped}ms away)` : ""]
          .filter(Boolean).join(" "),
        measurements: meas,
      });
      setMissAt(""); setNote((n) => ({ ...n, missed: "" }));
      await refresh();
    } catch (e) { setErr(e.message); } finally { setBusy(null); }
  }

  async function remove(id) {
    setBusy("del"); setErr(null);
    try {
      await api.deleteAudioLabel(adminPassword, uploadId, id);
      await refresh();
    } catch (e) { setErr(e.message); } finally { setBusy(null); }
  }

  if (!data) return null;
  if (!data.ok) {
    return (
      <div className="card tight" style={{ margin: "10px 0 0" }}>
        <b className="small">audio 1</b>{" "}
        <span className="tiny err-text">
          could not measure this clip — {data.reason || "no reason given"}
        </span>
      </div>
    );
  }

  const pr = data.probe || {};
  const missed = Object.values(labels).filter((l) => l.kind === "missed");

  return (
    <div className="card tight" style={{ margin: "10px 0 0",
                                         background: "var(--surface-alt)" }}>
      <div className="small" style={{ marginBottom: 4 }}>
        <b>audio 1 — measurements</b>{" "}
        <span className="muted tiny">
          · {data.n_cut ?? 0} cut · {data.n_refused ?? 0} refused ·{" "}
          {/* THIS USED TO SAY "these numbers decide nothing". It was
              true when audio1 was a measurement pass and became false
              the day the gates were armed -- and a caption telling an
              operator that the numbers in front of them cannot have
              refused anything, on a screen where they just did, is
              worse than no caption. What decides is whether this tee
              box's gates are armed, which the line below says. */}
          measured on every peak the detector saw, kept or not
        </span>
      </div>

      {/* THE ARITHMETIC THE OPERATOR WAS DOING BY HAND. "8 cut" over
          a table of eight rows, beside four gates that are set and
          not armed, says nothing about what arming would leave. */}
      {gateReport?.ran && wouldDrop > 0 && (
        <div className="small" style={{ marginBottom: 6, padding: "4px 8px",
                                        borderRadius: 4,
                                        background: "var(--warn-bg, #fdf3d7)" }}>
          <b>These gates are not armed.</b> Armed, they would leave{" "}
          <b>{(gateReport.n_in || 0) - wouldDrop} of {gateReport.n_in}</b>
          {" "}cuts — the {wouldDrop} marked below would go. Arm them in
          the gates panel above.
        </div>
      )}
      {gateReport?.ran && armed && (
        <div className="tiny" style={{ marginBottom: 6 }}>
          <span className="pill ok small">armed</span>{" "}
          these gates decided this run: {gateReport.kept} of{" "}
          {gateReport.n_in} peaks kept.
        </div>
      )}

      {/* THE RECORDING ITSELF, because every band below depends on it.
          A camera at 16 kHz cannot carry 3-8 kHz and a table that
          printed 0 for it would be lying with a number. */}
      <div className="tiny muted" style={{ marginBottom: 6 }}>
        {pr.sample_rate
          ? <>recorded at <b>{pr.sample_rate} Hz</b>
              {pr.channels ? ` · ${pr.channels}ch` : ""}
              {pr.codec ? ` · ${pr.codec}` : ""}
              {" "}· Nyquist <b>{Math.round(pr.nyquist_hz)} Hz</b></>
          : <>sample rate unknown</>}
        {(data.bands || []).some((b) => !b.supported || b.clipped) && (
          <>
            {" · "}
            {(data.bands || []).filter((b) => !b.supported || b.clipped)
              .map((b) => (
                <span key={b.name} className="err-text">
                  {b.name} {b.supported ? "clipped to" : "unavailable above"}{" "}
                  {Math.round(pr.nyquist_hz)} Hz{" "}
                </span>
              ))}
          </>
        )}
      </div>

      {err && <div className="tiny err-text" style={{ marginBottom: 6 }}>
        {err}
      </div>}

      {/* NO overflowX HERE ANY MORE. A table you scroll sideways is
          a table you cannot compare across, which is the only reason
          to have one. `table-layout: fixed` with a width per group
          stops one long number widening a column and pushing the rest
          off the edge. */}
      <div>
        <table className="tiny"
               style={{ borderCollapse: "collapse", whiteSpace: "nowrap",
                        tableLayout: "fixed", width: "100%",
                        fontSize: 11 }}>
          <colgroup>
            <col style={{ width: 26 }} />
            {GROUPS.map((g) => (
              <col key={g.key} style={{ width: g.w }} />
            ))}
            <col style={{ width: 96 }} />
          </colgroup>
          <thead>
            {/* TWO HEADER ROWS, so a group can be named once and its
                parts named under it. One row of headers for grouped
                cells either repeats the group name three times or
                omits it, and both read as a mistake. */}
            <tr className="muted">
              <th />
              <th style={{ textAlign: "left", fontSize: 10 }}>gates</th>
              {GROUPS.map((g) => (
                <th key={g.key}
                    style={{ textAlign: "left", paddingRight: 8,
                             fontWeight: 600, fontSize: 10,
                             letterSpacing: "0.02em",
                             whiteSpace: "normal", lineHeight: 1.25 }}>
                  {g.head}
                </th>
              ))}
              <th style={{ textAlign: "left", fontSize: 10 }}>label</th>
            </tr>
            <tr className="muted">
              <th />
              <th />
              {GROUPS.map((g) => (
                <th key={g.key}
                    style={{ textAlign: "left", paddingRight: 8,
                             fontWeight: 400, fontSize: 10, opacity: 0.75,
                             paddingBottom: 2,
                             // WRAP, DO NOT RUN OVER. These inherited
                             // `nowrap` from the table and overflowed
                             // their cells, so the header read
                             // "DECAY TRANSO MID 1.5-3K" — two
                             // columns' labels printed on top of each
                             // other. A header that cannot be trusted
                             // is worse than none: it names the wrong
                             // number.
                             whiteSpace: "normal", lineHeight: 1.25,
                             wordBreak: "break-word" }}>
                  {g.subs.join(" ")}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {peaks.map((p) => {
              const key = p.peak_time_sec.toFixed(2);
              const lab = labels[key];
              const isOpen = open === key;
              return (
                <tr key={key}
                    style={{ opacity: p.passed_gate ? 1 : 0.72,
                             borderTop: "1px solid var(--line)" }}>
                  <td>
                    <button type="button" className="secondary small"
                            style={{ padding: "0 4px", fontSize: 10 }}
                            onClick={() => setOpen(isOpen ? null : key)}>
                      {isOpen ? "−" : "+"}
                    </button>
                  </td>
                  {/* THE GATES' VERDICT ON THIS ROW, beside the row.
                      Unarmed it is the only place the operator can
                      see what arming would cost them. */}
                  <td style={{ whiteSpace: "nowrap", paddingRight: 6 }}>
                    {verdicts[key] ? (
                      <span
                        title={(verdicts[key].failed || []).map(
                          (f) => `${f.of} ${f.read} `
                            + `${f.cmp === "min" ? "<" : ">"} ${f.bar}`)
                          .join(" · ")}
                        style={{ color: "var(--rose-700, #b4254b)",
                                 fontSize: 10 }}>
                        {armed ? "dropped" : "would drop"}
                        {" "}
                        {(verdicts[key].failed || [])
                          .map((f) => f.of.replace(/ \(.*\)$/, ""))
                          .slice(0, 2).join(", ")}
                      </span>
                    ) : p.passed_gate && gateReport?.ran ? (
                      <span style={{ color: "var(--green-700, #197a4b)",
                                     fontSize: 10 }}>passes</span>
                    ) : null}
                  </td>
                  {GROUPS.map((g) => {
                    const vals = g.get(p) || [];
                    const shown = vals.map(
                      (v) => (v === undefined || v === null ? MISS
                        : String(v)));
                    const blank = shown.every((v) => v === MISS);
                    return (
                      <td key={g.key}
                          style={{
                            paddingRight: 8,
                            fontVariantNumeric: "tabular-nums",
                            overflow: "hidden", textOverflow: "ellipsis",
                            color: g.key === "sc" && p.confidence?.band
                              ? BAND_COLOUR[p.confidence.band]
                              : (blank ? "var(--muted, #999)" : undefined),
                            fontWeight: g.key === "sc" ? 600 : undefined,
                          }}
                          title={g.subs.map((sub, i) => `${sub} ${shown[i]}`)
                            .join(" · ")}>
                        {shown.join(" ")}
                      </td>
                    );
                  })}
                  <td>
                    <select
                      value={lab?.kind === "detected" ? lab.label : ""}
                      disabled={busy === key}
                      onChange={(e) => e.target.value
                        && classify(p, e.target.value)}
                      style={{ fontSize: 10, padding: 0, width: "100%" }}
                    >
                      <option value="">—</option>
                      {LABELS.map(([v, t]) => (
                        <option key={v} value={v}>{t}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
            {peaks.length === 0 && (
              <tr><td colSpan={GROUPS.length + 2} className="muted tiny">
                No peaks at all — not even below the gate. Nothing to
                measure.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* THE EXPANDED PEAK: its picture, the components of its score,
          and everything the row had no column for. */}
      {peaks.filter((p) => open === p.peak_time_sec.toFixed(2)).map((p) => {
        const key = p.peak_time_sec.toFixed(2);
        return (
          <div key={key} className="card tight"
               style={{ margin: "8px 0 0" }}>
            <div className="small"><b>{key}s</b>{" "}
              <span className="muted tiny">
                {p.passed_gate ? "cut by the detector" : "found and refused"}
                {" · "}{p.source}
              </span>
            </div>
            {p.image_url && (
              <figure style={{ margin: "6px 0 0" }}>
                <img src={p.image_url} alt={`audio around ${key}s`}
                     style={{ width: "100%", maxWidth: 900,
                              borderRadius: 6 }} />
                <figcaption className="tiny muted">
                  waveform over spectrogram, 500 ms either side of the
                  peak · the blue line is the peak · grey lines are the
                  band edges
                </figcaption>
              </figure>
            )}
            <div className="tiny" style={{ marginTop: 6 }}>
              <b>score {p.confidence?.score ?? "—"}</b>
              {p.confidence?.band ? ` (${p.confidence.band})` : ""}
              {" — "}
              {Object.entries(p.confidence?.components || {})
                .map(([k, v]) => `${k} ${v === null ? "—" : v}`)
                .join(" · ")}
            </div>
            {p.confidence?.why && (
              <div className="tiny muted">{p.confidence.why}</div>
            )}
            {p.decay_end_reason && (
              <div className="tiny" style={{ marginTop: 4 }}>
                <b>decay:</b>{" "}
                <span className={p.decay_end_reason.startsWith("fell and")
                                 ? "muted" : "err-text"}>
                  {p.decay_end_reason}
                </span>
                {p.decay_first_cross_ms != null && (
                  <span className="muted">
                    {" "}· first crossing {p.decay_first_cross_ms} ms ·{" "}
                    longest quiet {p.decay_longest_quiet_ms} ms ·{" "}
                    {p.decay_crossings} crossing(s), {p.decay_resets} of
                    them long enough to be a sound
                  </span>
                )}
              </div>
            )}
            <div className="tiny muted" style={{ marginTop: 4 }}>
              <b>energy, background-adjusted and integrated</b> — full:{" "}
              {["w10", "w20", "w50", "w100", "w200"].map((k) => (
                <span key={k} style={{ marginRight: 7 }}>
                  {k.slice(1)}ms{" "}
                  {p.energy?.[k] == null
                    ? "—" : Number(p.energy[k]).toExponential(2)}
                </span>
              ))}
              {" | "}above 1.5 kHz:{" "}
              {["w10", "w20", "w50", "w100", "w200"].map((k) => (
                <span key={k} style={{ marginRight: 7 }}>
                  {k.slice(1)}ms{" "}
                  {p.energy_hf?.[k] == null
                    ? "—" : Number(p.energy_hf[k]).toExponential(2)}
                </span>
              ))}
            </div>
            <div className="tiny muted">
              <b>after the peak</b>{" "}
              {["ms0_20", "ms20_50", "ms50_100", "ms100_200"].map((k) => (
                <span key={k} style={{ marginRight: 8 }}>
                  {k.replace("ms", "").replace("_", "–")}ms{" "}
                  {p.post?.[k] == null
                    ? "—" : Number(p.post[k]).toExponential(2)}
                  {p.post_hf?.[k] != null
                    && ` (hf ${Number(p.post_hf[k]).toExponential(2)})`}
                </span>
              ))}
            </div>
            {(p.notes || []).length > 0 && (
              <ul className="tiny muted" style={{ margin: "4px 0 0",
                                                  paddingLeft: 16 }}>
                {p.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}
            <div className="tiny" style={{ marginTop: 6 }}>
              <input
                type="text"
                placeholder="note (optional) — what you heard"
                value={note[key] || ""}
                onChange={(e) => setNote((n) => ({ ...n,
                                                   [key]: e.target.value }))}
                style={{ width: 320, fontSize: 12, padding: "2px 5px" }}
              />
              {labels[key] && (
                <>
                  {" "}
                  <span className="muted">
                    labelled <b>{LABEL_SHORT[labels[key].label]
                                 || labels[key].label}</b>
                  </span>{" "}
                  <button type="button" className="secondary small"
                          disabled={busy === "del"}
                          onClick={() => remove(labels[key].id)}>
                    clear
                  </button>
                </>
              )}
            </div>
          </div>
        );
      })}

      {/* ── A STRIKE THAT MADE NO PEAK ──────────────────────────────
          The only way a false negative is ever recorded. Without it
          this screen can only ever learn about sounds we already
          catch, which is the half of the problem that is not the
          problem. */}
      <div className="tiny" style={{ marginTop: 10,
                                     paddingTop: 8,
                                     borderTop: "1px solid var(--line)" }}>
        <b>Missed a strike?</b>{" "}
        <span className="muted">
          If you can hear one that is not in the table, put its time in —
          there is nothing to measure, and that IS the finding.
        </span>
        <div style={{ marginTop: 4, display: "flex", gap: 6,
                      alignItems: "center", flexWrap: "wrap" }}>
          <input type="text" inputMode="decimal" placeholder="seconds"
                 value={missAt}
                 onChange={(e) => setMissAt(e.target.value)}
                 style={{ width: 90, fontSize: 12, padding: "2px 5px" }} />
          <select value={missLabel}
                  onChange={(e) => setMissLabel(e.target.value)}
                  style={{ fontSize: 12 }}>
            {LABELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
          <input type="text" placeholder="note (optional)"
                 value={note.missed || ""}
                 onChange={(e) => setNote((n) => ({ ...n,
                                                    missed: e.target.value }))}
                 style={{ width: 240, fontSize: 12, padding: "2px 5px" }} />
          <button type="button" className="secondary small"
                  disabled={busy === "missed"} onClick={addMissed}>
            {busy === "missed" ? "saving…" : "mark missed"}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 3 }}>
          The audio around that time is measured too, centred on the
          loudest point within 150 ms of it and marked <b>manual</b> —
          so a strike the detector never found can still be compared
          with the ones it did.
        </div>
        {missed.length > 0 && (
          <div className="muted" style={{ marginTop: 4 }}>
            missed so far:{" "}
            {missed.map((m) => (
              <span key={m.id} style={{ marginRight: 8 }}>
                {m.peak_time_sec.toFixed(2)}s ({LABEL_SHORT[m.label]
                                                || m.label}){" "}
                <button type="button" className="link small"
                        style={{ padding: 0 }}
                        onClick={() => remove(m.id)}>×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      <Audio1Thresholds peaks={peaks} labels={labels}
                        uploadId={uploadId} adminPassword={adminPassword}
                        zoneOptions={zoneOptions} />

      {/* HOW EVERY NUMBER ABOVE IS ARRIVED AT, served from the code
          that takes it rather than written out here — a description
          kept somewhere else drifts from the thing it describes, and
          a wrong description of a measurement gets believed. */}
      {(data.how_measured || []).length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="tiny" style={{ cursor: "pointer" }}>
            how these are measured
          </summary>
          <div className="tiny muted" style={{ marginTop: 6 }}>
            Not all of these are taken off the same signal. The{" "}
            <b>original attack</b> is measured on the detector's audio,
            resampled to 22050 Hz and high-passed at 1500 Hz before its
            envelope is built; everything audio1 adds is measured at the
            camera's own rate, unfiltered. Two attack figures in one row
            are two measurements of two different signals.
          </div>
          {data.how_measured.map((h) => (
            <div key={h.name} style={{ marginTop: 8 }}>
              <div className="tiny"><b>{h.name}</b>{" "}
                {h.where && <code className="muted">{h.where}</code>}
              </div>
              {["signal", "envelope", "how", "background", "cannot",
                "note"].map((k) => (h[k] ? (
                  <div key={k} className="tiny muted"
                       style={{ paddingLeft: 10 }}>
                    <i>{k === "cannot" ? "when it cannot be measured"
                        : k}:</i> {h[k]}
                  </div>
                ) : null))}
            </div>
          ))}
        </details>
      )}

      <div className="tiny" style={{ marginTop: 8 }}>
        <a href={api.audioLabelsCsvUrl(0)} download>
          export every label as CSV
        </a>
        {" · "}
        <a href={api.audioLabelsCsvUrl(uploadId)} download>
          just this upload
        </a>
        <span className="muted">
          {" "}· the measurements are frozen into each label when you save
          it, so re-running with different knobs cannot move them
        </span>
      </div>
    </div>
  );
}
