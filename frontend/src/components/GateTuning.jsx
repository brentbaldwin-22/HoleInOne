/**
 * PER-HOLE GATE TUNING, edited where the gates are seen to fail.
 *
 * The report already prints, for every refused chain, the measured
 * value beside the limit it broke: "rises at 0.9, outside 1.0-12.0".
 * That sentence is the whole diagnosis, and until now the only way to
 * act on it was to change a constant and redeploy — which changed it
 * for every hole, including the ones it was already right for.
 *
 * So the limits are editable here, beside the evidence. Everything is
 * keyed per camera pair and hole: tuning hole 8 cannot move hole 3.
 *
 * ONLY DIFFERENCES ARE STORED. A hole tuned to exactly the defaults is
 * an untuned hole, and saving it as a copy of today's numbers would
 * pin it there — a later change to a default would then lift every
 * hole except the ones somebody had "confirmed".
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";

export function GateTuning({ adminPassword, uploadId, onSaved }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [draft, setDraft] = useState({});     // {key: string} being typed
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  async function load() {
    setErr(null);
    try {
      const d = await api.getGateTuning(adminPassword, uploadId);
      setData(d);
      setDraft({});
    } catch (e) {
      setErr(e?.message || String(e));
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [uploadId]);

  if (err) return <div className="err-text tiny">{err}</div>;
  if (!data) return <div className="tiny muted">Loading this hole&apos;s gates…</div>;

  const spec = data.spec || {};
  const values = data.values || {};
  const defaults = data.defaults || {};
  const tuned = data.tuned || {};
  // What would be sent: the saved overrides, with anything typed in
  // this session laid over them. A field cleared to empty means "back
  // to the default", which is the same as not sending it.
  const pending = () => {
    const out = { ...tuned };
    for (const [k, v] of Object.entries(draft)) {
      if (v === "") delete out[k];
      else if (Number.isFinite(Number(v))) out[k] = Number(v);
    }
    return out;
  };
  const shown = (k) => (draft[k] !== undefined ? draft[k]
    : (tuned[k] !== undefined ? String(tuned[k]) : ""));
  const dirty = Object.keys(draft).length > 0;
  const nTuned = Object.keys(pending()).length;

  async function saveCutter(name) {
    setBusy(true);
    setNote(null);
    try {
      // THE GATES GO WITH IT, unchanged. The backend keeps whichever
      // half it is not told about, but sending the pending edits here
      // means choosing a cutter does not silently discard numbers the
      // operator has typed and not yet saved.
      const out = await api.setGateTuning(
        adminPassword, uploadId, pending(), name);
      setNote(`Cutter set to ${name}. Re-produce to see it applied.`);
      await load();
      onSaved?.(out);
    } catch (e) {
      setNote(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(clearAll) {
    setBusy(true);
    setNote(null);
    try {
      const out = await api.setGateTuning(
        adminPassword, uploadId, clearAll ? {} : pending());
      setNote(Object.keys(out.tuned || {}).length
        ? `Saved against ${out.key} — ${Object.keys(out.tuned).length} `
          + "gate(s) tuned. Re-produce to see it applied."
        : "Cleared — this hole is back on the defaults.");
      await load();
      onSaved?.(out);
    } catch (e) {
      setNote(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const rows = Object.keys(spec);
  const asc = rows.filter((k) => k.startsWith("ascent_"));
  const desc = rows.filter((k) => k.startsWith("descent_"));
  // THE PREFIXES ARE THE GROUPING, and a gate whose prefix matches no
  // group is INVISIBLE here however correctly the backend serves it.
  // The audio gates landed in exactly that hole: added to the spec,
  // rendered by nothing, and reported as tunable when they were not.
  const aud = rows.filter((k) => k.startsWith("audio_"));
  // THE BALL AT ADDRESS, its own group. It landed in "Other" working
  // correctly, which is the trap this comment block is about: a gate
  // nobody can find is a gate nobody tunes.
  const ball = rows.filter((k) => k.startsWith("ball_"));
  const other = rows.filter((k) => !asc.includes(k) && !desc.includes(k)
    && !aud.includes(k) && !ball.includes(k));
  const label = (k) => k.replace(/^(ascent|descent|audio|ball)_/, "")
    .replace(/_/g, " ");
  // THE ONE GATE WHOSE NAME DOES NOT EXPLAIN ITSELF. "lag sec" in the
  // audio column reads as something about the detector; it is how
  // late the SOUND is against the strike, which is what moves the
  // ball check's six frames.
  const hint = (k) => (k === "audio_lag_sec"
    ? "how late the sound is against the real strike — the ball "
      + "check's frames move earlier by this"
    : null);
  const zones = data.zones || [];

  const group = (title, keys) => (
    <div style={{ flex: 1, minWidth: 260 }}>
      <div className="tiny upper muted" style={{ marginBottom: 4 }}>{title}</div>
      <table className="tiny" style={{ width: "100%" }}>
        <tbody>
          {keys.map((k) => {
            const sp = spec[k] || {};
            const isTuned = pending()[k] !== undefined;
            return (
              <tr key={k}>
                <td style={{ paddingRight: 6, whiteSpace: "nowrap" }}>
                  <span style={{ color: isTuned ? "#f59e0b" : undefined,
                                 fontWeight: isTuned ? 700 : 400 }}>
                    {label(k)}
                  </span>
                  {sp.verdict && (
                    <span className="muted"> · {sp.verdict}</span>
                  )}
                  {hint(k) && (
                    <div className="tiny muted" style={{ maxWidth: 230 }}>
                      {hint(k)}
                    </div>
                  )}
                </td>
                <td style={{ paddingRight: 6, textAlign: "right" }}
                    className="muted">
                  {defaults[k]}
                </td>
                <td>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={shown(k)}
                    placeholder={String(defaults[k])}
                    onChange={(e) => setDraft((d) => (
                      { ...d, [k]: e.target.value.replace(/[^\d.\-]/g, "") }
                    ))}
                    title={`${sp.int ? "Whole number" : "Number"} between `
                      + `${sp.lo} and ${sp.hi}. Blank = use the default `
                      + `(${defaults[k]}). In force now: ${values[k]}.`}
                    style={{ width: 72, padding: "0 4px", fontSize: 12,
                             textAlign: "right" }}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  return (
    <div style={{ marginTop: 8 }}>
      <div className="tiny muted" style={{ marginBottom: 6 }}>
        Gates for <b>{data.course_name || "this course"} · hole {data.hole}</b>,
        filed under <code>{data.key}</code> ({data.key_reason}). The middle
        column is the default; type a number to override it for this hole
        only, or leave it blank to use the default. Nothing here touches
        any other hole.
      </div>
      {/* WHICH CUTTER PRODUCTION RUNS ON THIS HOLE.
          Test Cut could run five detectors and show exactly what each
          would cut; production ran none of them — it swept the whole
          clip unless the rig had heard an impact. So the detector an
          operator had just verified here could not be the one used.
          It sits with the gates because the audio cutter is tuned by
          them. */}
      {(data.cutters || []).length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div className="tiny upper muted" style={{ marginBottom: 4 }}>
            Cutter — what narrows the search when producing
          </div>
          <select
            value={data.cutter || "auto"}
            disabled={busy}
            onChange={(e) => saveCutter(e.target.value)}
            style={{ width: "auto", maxWidth: "100%" }}
          >
            {(data.cutters || []).map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} — {c.detail}
              </option>
            ))}
          </select>
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {data.cutter_why}. A cutter that finds nothing falls back to
            searching the whole clip, so this can narrow the search but
            never hide a swing.
          </div>
        </div>
      )}
      <div className="row" style={{ gap: 16, flexWrap: "wrap",
                                    alignItems: "flex-start" }}>
        {group("Ascent — leaving the tee", asc)}
        {group("Descent — arriving on the green", desc)}
        {/* ANY GATE WITH A PREFIX NOTHING GROUPS. Better an ugly
            section than a silently missing one -- the audio gates
            were added to the spec and shown by nobody, and nothing
            said so. */}
        {ball.length > 0 && group("Ball — sitting on the tee", ball)}
        {other.length > 0 && group("Other", other)}
      </div>
      {aud.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="tiny upper muted" style={{ marginBottom: 4 }}>
            Audio — hearing the strike
          </div>
          {/* PER TEE, BECAUSE LOUDNESS IS DISTANCE. The camera stands
              behind the hitting area, so the back tee is a few yards
              from its microphone and a forward tee is a hundred: one
              ratio set for the back deafens the front, and set for
              the front it fires on everything near the camera.
              "gate@N" overrides "gate" for zone N -- see
              debug3.resolve_gates. */}
          <table className="tiny" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th />
                <th className="muted" style={{ textAlign: "right",
                                               paddingRight: 6 }}>
                  default
                </th>
                <th className="muted" style={{ paddingRight: 6 }}>
                  this hole
                </th>
                {zones.map((z) => (
                  <th key={z.index} className="muted"
                      style={{ paddingRight: 6, whiteSpace: "nowrap" }}
                      title={"Overrides this hole's value for Zone "
                        + z.screen + " only"}>
                    Zone {z.screen}
                    {z.label ? ` · ${z.label}` : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {aud.map((k) => {
                const isTuned = pending()[k] !== undefined;
                return (
                  <tr key={k}>
                    <td style={{ paddingRight: 6, whiteSpace: "nowrap" }}>
                      <span style={{ color: isTuned ? "#f59e0b" : undefined,
                                     fontWeight: isTuned ? 700 : 400 }}>
                        {label(k)}
                      </span>
                    </td>
                    <td style={{ paddingRight: 6, textAlign: "right" }}
                        className="muted">
                      {defaults[k]}
                    </td>
                    {[null, ...zones.map((z) => z.index)].map((zi) => {
                      const kk = zi === null ? k : `${k}@${zi}`;
                      return (
                        <td key={kk} style={{ paddingRight: 6 }}>
                          <input
                            type="text"
                            inputMode="decimal"
                            style={{ width: 64 }}
                            value={shown(kk)}
                            placeholder={zi === null
                              ? String(defaults[k])
                              : (shown(k) || String(defaults[k]))}
                            onChange={(e) => setDraft((d) => (
                              { ...d,
                                [kk]: e.target.value.replace(/[^\d.\-]/g, "") }
                            ))}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {zones.length === 0 && (
            <div className="tiny muted" style={{ marginTop: 4 }}>
              No trigger zones on this upload&apos;s camera — the hole&apos;s
              own values apply to every strike in it.
            </div>
          )}
        </div>
      )}
      <div className="row" style={{ gap: 8, marginTop: 8,
                                    alignItems: "center", flexWrap: "wrap" }}>
        <span className="tiny muted">
          {nTuned
            ? `${nTuned} gate${nTuned === 1 ? "" : "s"} tuned for this hole`
            : "Running the defaults"}
        </span>
        <button type="button" className="small" style={{ width: "auto" }}
                disabled={busy || !dirty} onClick={() => save(false)}>
          {busy ? "Saving…" : "Save gates for this hole"}
        </button>
        {dirty && (
          <button type="button" className="ghost small" style={{ width: "auto" }}
                  disabled={busy} onClick={() => setDraft({})}>
            Discard edits
          </button>
        )}
        {Object.keys(tuned).length > 0 && (
          <button type="button" className="ghost small" style={{ width: "auto" }}
                  disabled={busy} onClick={() => save(true)}
                  title={"Forget every override on this hole and run the "
                    + "module defaults again"}>
            Reset this hole to defaults
          </button>
        )}
      </div>
      {note && <div className="tiny" style={{ marginTop: 6 }}>{note}</div>}
    </div>
  );
}
