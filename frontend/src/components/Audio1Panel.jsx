import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

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

// THE COLUMNS, AND THE ORDER THE REQUEST ASKED FOR THEM IN. `get`
// returns undefined for anything unmeasured so the cell stays empty.
const COLS = [
  ["t", "time", (p) => p.peak_time_sec?.toFixed(2)],
  ["cut", "cut", (p) => (p.passed_gate ? "yes" : "no")],
  ["amp", "amp", (p) => fx(p.peak_amplitude, 3)],
  ["ratio", "ratio", (p) => fx(p.peak_ratio, 1)],
  ["atk0", "attack¹", (p) => fx(p.detector_attack_ms, 1)],
  ["atk", "attack", (p) => fx(p.attack_ms, 1)],
  ["dec", "decay", (p) => fx(p.decay_ms, 0)],
  ["dur", "transient", (p) => fx(p.transient_duration_ms, 0)],
  ["low", "low%", (p) => fx(band(p, "low"), 0)],
  ["mid", "mid%", (p) => fx(band(p, "mid"), 0)],
  ["hi", ">1.5k%", (p) => fx(p.pct_above?.pct_above_1500, 0)],
  ["hi3", ">3k%", (p) => fx(p.pct_above?.pct_above_3000, 0)],
  ["hfsnr", "HF SNR", (p) => fx(p.hf_snr_db, 1)],
  ["snr", "SNR", (p) => fx(p.snr_db, 1)],
  ["cen", "centroid", (p) => fx(p.spectral_centroid_hz, 0)],
  ["flux", "flux", (p) => fx(p.spectral_flux, 1)],
  ["crest", "crest", (p) => fx(p.crest_factor, 1)],
  ["sec", "2nd", (p) => (p.n_secondary_peaks ?? undefined)],
  ["iso", "isolation", (p) => fx(p.isolation_db, 1)],
  ["bg", "background", (p) => fx(p.background_level, 4)],
  ["clip", "clipped", (p) => (p.clipped ? "YES" : "no")],
  ["conf", "score", (p) => fx(p.confidence?.score, 0)],
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

export default function Audio1Panel({ uploadId, data, adminPassword }) {
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
      await api.saveAudioLabel(adminPassword, uploadId, {
        peakTimeSec: t, label: missLabel, kind: "missed",
        note: note.missed || "",
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
          these numbers decide nothing: the clips above are whatever the
          audio detector cut on its own thresholds
        </span>
      </div>

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

      <div style={{ overflowX: "auto" }}>
        <table className="tiny" style={{ borderCollapse: "collapse",
                                         whiteSpace: "nowrap" }}>
          <thead>
            <tr className="muted">
              <th style={{ textAlign: "left", paddingRight: 8 }}></th>
              {COLS.map(([k, h]) => (
                <th key={k} style={{ textAlign: "left", paddingRight: 10 }}>
                  {h}
                </th>
              ))}
              <th style={{ textAlign: "left", paddingRight: 10 }}>label</th>
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
                  <td style={{ paddingRight: 8 }}>
                    <button type="button" className="secondary small"
                            style={{ padding: "0 5px", fontSize: 11 }}
                            onClick={() => setOpen(isOpen ? null : key)}>
                      {isOpen ? "−" : "+"}
                    </button>
                  </td>
                  {COLS.map(([k, , get]) => {
                    const v = get(p);
                    return (
                      <td key={k}
                          style={{
                            paddingRight: 10,
                            fontVariantNumeric: "tabular-nums",
                            color: k === "conf" && p.confidence?.band
                              ? BAND_COLOUR[p.confidence.band] : undefined,
                            fontWeight: k === "conf" ? 600 : undefined,
                          }}>
                        {v === undefined ? "" : v}
                      </td>
                    );
                  })}
                  <td style={{ paddingRight: 10 }}>
                    <select
                      value={lab?.kind === "detected" ? lab.label : ""}
                      disabled={busy === key}
                      onChange={(e) => e.target.value
                        && classify(p, e.target.value)}
                      style={{ fontSize: 11, padding: "0 2px" }}
                    >
                      <option value="">— unlabelled —</option>
                      {LABELS.map(([v, t]) => (
                        <option key={v} value={v}>{t}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
            {peaks.length === 0 && (
              <tr><td colSpan={COLS.length + 2} className="muted tiny">
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
