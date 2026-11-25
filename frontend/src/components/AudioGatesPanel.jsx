import { useCallback, useEffect, useState } from "react";

import { api } from "../api.js";

/*
 * The gates that will cut THIS recording, before it is cut.
 *
 * WHY IT IS NOT THE HOLE'S GATE EDITOR. That one answers "what has
 * this hole stored", which is a list containing both
 * "audio_min_ratio" and "audio_min_ratio@1" with nothing saying which
 * of them is going to cut the clip in front of you. The zone that
 * fired the recording decides, and working that out by eye is how a
 * cut gets verified at a ratio the cutter would never run at. The
 * server resolves it with the same resolver the cutter uses and says
 * where each value came from.
 *
 * TWO KINDS OF GATE, AND THEY DO NOT SWITCH OFF THE SAME WAY.
 * The detector's own three -- ratio, attack, separation -- have no
 * off: the detector cannot run without them, and zero is below their
 * floor. They show as plain numbers, and clearing one puts the module
 * default back. The audio1 gates default to zero, which IS their off
 * switch, so they get a checkbox.
 *
 * ARMING IS SEPARATE, and it only governs the audio1 gates. Ratio and
 * attack bite the moment they are written. The panel says so rather
 * than leaving an operator to find out from a cut.
 */

const DETECTOR_NOTE = "the detector's own gate — it has no off, so "
  + "clearing it restores the default";

export default function AudioGatesPanel({ uploadId, adminPassword, onSaved }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [edit, setEdit] = useState({});     // name -> string
  const [off, setOff] = useState({});       // name -> true when switched off
  const [arm, setArm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(null);

  const load = useCallback(async () => {
    try {
      const got = await api.getAudioGates(adminPassword, uploadId);
      setData(got);
      setArm(!!got.armed);
      setEdit(Object.fromEntries(got.gates.map((g) => [g.name,
        g.value == null ? "" : String(g.value)])));
      setOff(Object.fromEntries(got.gates.map((g) => [g.name,
        !g.detector_gate && !(Number(g.value) > 0)])));
      setErr(null);
    } catch (e) { setErr(e.message); }
  }, [adminPassword, uploadId]);

  useEffect(() => { load(); }, [load]);

  function seed(preset) {
    const g = data?.presets?.[preset]?.gates || {};
    setEdit((s) => {
      const next = { ...s };
      (data?.gates || []).forEach((q) => {
        if (q.name in g) next[q.name] = String(g[q.name]);
      });
      return next;
    });
    setOff((s) => {
      const next = { ...s };
      (data?.gates || []).forEach((q) => {
        if (q.detector_gate) return;
        next[q.name] = !(q.name in g);
      });
      return next;
    });
  }

  async function save() {
    setBusy(true); setSaved(null); setErr(null);
    try {
      const z = data.zone;
      const suffix = z == null ? "" : `@${z}`;
      const gates = {};
      (data.gates || []).forEach((g) => {
        const key = `${g.name}${suffix}`;
        if (!g.detector_gate && off[g.name]) {
          // Zero IS off for an audio1 gate.
          gates[key] = g.off_is ?? 0;
          return;
        }
        const v = Number(edit[g.name]);
        // A BLANK CLEARS, it does not mean zero. On a detector gate
        // zero is out of range anyway, and on an audio1 gate the
        // operator has a checkbox for off.
        gates[key] = (edit[g.name] ?? "") === "" || !Number.isFinite(v)
          ? null : v;
      });
      gates[`audio1_armed${suffix}`] = arm ? 1 : 0;
      await api.setGateTuning(adminPassword, uploadId, gates);
      await load();
      setSaved("saved — this is what the next cut will use");
      if (onSaved) onSaved();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  if (err && !data) {
    return <div className="tiny err-text">could not read the gates: {err}</div>;
  }
  if (!data) return <div className="tiny muted">reading the gates…</div>;

  const detector = data.gates.filter((g) => g.detector_gate);
  const audio1 = data.gates.filter((g) => !g.detector_gate);

  const row = (g) => (
    <label key={g.name} className="tiny"
           title={g.detector_gate ? DETECTOR_NOTE
             : `${g.name} · ${g.lo} to ${g.hi}`}
           style={{ display: "flex", alignItems: "center", gap: 5 }}>
      {!g.detector_gate && (
        <input type="checkbox" checked={!off[g.name]}
               onChange={(e) => setOff((s) => ({ ...s,
                 [g.name]: !e.target.checked }))}
               style={{ margin: 0 }} />
      )}
      <span style={{ flex: 1,
                     opacity: (!g.detector_gate && off[g.name]) ? 0.55 : 1 }}>
        {g.cmp === "max" ? "max " : "min "}{g.label}
        {/* WHERE THE VALUE IN FORCE CAME FROM. "1.3" alone does not
            say whether somebody chose it for this tee box or it is
            the number every untouched hole has. */}
        <span className="muted"> · {g.source}</span>
      </span>
      <input type="text" inputMode="decimal"
             value={edit[g.name] ?? ""}
             disabled={!g.detector_gate && off[g.name]}
             onChange={(e) => setEdit((s) => ({ ...s,
               [g.name]: e.target.value }))}
             style={{ width: 62, fontSize: 11, padding: "1px 4px" }} />
    </label>
  );

  return (
    <div className="card tight" style={{ marginTop: 6 }}>
      <div className="row" style={{ justifyContent: "space-between",
                                    gap: 8, flexWrap: "wrap" }}>
        <div className="small">
          <b>Gates for {data.zone_name || "this hole"}</b>{" "}
          <span className="muted tiny">
            · what the next cut of this recording will use
          </span>
        </div>
        <div className="row" style={{ gap: 6 }}>
          {Object.entries(data.presets || {}).map(([k, p]) => (
            <button key={k} type="button" className="btn ghost small"
                    title={p.why} onClick={() => seed(k)}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="tiny muted" style={{ marginTop: 3 }}>
        {data.sentence}
      </div>

      <div className="tiny" style={{ marginTop: 8, fontWeight: 600 }}>
        The detector's own gates
        <span className="muted" style={{ fontWeight: 400 }}>
          {" "}· these cut as soon as they are saved
        </span>
      </div>
      <div style={{ display: "grid", gap: "2px 14px", marginTop: 3,
                    gridTemplateColumns:
                      "repeat(auto-fill, minmax(230px, 1fr))" }}>
        {detector.map(row)}
      </div>

      <div className="tiny" style={{ marginTop: 8, fontWeight: 600 }}>
        The audio1 gates
        <span className="muted" style={{ fontWeight: 400 }}>
          {" "}· these only refuse anything once armed, below
        </span>
      </div>
      <div style={{ display: "grid", gap: "2px 14px", marginTop: 3,
                    gridTemplateColumns:
                      "repeat(auto-fill, minmax(230px, 1fr))" }}>
        {audio1.map(row)}
      </div>

      <div className="row" style={{ gap: 10, marginTop: 8,
                                    alignItems: "center",
                                    flexWrap: "wrap" }}>
        <label className="tiny" style={{ display: "inline-flex", gap: 5,
                                         alignItems: "center" }}
               title={"Off: the audio1 gates are worked out on every cut "
                      + "and reported, and refuse nothing. On: they "
                      + "decide. The detector's three above bite either "
                      + "way."}>
          <input type="checkbox" checked={arm}
                 onChange={(e) => setArm(e.target.checked)}
                 style={{ margin: 0 }} />
          act on the audio1 gates for {data.zone_name || "this hole"}
        </label>
        <button type="button" className="btn small" disabled={busy}
                onClick={save}>
          {busy ? "saving…" : "Save gates"}
        </button>
        {saved && <span className="tiny">{saved}</span>}
        {err && <span className="tiny err-text">{err}</span>}
      </div>
      {data.zone == null && (
        <div className="tiny muted" style={{ marginTop: 4 }}>
          No trigger zone was recorded for this recording, so these are
          the hole's own gates and saving writes them for the whole hole
          rather than one tee box.
        </div>
      )}
    </div>
  );
}
