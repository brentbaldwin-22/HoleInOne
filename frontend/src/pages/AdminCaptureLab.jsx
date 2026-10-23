import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

// A SEPARATE PAGE ON PURPOSE. Two capture engines now exist and the
// second one has to earn its place across real weather on a real
// course before anything is retired. Putting the comparison here keeps
// the Cameras page -- the one that gets used every day -- exactly as it
// was, and gives the experiment somewhere to be read without anyone
// having to SSH into a Pi to find out what it is doing.
//
// Everything shown comes from the heartbeat each agent already sends,
// so this page adds no load to the cameras and no endpoint to the
// backend.

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const POLL_MS = 15000;

function mb(n) {
  if (!n && n !== 0) return "—";
  return n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(1)} MB`;
}

function rel(iso) {
  if (!iso) return "—";
  const utc = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const s = Math.round((Date.now() - new Date(utc).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function relEpoch(sec) {
  if (!sec) return "—";
  const s = Math.round(Date.now() / 1000 - sec);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

export default function AdminCaptureLab() {
  const [adminPassword, setAdminPassword] = useState(
    () => localStorage.getItem(ADMIN_PW_STORAGE) || "",
  );
  const [cameras, setCameras] = useState(null);
  const [error, setError] = useState("");
  // The same overlap guard the Cameras page needs: setInterval does not
  // care whether the last call came back, and a slow response used to
  // stack requests until the pool ran dry.
  const running = useRef(false);

  useEffect(() => {
    if (!adminPassword) return undefined;
    let alive = true;
    async function load() {
      if (running.current) return;
      running.current = true;
      try {
        const list = await api.listCameras(adminPassword);
        if (alive) { setCameras(list); setError(""); }
      } catch (e) {
        if (alive) setError(String(e?.message || e));
      } finally {
        running.current = false;
      }
    }
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [adminPassword]);

  if (!adminPassword) {
    return (
      <div className="container">
        <h2>Capture lab</h2>
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            const v = new FormData(e.target).get("pw");
            localStorage.setItem(ADMIN_PW_STORAGE, v);
            setAdminPassword(v);
          }}
        >
          <input name="pw" type="password" placeholder="Admin password" />
          <button type="submit">Unlock</button>
        </form>
      </div>
    );
  }

  const cams = cameras || [];
  const copyCams = cams.filter((c) => c.stream?.engine === "copy");

  return (
    <div className="container">
      <div style={{ display: "flex", alignItems: "baseline", gap: 12,
                    flexWrap: "wrap" }}>
        <h2 style={{ marginBottom: 0 }}>Capture lab</h2>
        <Link to="/admin/cameras" className="small">← Cameras</Link>
      </div>

      <p className="small muted" style={{ maxWidth: 760 }}>
        Which capture engine each camera is actually running, in the Pi's own
        words. <b>decode</b> turns the camera's H.264 into raw frames and
        re-encodes twice; <b>copy</b> keeps the camera's own encoded video in a
        rolling on-disk ring and cuts the swing out of it. A config file says
        what a Pi was <i>told</i>; this says what it is <i>doing</i>, which are
        different until it restarts.
      </p>

      {error && <div className="card warn small">{error}</div>}
      {cameras === null && (
        <div className="card"><div className="shimmer" style={{ height: 70 }} /></div>
      )}

      {cameras !== null && copyCams.length === 0 && (
        <div className="card small muted">
          No camera is running the copy engine yet. Set{" "}
          <code>capture_engine: "copy"</code> in a Pi's{" "}
          <code>config.yaml</code> and restart <code>golfreelz-agent</code>.
          Everything below will fill in on its next heartbeat. Until then every
          camera stays on the engine it has always used.
        </div>
      )}

      <div className="stack" style={{ gap: 10 }}>
        {cams.map((cam) => {
          const st = cam.stream || {};
          const ring = st.ring || null;
          const isCopy = st.engine === "copy";
          const last = ring?.last || null;
          return (
            <div key={cam.id} className="card tight"
                 style={{ margin: 0, padding: 12 }}>
              <div className="small" style={{ display: "flex", gap: 8,
                                              alignItems: "center",
                                              flexWrap: "wrap" }}>
                <b>#{cam.id}</b>
                <span>{cam.name || `hole ${cam.assigned_hole}`}</span>
                <span className="pill small">{cam.assigned_role}</span>
                <span className={`pill small ${isCopy ? "" : "ok"}`}
                      style={isCopy
                        ? { background: "#2563eb", color: "#fff" }
                        : undefined}
                      title={isCopy
                        ? "Recording by copying the camera's own H.264"
                        : "Decoding and re-encoding every frame"}>
                  {isCopy ? "⧉ copy engine" : "decode engine"}
                </span>
                {isCopy && ring && (
                  <span className={`pill small ${ring.healthy ? "ok" : "warn"}`}
                        style={ring.healthy ? undefined
                          : { background: "#dc2626", color: "#fff" }}>
                    {ring.healthy ? "ring healthy" : "RING DOWN"}
                  </span>
                )}
                <span className="muted" style={{ marginLeft: "auto" }}>
                  heartbeat {rel(st.updated_at || cam.last_seen_at)}
                </span>
              </div>

              <div className="small muted" style={{ marginTop: 6 }}>
                {st.open_w ? `${st.open_w}x${st.open_h}` : "—"}
                {st.open_fps ? ` @ ${st.open_fps}` : ""}
                {st.delivered_fps ? ` · delivering ${st.delivered_fps} fps` : ""}
                {st.stamped_fps ? ` · stamped ${st.stamped_fps}` : ""}
                {st.mismatch && (
                  <span className="warn"> · {st.mismatch}</span>
                )}
              </div>

              {isCopy && ring && (
                <div style={{ marginTop: 10, display: "grid", gap: 8,
                              gridTemplateColumns:
                                "repeat(auto-fit, minmax(150px, 1fr))" }}>
                  <Stat label="ring holds"
                        value={`${ring.seconds_held ?? "—"}s`}
                        sub={`${ring.segments ?? 0} segments · ${mb(ring.bytes_held)}`} />
                  <Stat label="segment size"
                        value={`${ring.segment_seconds ?? "—"}s`}
                        sub={`window ${ring.window_seconds ?? "—"}s`} />
                  <Stat label="clips cut"
                        value={ring.clips_extracted ?? 0}
                        sub={ring.clips_failed
                          ? `${ring.clips_failed} failed`
                          : "none failed"}
                        bad={!!ring.clips_failed} />
                  <Stat label="ring restarts"
                        value={ring.restarts ?? 0}
                        sub={ring.restarts ? "stream dropped" : "stable"}
                        bad={!!ring.restarts} />
                </div>
              )}

              {/* `last` is {} until a clip has actually been cut, and an
                  empty object is TRUTHY in JS — which rendered "no clip
                  yet" as a failure with undefined fields, next to a
                  counter reading zero failures. Key off the timestamp,
                  which only exists once something really happened. */}
              {isCopy && !last?.at && (
                <div className="small muted" style={{ marginTop: 10 }}>
                  No clip cut yet. The ring is recording; it is waiting
                  for a trigger.
                </div>
              )}
              {isCopy && last?.at && (
                <div className="small" style={{ marginTop: 10 }}>
                  <span className="tiny upper muted">Last clip</span>{" "}
                  {last.ok ? (
                    <>
                      <b>{last.seconds}s</b> · {mb(last.bytes)} ·{" "}
                      cut from {last.segments} segments in{" "}
                      <b>{last.took}s</b> · ended on {last.reason} ·{" "}
                      {relEpoch(last.at)}
                      {last.missing_seconds > 0 && (
                        <div className="warn tiny" style={{ marginTop: 2 }}>
                          {last.missing_seconds}s missing from a{" "}
                          {last.span_seconds}s window — the ring was not
                          recording for part of it. Check ring restarts.
                        </div>
                      )}
                      <div className="tiny muted" style={{ marginTop: 2 }}>
                        A decode-engine clip of this length takes roughly
                        twenty times that long to produce, and is a third
                        generation copy rather than a first.
                      </div>
                    </>
                  ) : (
                    <span className="warn">
                      failed: {last.why} · {relEpoch(last.at)}
                      <div className="tiny muted">
                        The swing fell back to nothing — check the ring's
                        health above. A camera whose ring keeps failing
                        should go back to <code>capture_engine: "decode"</code>.
                      </div>
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, bad }) {
  return (
    <div style={{ padding: "6px 10px", borderRadius: 8,
                  background: "rgba(127,127,127,0.08)" }}>
      <div className="tiny upper muted">{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600,
                    color: bad ? "#dc2626" : undefined }}>{value}</div>
      {sub && <div className="tiny muted">{sub}</div>}
    </div>
  );
}
