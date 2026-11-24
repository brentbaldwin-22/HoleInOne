import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PlayerIdLink from "../components/PlayerIdLink.jsx";
import { api } from "../api.js";
import { readAdminPassword } from "../adminAuth.js";
import { Brand } from "../components/Brand.jsx";

/*
 * Production flow — one recording, its cuts, and what became of them.
 *
 * FIVE VIEWS OF ONE PIPELINE:
 *
 *   raw        a recording off a camera pair, minutes long
 *   cuts       the swing-sized pieces the cutter heard in it, both
 *              angles, nothing drawn on them
 *   draft      produced automatically; nobody has looked
 *   final      an administrator looked and accepted it
 *   broadcast  it went out
 *
 * TWO DIFFERENT UNITS, AND THE STRIP SAYS SO. The first two tabs
 * count recordings and cuts; the last three count CLIPS. One
 * recording holding four swings is one row in `raw` and four in
 * `draft`, so these numbers are not a funnel and are not meant to
 * subtract. A reader who tries should be told, not left to work it
 * out from the arithmetic failing.
 *
 * BROADCAST IS NOT A STAGE. A clip can go out from draft or from
 * final, so it is shown as an independent fact with its own
 * timestamp. Were it a stage, "final" would stop meaning "a person
 * approved this" and start meaning "approved or went out", and
 * neither question could be answered afterwards.
 */

const CLIP_TABS = { draft: 1, final: 1, broadcast: 1 };

function fmtWhen(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch { return iso; }
}

function fmtSecs(v) {
  return v == null ? "—" : `${Number(v).toFixed(1)}s`;
}

export default function AdminProductionFlow() {
  // Read on every render, like its siblings: signing in on another tab
  // should not need a reload here. NOT a useState initialiser, which
  // snapshots whatever was stored the first time this mounted.
  const adminPassword = readAdminPassword();
  const [tab, setTab] = useState("draft");
  const [tabs, setTabs] = useState([]);
  const [note, setNote] = useState("");
  const [rows, setRows] = useState([]);
  const [cuts, setCuts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState(null);
  const [err, setErr] = useState(null);
  // A cut nothing was produced from is the interesting one: either a
  // swing the producer failed on or a sound that was never a swing,
  // and neither is visible in a list of produced clips.
  const [onlyUnproduced, setOnlyUnproduced] = useState(false);

  const loadCounts = useCallback(async () => {
    try {
      const d = await api.flowCounts(adminPassword);
      setTabs(d.tabs || []);
      setNote(d.note || "");
    } catch (e) { setErr(e.message); }
  }, [adminPassword]);

  const load = useCallback(async () => {
    if (!adminPassword) return;
    setBusy(true); setErr(null);
    try {
      if (tab === "cuts") {
        const d = await api.flowCuts(adminPassword, {
          produced: onlyUnproduced ? "0" : "" });
        setCuts(d.cuts || []);
      } else if (CLIP_TABS[tab]) {
        const d = await api.flowClips(adminPassword, { stage: tab });
        setRows(d.clips || []);
      } else {
        setRows([]);
      }
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }, [adminPassword, tab, onlyUnproduced]);

  useEffect(() => { loadCounts(); }, [loadCounts]);
  useEffect(() => { load(); }, [load]);

  async function act(fn, id, ...rest) {
    setActing(id); setErr(null);
    try {
      await fn(adminPassword, id, ...rest);
      await Promise.all([load(), loadCounts()]);
    } catch (e) { setErr(e.message); } finally { setActing(null); }
  }

  // THE SAME WAY OUT EVERY OTHER ADMIN PAGE GIVES. This said "sign in
  // on an admin page first" and offered no link to one -- and the
  // page carried no nav either, so a signed-out operator landing here
  // had prose, no sign-in, and no way back to a page that has one.
  if (!adminPassword) {
    return (
      <div className="wrap">
        <Brand subtitle="Operator Console" />
        <div className="card center">
          <h2>Admin password required</h2>
          <Link to="/admin">
            <button style={{ marginTop: 10 }}>Sign in</button>
          </Link>
        </div>
      </div>
    );
  }

  const current = tabs.find((t) => t.key === tab);

  return (
    <div className="wrap wide">
      <Brand subtitle="Operator Console" />
      <div className="nav">
        <Link to="/admin">Dashboard</Link>
        <Link to="/admin/participants">Players</Link>
        <Link to="/admin/courses">Courses</Link>
        <Link to="/admin/upload-videos">Upload</Link>
        <Link to="/admin/production">Production</Link>
        <Link to="/admin/production-flow" className="active">
          Production Flow
        </Link>
        <Link to="/admin/produced-clips">Produced Clips</Link>
        <Link to="/admin/broadcast-clips">Broadcast</Link>
        <Link to="/admin/cameras">Cameras</Link>
        <PlayerIdLink />
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 2 }}>Production flow</h2>
        <p className="small muted" style={{ marginBottom: 10 }}>
          {note || "One recording, the swings cut out of it, and what "
                 + "became of each."}
        </p>

        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              className={t.key === tab ? "" : "secondary"}
              onClick={() => setTab(t.key)}
              title={t.detail}
              style={{ padding: "4px 10px" }}
            >
              {t.label}{" "}
              <span className="pill small" style={{ marginLeft: 4 }}>
                {t.count}
              </span>
            </button>
          ))}
        </div>
        {current && (
          <p className="tiny muted" style={{ marginTop: 6 }}>
            {current.detail} · counted in <b>{current.unit}</b>
          </p>
        )}
        {err && <p className="tiny err-text">{err}</p>}
      </div>

      {tab === "raw" && (
        <div className="card">
          <p className="small muted">
            Raw recordings live on the{" "}
            <a href="/admin/long-upload">Long upload</a> screen, which
            already lists them with their sources, test cutter and audio
            diagnostics. They are counted here so the strip is complete;
            duplicating that list would give two places to delete a
            recording from and two chances to disagree about what exists.
          </p>
        </div>
      )}

      {tab === "cuts" && (
        <div className="card">
          <label className="tiny"
                 style={{ display: "inline-flex", gap: 5,
                          alignItems: "center", marginBottom: 8 }}>
            <input type="checkbox" checked={onlyUnproduced}
                   onChange={(e) => setOnlyUnproduced(e.target.checked)}
                   style={{ margin: 0 }} />
            only cuts nothing was produced from
          </label>
          {busy && <p className="tiny muted">loading…</p>}
          {!busy && cuts.length === 0 && (
            <p className="small muted">
              No cuts kept yet. They are recorded when a recording is test
              cut; each one is a swing the cutter heard, with both camera
              angles and nothing drawn on them.
            </p>
          )}
          <div style={{ display: "grid", gap: 10,
                        gridTemplateColumns:
                          "repeat(auto-fill, minmax(300px, 1fr))" }}>
            {cuts.map((k) => (
              <div key={k.id} className="card tight"
                   style={{ margin: 0, background: "var(--surface-alt)" }}>
                <div className="small">
                  <b>#{k.long_upload_id}</b>
                  <span className="muted"> · cut {k.idx + 1}</span>
                  {k.trigger_zone != null && (
                    <span className="muted">
                      {" "}· Zone {k.trigger_zone + 1}
                    </span>
                  )}
                  {k.produced
                    ? <span className="pill ok small"
                            style={{ marginLeft: 6 }}>produced</span>
                    : <span className="pill warn small"
                            style={{ marginLeft: 6 }}>no clip</span>}
                </div>
                <div className="tiny muted">
                  peak {fmtSecs(k.peak_time_sec)} · {fmtSecs(k.duration_sec)}
                  {k.detector ? ` · ${k.detector}` : ""}
                  {k.ratio != null ? ` · ×${Number(k.ratio).toFixed(1)}` : ""}
                </div>
                <div className="row" style={{ gap: 6, marginTop: 6,
                                              flexWrap: "wrap" }}>
                  {k.tee_url && (
                    <video src={k.tee_url} controls preload="metadata"
                           style={{ width: 142, borderRadius: 6,
                                    background: "#000" }} />
                  )}
                  {k.green_url && (
                    <video src={k.green_url} controls preload="metadata"
                           style={{ width: 142, borderRadius: 6,
                                    background: "#000" }} />
                  )}
                  {!k.green_url && (
                    <span className="tiny muted" style={{ alignSelf: "center" }}>
                      tee only
                    </span>
                  )}
                </div>
                {k.produce_error && (
                  <div className="tiny err-text" style={{ marginTop: 4 }}>
                    {k.produce_error}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {CLIP_TABS[tab] && (
        <div className="card">
          {busy && <p className="tiny muted">loading…</p>}
          {!busy && rows.length === 0 && (
            <p className="small muted">
              Nothing here yet.
            </p>
          )}
          <div style={{ display: "grid", gap: 10,
                        gridTemplateColumns:
                          "repeat(auto-fill, minmax(260px, 1fr))" }}>
            {rows.map((c) => (
              <div key={c.id} className="card tight"
                   style={{ margin: 0, background: "var(--surface-alt)" }}>
                <div className="small">
                  <b>Hole {c.hole_number}</b>
                  <span className="muted">
                    {" "}· {c.course_name || `course #${c.course_id}`}
                  </span>
                  {c.stage === "final"
                    ? <span className="pill ok small"
                            style={{ marginLeft: 6 }}>final</span>
                    : <span className="pill small"
                            style={{ marginLeft: 6 }}>draft</span>}
                  {c.broadcast_at && (
                    <span className="pill small" style={{ marginLeft: 4 }}>
                      broadcast
                    </span>
                  )}
                </div>
                <div className="tiny muted">
                  {fmtWhen(c.captured_at)}
                  {c.long_upload_id ? ` · from #${c.long_upload_id}` : ""}
                  {c.cut_idx != null ? ` · swing ${c.cut_idx + 1}` : ""}
                </div>
                {c.url && (
                  <video src={c.url} controls preload="metadata"
                         poster={c.thumbnail_url || undefined}
                         style={{ width: "100%", marginTop: 6,
                                  borderRadius: 6, background: "#000" }} />
                )}
                {c.accepted_at && (
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    accepted {fmtWhen(c.accepted_at)}
                    {c.accepted_by ? ` by ${c.accepted_by}` : ""}
                  </div>
                )}
                {c.broadcast_at && (
                  <div className="tiny muted">
                    broadcast {fmtWhen(c.broadcast_at)}
                  </div>
                )}
                <div className="row" style={{ gap: 6, marginTop: 6,
                                              flexWrap: "wrap" }}>
                  {c.stage !== "final" ? (
                    <button type="button" className="small"
                            disabled={acting === c.id}
                            onClick={() => act(api.flowAccept, c.id)}>
                      accept
                    </button>
                  ) : (
                    <button type="button" className="secondary small"
                            disabled={acting === c.id}
                            onClick={() => act(api.flowUnaccept, c.id)}>
                      back to draft
                    </button>
                  )}
                  {/* FROM EITHER STAGE, deliberately. A draft that goes
                      out is still a draft nobody approved. */}
                  <button type="button" className="secondary small"
                          disabled={acting === c.id}
                          onClick={() => act(api.flowBroadcast, c.id,
                                             !c.broadcast_at)}>
                    {c.broadcast_at ? "un-broadcast" : "broadcast"}
                  </button>
                  <a className="tiny" href="/admin/production"
                     style={{ alignSelf: "center" }}>edit</a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
