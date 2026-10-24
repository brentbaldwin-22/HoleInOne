/**
 * Produced clips — finished output of the production pipeline.
 *
 * Filters /admin/clips to clips that have actually been produced
 * (tracer URL is rendered OR the source is a dual-cam composite).
 * Raw, mid-pipeline, and un-processed clips stay on the legacy
 * /admin/clips iteration page (still reachable by URL).
 */
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Brand } from "../components/Brand.jsx";
import { useInfiniteList } from "../hooks/useInfiniteList.js";
import { fmtDateTime } from "../time.js";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

function isProduced(c) {
  if (!c) return false;
  if (c.tracer_url) return true;
  if ((c.source_url || "").includes("_composite")) return true;
  return false;
}

const diagStyle = {
  fontSize: "0.72rem",
  opacity: 0.75,
  marginTop: "0.35rem",
  fontFamily: "ui-monospace, SFMono-Regular, monospace",
};

export default function AdminProducedClips() {
  const adminPassword =
    localStorage.getItem(ADMIN_PW_STORAGE) ||
    localStorage.getItem(LEGACY_ADMIN_PW_STORAGE) ||
    "";

  // The "produced" filter runs client-side after fetch — the backend
  // /clips endpoint includes mid-pipeline rows too. That means a given
  // page of 25 raw clips might yield anywhere from 0–25 "produced"
  // ones, so the list density is a bit variable. Good enough; switching
  // to a server-side filter would mean a new endpoint.
  const fetchClips = useCallback(
    async (limit, offset) => {
      const list = await api.listAllClips(adminPassword, limit, offset);
      return (list || []).filter(isProduced);
    },
    [adminPassword],
  );
  const {
    items: clips,
    setItems: setClips,
    sentinelRef,
    hasMore,
    loadingMore,
    error: listError,
  } = useInfiniteList(fetchClips, { pageSize: 25, deps: [adminPassword] });
  const [actionError, setActionError] = useState(null);
  const error = actionError || listError;
  const setError = setActionError;
  const [busyId, setBusyId] = useState(null);
  // What the last 9:16 re-render actually did, per clip. A wrong
  // framing looks the same whether the pan never ran, the cut was
  // never found, or the flag was never located — this says which.
  const [vertInfo, setVertInfo] = useState(null);

  async function toggleBroadcast(clip) {
    setBusyId(clip.id);
    try {
      const next = !clip.is_highlight;
      await api.setClipBroadcast(adminPassword, clip.id, next);
      setClips((cs) => (cs || []).map(
        (c) => (c.id === clip.id ? { ...c, is_highlight: next } : c)
      ));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function openVertical(clip, ev) {
    // Open the 9:16 variant, generating it first if this clip predates
    // the feature. Alt/Option-click forces a re-render (picks up crop
    // style changes). Generation is one ffmpeg pass (~clip length).
    const force = !!(ev && ev.altKey);
    if (clip.vertical_url && !force) {
      window.open(clip.vertical_url, "_blank");
      return;
    }
    setBusyId(clip.id);
    try {
      const out = await api.makeClipVertical(adminPassword, clip.id, force);
      setVertInfo(out);
      // Only overwrite the url when one came back. Writing undefined
      // from a response that did not carry one blanked vertical_url
      // in local state, which hid the ⟳ button until a reload.
      if (out.vertical_url) {
        setClips((cs) => (cs || []).map(
          (c) => (c.id === clip.id
            ? { ...c, vertical_url: out.vertical_url } : c)
        ));
      }
      if (out.vertical_url) window.open(out.vertical_url, "_blank");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function deleteClip(clip) {
    const label = `#${clip.id}${
      clip.hole_number != null ? ` (hole ${clip.hole_number})` : ""
    }`;
    if (!window.confirm(
      `Delete produced clip ${label}? The video and its files are ` +
      `removed${clip.is_highlight ? " and it comes off Broadcast" : ""}. ` +
      `The raw upload stays on Production, so Re-Produce can recreate it.`
    )) {
      return;
    }
    setBusyId(clip.id);
    try {
      await api.deleteClip(adminPassword, clip.id);
      setClips((cs) => (cs || []).filter((c) => c.id !== clip.id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  if (!adminPassword) {
    return (
      <div className="wrap">
        <Brand subtitle="Operator Console" />
        <div className="card center">
          <h2>Admin password required</h2>
          <Link to="/admin"><button style={{ marginTop: 10 }}>Sign in</button></Link>
        </div>
      </div>
    );
  }

  return (
    <div className="wrap wide">
      <Brand subtitle="Operator Console" />
      <div className="nav">
        <Link to="/admin">Dashboard</Link>
        <Link to="/admin/participants">Players</Link>
        <Link to="/admin/courses">Courses</Link>
        <Link to="/admin/upload-videos">Upload</Link>
        <Link to="/admin/production">Production</Link>
        <Link to="/admin/produced-clips" className="active">Produced Clips</Link>
        <Link to="/admin/broadcast-clips">Broadcast</Link>
        <Link to="/admin/cameras">Cameras</Link>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 4 }}>Produced clips</h3>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Every clip that's finished the production pipeline — tracer
          rendered or dual-cam composite cut. Newest first. The full
          raw catalog (including in-progress clips) lives on{" "}
          <Link to="/admin/clips">/admin/clips</Link>.
        </p>
      </div>

      {error && <div className="card err-text small">{error}</div>}

      {clips === null && (
        <div className="card"><div className="shimmer" style={{ height: 200 }} /></div>
      )}

      {clips?.length === 0 && (
        <div className="card muted center" style={{ padding: 40 }}>
          No produced clips yet. Once a round finishes processing on{" "}
          <Link to="/admin/production">Production</Link>, finished clips show up here.
        </div>
      )}

      <div
        className="grid"
        style={{
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: 12,
        }}
      >
        {clips?.map((c) => {
          const playUrl = c.tracer_url || c.source_url;
          const isAce = !!c.ball_in_cup;
          return (
            <div key={c.id} className="card" style={{ margin: 0 }}>
              <div
                className="row"
                style={{
                  gap: 6, alignItems: "baseline", flexWrap: "wrap",
                  marginBottom: 6,
                }}
              >
                <b>#{c.id}</b>
                <span className="small muted">
                  {c.course_name || `course ${c.course_id}`} · hole {c.hole_number}
                </span>
                <div style={{ flex: 1 }} />
                {isAce && <span className="pill ok small">ace</span>}
                {c.participant_name ? (
                  <span className="pill ok small">{c.participant_name}</span>
                ) : (
                  <span className="pill warn small">unassigned</span>
                )}
              </div>
              <video
                src={playUrl}
                poster={c.thumbnail_url || undefined}
                controls
                style={{ width: "100%", borderRadius: 6, background: "#000" }}
              />
              <div className="tiny muted" style={{ marginTop: 6 }}>
                {fmtDateTime(c.captured_at)}
                {c.fps != null && <> · <code>{c.fps}</code> fps</>}
              </div>
              <div className="row" style={{ gap: 6, marginTop: 8 }}>
                <button
                  type="button"
                  className={c.is_highlight ? "" : "ghost"}
                  style={{ flex: 1 }}
                  onClick={() => toggleBroadcast(c)}
                  disabled={busyId === c.id}
                  title={c.is_highlight
                    ? "Remove from the Broadcast channel"
                    : "Send this clip to the Broadcast channel"}
                >
                  {busyId === c.id
                    ? "…"
                    : c.is_highlight ? "On Broadcast" : "Broadcast"}
                </button>
                <button
                  type="button"
                  className="ghost"
                  style={{ width: "auto" }}
                  onClick={(e) => openVertical(c, e)}
                  disabled={busyId === c.id}
                  title={c.vertical_url
                    ? "Open the full-screen 9:16 vertical (social / phone). Alt/Option-click to re-render."
                    : "Generate a full-screen 9:16 vertical of this clip (cropped to the action) for social / phone"}
                >
                  {busyId === c.id ? "…" : c.vertical_url ? "📱" : "📱＋"}
                </button>
                {c.vertical_url ? (
                  <button
                    type="button"
                    className="ghost"
                    style={{ width: "auto" }}
                    onClick={() => openVertical(c, { altKey: true })}
                    disabled={busyId === c.id}
                    title="Re-render the 9:16 vertical with the current code and open it. Same as Alt/Option-clicking 📱, but without the modifier — Safari gives Option-click its own meaning, so a plain button removes the doubt about whether the force registered."
                  >
                    {busyId === c.id ? "…" : "⟳"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="ghost"
                  style={{ width: "auto", color: "var(--danger)" }}
                  onClick={() => deleteClip(c)}
                  disabled={busyId === c.id}
                  title="Delete this produced clip and its files. The raw upload stays on Production; Re-Produce can recreate it."
                >
                  🗑
                </button>
              </div>
              {(() => {
                // THE FOUR ANCHORS, ON EVERY CARD. This used to render
                // only for whichever clip was rendered last, so the
                // numbers vanished on reload and two clips could never
                // be compared. The live response wins for the clip just
                // rendered (it is fresher than the row); every other
                // card reads what its last render stored.
                const live =
                  vertInfo && vertInfo.clip_id === c.id ? vertInfo : null;
                const d = c.vertical_diag || null;
                if (!live && !d) {
                  return (
                    <div style={diagStyle} title="No 9:16 render yet — press ⟳ to build one.">
                      9:16 not rendered
                    </div>
                  );
                }
                const pick = (a, b) => (live ? a : b);
                const mode = pick(live && live.mode, d && d.mode);
                const tracer = pick(live && live.tracer_x, d && d.tracer);
                const tracerWhy = pick(live && live.tracer_why, d && d.tracer_why);
                const cut = pick(live && live.cut, d && d.cut);
                const cutSrc = pick(live && live.cut_src, d && d.cut_src);
                const gball = pick(live && live.green_ball_x, d && d.gball);
                const gballWhy = pick(live && live.green_ball_why, d && d.gball_why);
                const pin = pick(live && live.pin_x, d && d.pin);
                const pinSrc = pick(live && live.pin_src, d && d.pin_src);
                const swing = pick(live && live.swing_match, d && d.swing_match);
                const clean = pick(live && live.clean_src, d && d.clean_src);
                const num = (v, why) =>
                  v != null ? String(v) : why ? String(why) : "none";
                const cutStr =
                  cut == null
                    ? "none"
                    : cutSrc
                      ? cut + "s(" + cutSrc + ")"
                      : cut + "s";
                return (
                  <div
                    style={diagStyle}
                    title="What the 9:16 render read off this clip's own frames. tracer = where the stroke starts (fraction of frame width). cut = the tee->green scene change. gball = where the ball finished, read from the green camera's tracer. pin = the flagstick. Any of them reading 'none' means that anchor was not found, and the pan falls back."
                  >
                    {`9:16 ${mode || "—"}`}
                    {clean === false ? " · STAMPED SRC" : ""}
                    {swing ? ` · swing ${swing}` : ""}
                    {` · tracer ${num(tracer, tracerWhy)}`}
                    {` · cut ${cutStr}`}
                    {` · gball ${num(gball, gballWhy)}`}
                    {` · pin ${num(pin)}${pin != null && pinSrc ? `(${pinSrc})` : ""}`}
                  </div>
                );
              })()}
            </div>
          );
        })}
      </div>

      {clips && clips.length > 0 && (
        <div
          ref={sentinelRef}
          className="muted center small"
          style={{ padding: 20 }}
        >
          {loadingMore
            ? "Loading more…"
            : hasMore
              ? "Scroll for more"
              : "End of produced clips"}
        </div>
      )}
    </div>
  );
}
