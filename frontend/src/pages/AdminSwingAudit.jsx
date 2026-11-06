/**
 * Swing audit — every piece of evidence behind one verdict, in a row.
 *
 * WHY THIS PAGE EXISTS. Judging a swing uses four things and until
 * now they lived in four places: the clip on the production card, the
 * composite the judge was shown in a filename on the event, the
 * geometry nowhere at all, and the gates' reasoning in a log line on
 * a Pi. Arguing with a verdict meant reconstructing it from parts.
 *
 * On 5-6 Nov the operator read most of 29 accepted clips as not
 * swings. That is the kind of thing this page is for: scroll it and
 * the pattern is visible in a minute instead of an afternoon.
 *
 * NOTHING HERE ASKS THE MODEL ANYTHING. Verdicts are the ones the rig
 * got; re-judging on every page load would spend money on a refresh.
 * The gates are arithmetic and recomputed live, so changing a
 * threshold and reloading shows what it would have done.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Brand } from "../components/Brand.jsx";
import { fmtDateTime } from "../time.js";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";

const card = {
  border: "1px solid #ddd", borderRadius: 10, padding: "0.9rem",
  marginBottom: "1.1rem", background: "#fff",
};
const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };
const shot = {
  width: "100%", borderRadius: 6, background: "#111", display: "block",
};

function Gate({ name, g }) {
  if (!g) return null;
  return (
    <div style={{ marginBottom: "0.25rem" }}>
      <span style={{
        display: "inline-block", minWidth: 74, fontWeight: 600,
        color: g.ok ? "#1a7f37" : "#c0392b",
      }}>
        {g.ok ? "pass" : "REFUSE"}
      </span>
      <span style={{ opacity: 0.6, marginRight: 6 }}>{name}</span>
      <span style={{ ...mono, fontSize: "0.78rem" }}>{g.why}</span>
    </div>
  );
}

function Num({ label, value, hint }) {
  return (
    <div style={{ marginRight: "1.4rem", display: "inline-block" }}>
      <div style={{ fontSize: "0.68rem", opacity: 0.55 }}>{label}</div>
      <div style={{ ...mono, fontSize: "0.95rem" }} title={hint || ""}>
        {value ?? "—"}
      </div>
    </div>
  );
}

export default function AdminSwingAudit() {
  const [pw, setPw] = useState(
    () => localStorage.getItem(ADMIN_PW_STORAGE) || "",
  );
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [limit, setLimit] = useState(30);

  const load = useCallback(async (refresh) => {
    if (!pw) return;
    setBusy(true);
    setErr("");
    try {
      localStorage.setItem(ADMIN_PW_STORAGE, pw);
      // FIRST LOAD IS SLOW ON PURPOSE: the strip and the measurement
      // need every clip decoded. They are cached after that.
      const d = await api.swingAudit(pw, limit, !!refresh);
      setRows(d.events || []);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }, [pw, limit]);

  useEffect(() => { if (pw) load(false); /* eslint-disable-line */ }, []);

  const kept = rows.filter((r) => r.would_keep).length;

  return (
    <div style={{ padding: "1rem", maxWidth: 1500, margin: "0 auto" }}>
      <Brand />
      <h2>Swing audit</h2>
      <p style={{ opacity: 0.7, maxWidth: 760 }}>
        Every audio-triggered clip with all the evidence behind its
        verdict: the clip, the composite the judge was shown, four real
        frames around the impact, the measured heat blob, and what each
        gate makes of it. Verdicts are the ones the rig got — nothing
        here re-asks the model. The gates are recomputed live.
      </p>

      <div style={{ margin: "0.8rem 0" }}>
        <input
          type="password" placeholder="admin password" value={pw}
          onChange={(e) => setPw(e.target.value)}
          style={{ padding: "0.4rem", marginRight: 8 }}
        />
        <input
          type="number" value={limit} min={1} max={100}
          onChange={(e) => setLimit(Number(e.target.value) || 30)}
          style={{ padding: "0.4rem", width: 70, marginRight: 8 }}
        />
        <button onClick={() => load(false)} disabled={busy || !pw}>
          {busy ? "working…" : "Load"}
        </button>
        <button
          onClick={() => load(true)} disabled={busy || !pw}
          style={{ marginLeft: 8 }}
          title="Re-render every composite, strip and measurement"
        >
          Rebuild
        </button>
        <Link to="/admin/production" style={{ marginLeft: 14 }}>
          Production
        </Link>
      </div>

      {err && <p style={{ color: "#c0392b" }}>{err}</p>}
      {busy && !rows.length && (
        <p style={{ opacity: 0.7 }}>
          Decoding every clip to build its frames — the first run takes a
          minute or two, then it is cached.
        </p>
      )}

      {rows.length > 0 && (
        <p style={{ ...mono }}>
          {rows.length} clips · the gates would keep{" "}
          <b>{kept}</b> and refuse <b>{rows.length - kept}</b>
        </p>
      )}

      {rows.map((r) => (
        <div key={r.id} style={{
          ...card,
          borderLeft: `5px solid ${r.would_keep ? "#1a7f37" : "#c0392b"}`,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <b>#{r.id} · hole {r.hole ?? "—"}</b>
            <span style={{ opacity: 0.6 }}>
              {fmtDateTime(r.triggered_at)}
            </span>
          </div>

          <div style={{ margin: "0.5rem 0 0.7rem" }}>
            <Num label="strike" value={r.ratio != null ? `×${r.ratio}` : null}
                 hint="how many times the noise floor" />
            <Num label="gate" value={`×${r.gate_ratio}`} />
            <Num label="over gate"
                 value={r.ratio ? (r.ratio / r.gate_ratio).toFixed(2) + "×" : null} />
            <Num label="attack" value={r.attack_ms ? `${r.attack_ms} ms` : null} />
            <Num label="judge said"
                 value={r.verdict === true ? "SWING"
                        : r.verdict === false ? "not" : "—"} />
            <Num label="confidence" value={r.confidence} />
            <Num label="blob"
                 value={r.blob ? `${r.blob[0]}×${r.blob[1]}` : null} />
            <Num label="h/w" value={r.blob ? r.blob[2] : null} />
            <Num label="zone" value={r.zone_label || r.zone} />
            <Num label="impact at"
                 value={r.impact_at_sec != null ? `${r.impact_at_sec}s` : null}
                 hint="seconds into the clip" />
          </div>

          <div style={{
            display: "grid", gap: "0.6rem",
            gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
          }}>
            <figure style={{ margin: 0 }}>
              <figcaption style={{ fontSize: "0.72rem", opacity: 0.6 }}>
                the clip
              </figcaption>
              {r.clip_url
                ? <video src={r.clip_url} controls preload="metadata"
                         style={shot} />
                : <div style={{ ...shot, height: 160 }} />}
            </figure>
            <figure style={{ margin: 0 }}>
              <figcaption style={{ fontSize: "0.72rem", opacity: 0.6 }}>
                what the judge saw (the rig's composite)
              </figcaption>
              {r.rig_heat_url
                ? <a href={r.rig_heat_url} target="_blank" rel="noreferrer">
                    <img src={r.rig_heat_url} alt="" style={shot} />
                  </a>
                : <div style={{ ...shot, height: 160 }} />}
            </figure>
            <figure style={{ margin: 0 }}>
              <figcaption style={{ fontSize: "0.72rem", opacity: 0.6 }}>
                four frames around the impact
              </figcaption>
              {r.frames_url
                ? <a href={r.frames_url} target="_blank" rel="noreferrer">
                    <img src={r.frames_url} alt="" style={shot} />
                  </a>
                : <div style={{ ...shot, height: 160 }} />}
            </figure>
            <figure style={{ margin: 0 }}>
              <figcaption style={{ fontSize: "0.72rem", opacity: 0.6 }}>
                the server's composite (Test Cut's renderer — a wider
                window, so a different picture)
              </figcaption>
              {r.server_heat_url
                ? <a href={r.server_heat_url} target="_blank" rel="noreferrer">
                    <img src={r.server_heat_url} alt="" style={shot} />
                  </a>
                : <div style={{ ...shot, height: 160 }} />}
            </figure>
          </div>

          <div style={{ marginTop: "0.7rem" }}>
            <Gate name="shape" g={r.gate_shape} />
            <Gate name="stance" g={r.gate_stance} />
            <Gate name="volume" g={r.gate_confidence} />
            {r.reason && (
              <div style={{
                marginTop: "0.4rem", fontSize: "0.8rem", opacity: 0.75,
              }}>
                judge: <i>{r.reason}</i>
              </div>
            )}
            {!r.stance && (
              <div style={{
                marginTop: "0.3rem", fontSize: "0.74rem", color: "#b26a00",
              }}>
                no golfer position recorded on this event — the stance gate
                cannot run. Clips captured before 6 Nov do not carry one.
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
