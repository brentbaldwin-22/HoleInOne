import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import PlayerIdLink from "../components/PlayerIdLink.jsx";
import { api } from "../api.js";
import { readAdminPassword } from "../adminAuth.js";
import { Brand } from "../components/Brand.jsx";
import { VideoLightbox } from "../components/VideoLightbox.jsx";
import { zoneName } from "../zones.js";

/*
 * The production flow, left to right: one recording becomes drafts.
 *
 *   raw     a recording off a camera pair, both angles openable
 *   cuts    what the microphone heard, cut to size, nothing judged
 *   draft   produced automatically, nobody has looked
 *   final   somebody looked and approved it
 *
 * COLUMNS, NOT TABS, because the question the operator is asking is
 * "what happened to this recording" and the answer is spread across
 * all four. Tabs made them check four screens and hold the join in
 * their head.
 *
 * NOTHING FROM BEFORE THIS FLOW IS SHOWN. There are a thousand clips
 * in the database from every pipeline the console has ever had. A
 * column headed "Draft produced" that opens full of them says nothing
 * about the flow and buries the two clips somebody is looking at. The
 * server filters on provenance -- a clip produced through here knows
 * which cut it came from, and nothing else does.
 *
 * THE VIDEOS OPEN IN THE CONSOLE'S OWN VIEWER, the same one Production
 * uses, because a new browser tab loses the wall clock and the frame
 * number -- which are the two things an operator opens a raw angle to
 * read.
 */

const POLL_MS = 2500;

/*
 * WHICH COLUMN A STATUS LINE BELONGS TO.
 *
 * The background runner carries one `stage` string and this page has
 * four columns, so the runner tags each line with its phase --
 * "cut|Cutting strike 2 of 5". Untagged lines are the runner's own
 * ("starting", "queued behind 2 uploads"), which belong on the
 * recording's card where the button was pressed.
 */
function phaseOf(stage) {
  const i = (stage || "").indexOf("|");
  if (i < 0) return { phase: null, text: stage || "" };
  return { phase: stage.slice(0, i), text: stage.slice(i + 1) };
}

/** The live status line for one column, or null. */
function Working({ run, phase }) {
  if (!run?.running) return null;
  const { phase: p, text } = phaseOf(run.stage);
  if (p !== phase) return null;
  return (
    <div className="card tight" style={{ margin: 0 }}>
      <div className="tiny">
        <span className="pill small">working</span>{" "}
        {text || "…"}
        {run.total ? ` · ${run.done ?? 0} of ${run.total}` : ""}
      </div>
      <div className="shimmer" style={{ height: 3, marginTop: 6 }} />
    </div>
  );
}

function fmtWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit",
  });
}

function fmtDur(sec) {
  if (sec === null || sec === undefined) return "—";
  const s = Number(sec);
  if (!Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
}

function Column({ title, unit, count, blurb, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div className="card tight" style={{ margin: 0, marginBottom: 8 }}>
        <div className="small"><b>{title}</b>{" "}
          <span className="pill small" style={{ marginLeft: 4 }}>
            {count ?? "—"} {unit}
          </span>
        </div>
        <div className="tiny muted" style={{ marginTop: 2 }}>{blurb}</div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

/** A phase timing table. "94 seconds" is not actionable; which phase is. */
function Phases({ phases, total }) {
  if (!phases?.length) return null;
  return (
    <table className="tiny" style={{ borderCollapse: "collapse",
                                     marginTop: 4, width: "100%" }}>
      <tbody>
        {phases.map((p) => (
          <tr key={p.name}>
            <td className="muted" style={{ paddingRight: 8,
                                           whiteSpace: "normal" }}>
              {p.title}
              {p.count != null && (
                <span className="muted"> · {p.count} {p.units}</span>
              )}
            </td>
            <td style={{ textAlign: "right", fontVariantNumeric:
                         "tabular-nums", whiteSpace: "nowrap" }}>
              {fmtDur(p.secs)}
            </td>
          </tr>
        ))}
        {total != null && (
          <tr style={{ borderTop: "1px solid var(--line)" }}>
            <td className="muted" style={{ paddingRight: 8 }}>total</td>
            <td style={{ textAlign: "right", fontWeight: 600,
                         fontVariantNumeric: "tabular-nums" }}>
              {fmtDur(total)}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export default function AdminProductionFlow() {
  const adminPassword = readAdminPassword();

  const [counts, setCounts] = useState(null);
  const [raw, setRaw] = useState(null);
  const [cuts, setCuts] = useState(null);
  const [drafts, setDrafts] = useState(null);
  const [finals, setFinals] = useState(null);
  const [err, setErr] = useState(null);
  const [acting, setActing] = useState(null);
  const [runs, setRuns] = useState({});       // uploadId -> status blob
  const [liveDrafts, setLiveDrafts] = useState(null);
  const [viewer, setViewer] = useState(null);
  const [focus, setFocus] = useState(0);      // which recording, 0 = all
  const polling = useRef(new Set());

  const load = useCallback(async () => {
    if (!adminPassword) return;
    try {
      const [c, r, k, d, f] = await Promise.all([
        api.flowCounts(adminPassword),
        api.flowRaw(adminPassword, { limit: 40 }),
        api.flowCuts(adminPassword, { limit: 120 }),
        api.flowClips(adminPassword, { stage: "draft", limit: 60 }),
        api.flowClips(adminPassword, { stage: "final", limit: 60 }),
      ]);
      setCounts(c); setRaw(r.raw); setCuts(k.cuts);
      setDrafts(d.clips); setFinals(f.clips);
      setErr(null);
    } catch (e) { setErr(e.message); }
  }, [adminPassword]);

  useEffect(() => { load(); }, [load]);

  // WHILE A RUN IS GOING, only the two columns it is filling.
  //
  // The point of the tick is that the operator can watch the cuts
  // arrive and review them while the produce is still running, so it
  // has to be cheap enough to run every couple of seconds: the raw
  // list probes a video file per row for its duration and has not
  // changed anyway.
  //
  // THE DRAFTS ARE ASKED FOR BY UPLOAD AND UNFILTERED, because the
  // flow stamp is not applied until the run finishes and settles --
  // deliberately, since a clip row can be superseded on its way
  // there. A produce CLEARS the upload's existing clips before it
  // writes any, so while one is running every clip on that upload is
  // this run's work, and showing them is not a guess.
  const loadLive = useCallback(async (uploadId) => {
    try {
      const [c, k, d] = await Promise.all([
        api.flowCounts(adminPassword),
        api.flowCuts(adminPassword, { limit: 120 }),
        api.flowClips(adminPassword, { stage: "draft", limit: 60,
                                       uploadId, flowOnly: false }),
      ]);
      setCounts(c); setCuts(k.cuts); setLiveDrafts(d.clips);
    } catch { /* a tick that fails is a tick; the next one will do */ }
  }, [adminPassword]);

  // A run is minutes of work on a thread, so the page asks how it is
  // going rather than waiting on the request that started it.
  const poll = useCallback((uploadId) => {
    if (polling.current.has(uploadId)) return;
    polling.current.add(uploadId);
    const tick = async () => {
      try {
        const st = await api.flowStatus(adminPassword, uploadId);
        setRuns((s) => ({ ...s, [uploadId]: st }));
        if (st?.running) {
          await loadLive(uploadId);
          setTimeout(tick, POLL_MS);
          return;
        }
        polling.current.delete(uploadId);
        setLiveDrafts(null);
        await load();
      } catch {
        polling.current.delete(uploadId);
      }
    };
    setTimeout(tick, 600);
  }, [adminPassword, load, loadLive]);

  async function start(uploadId) {
    setActing(`raw-${uploadId}`); setErr(null);
    try {
      const got = await api.flowStart(adminPassword, uploadId);
      setRuns((s) => ({ ...s, [uploadId]: { ...got, running: true } }));
      poll(uploadId);
      setFocus(uploadId);
    } catch (e) { setErr(e.message); } finally { setActing(null); }
  }

  async function act(key, fn, ...args) {
    setActing(key); setErr(null);
    try { await fn(adminPassword, ...args); await load(); }
    catch (e) { setErr(e.message); } finally { setActing(null); }
  }

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

  const countOf = (k) =>
    counts?.tabs?.find((t) => t.key === k)?.count;
  const keep = (list, uploadId) =>
    (focus ? (list || []).filter((x) => uploadId(x) === focus) : (list || []));

  // The run the columns are narrating: the one being followed, or
  // whichever is going if nothing is.
  const liveId = Number(
    Object.keys(runs).find((id) => runs[id]?.running
      && (!focus || Number(id) === focus)) || 0);
  const liveRun = liveId ? runs[liveId] : null;

  const shownCuts = keep(cuts, (k) => k.long_upload_id);
  // ONLY ONCE IT IS ACTUALLY PRODUCING.
  //
  // The live draft fetch is unfiltered by design: the flow stamp the
  // column normally filters on is not applied until the run settles.
  // That is only safe to show because a produce CLEARS the upload's
  // existing clips before it writes any -- and it does that when the
  // produce STARTS, not when the run does. Using it during the audio
  // and cutting phases would put clips from some older pipeline in a
  // column headed "produced by this run", for as long as those phases
  // take.
  const producing = phaseOf(liveRun?.stage).phase === "produce";
  const draftSource = producing && liveDrafts ? liveDrafts : drafts;
  const shownDrafts = keep(draftSource, (c) => c.long_upload_id);
  const shownFinals = keep(finals, (c) => c.long_upload_id);

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

      <div className="card tight">
        <div className="row" style={{ justifyContent: "space-between",
                                      flexWrap: "wrap", gap: 8 }}>
          <div className="small">
            <b>Production flow</b>{" "}
            <span className="muted tiny">
              · one recording, left to right · only what this flow made
            </span>
          </div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            {focus > 0 && (
              <button type="button" className="btn ghost small"
                      onClick={() => setFocus(0)}>
                showing recording #{focus} — show all
              </button>
            )}
            <button type="button" className="btn ghost small" onClick={load}>
              refresh
            </button>
          </div>
        </div>
      </div>

      {err && <div className="card err-text small">{err}</div>}

      <div style={{ display: "grid", gap: 12, alignItems: "start",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(265px, 1fr))" }}>

        {/* ── 1. RAW ───────────────────────────────────────────── */}
        <Column title="Raw clips" unit="recordings" count={countOf("raw")}
                blurb="Both angles off the camera pair. Open either in the
                       console's viewer, then start the flow on it.">
          {raw === null && <div className="card tight"><div className="shimmer"
            style={{ height: 90 }} /></div>}
          {raw?.length === 0 && (
            <div className="card tight"><p className="tiny muted">
              No recordings.</p></div>
          )}
          {(raw || []).map((r) => {
            const run = runs[r.id];
            const busy = !!run?.running;
            return (
              <div key={r.id} className="card tight"
                   style={{ margin: 0,
                            outline: focus === r.id
                              ? "2px solid var(--accent, #1a56db)" : "none" }}>
                <div className="small">
                  <b>#{r.id}</b>
                  {r.hole_number ? <> · Hole {r.hole_number}</> : null}
                  <span className="muted">
                    {" "}· {r.course_name || `course #${r.course_id}`}
                  </span>
                </div>
                <div className="tiny muted">
                  {fmtWhen(r.captured_at)} · {fmtDur(r.duration_sec)}
                  {" · "}
                  {r.trigger_zone == null
                    ? "no zone recorded"
                    : zoneName(r.trigger_zone)}
                </div>
                <div className="row" style={{ gap: 6, marginTop: 5,
                                              flexWrap: "wrap" }}>
                  <button type="button" className="btn ghost small"
                          disabled={!r.tee_url}
                          onClick={() => setViewer({
                            url: r.tee_url, title: `#${r.id} tee angle`,
                            startedAt: r.captured_at })}>
                    tee angle
                  </button>
                  <button type="button" className="btn ghost small"
                          disabled={!r.green_url}
                          title={r.has_green ? "" : "this pair has no green"}
                          onClick={() => setViewer({
                            url: r.green_url, title: `#${r.id} green angle`,
                            startedAt: r.captured_at })}>
                    green angle
                  </button>
                </div>
                <button type="button" className="btn small"
                        style={{ marginTop: 6, width: "100%" }}
                        disabled={busy || acting === `raw-${r.id}`}
                        onClick={() => start(r.id)}>
                  {busy ? "running…"
                    : acting === `raw-${r.id}` ? "starting…"
                      : "Start production flow"}
                </button>
                {/* Only the runner's OWN lines -- "starting", "queued
                    behind 2 uploads". Everything tagged with a phase
                    belongs over the column doing that work, which is
                    the point of following a run across the page. */}
                {busy && !phaseOf(run?.stage).phase && (
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    {phaseOf(run?.stage).text || "working"}
                    {run?.total ? ` · ${run.done ?? 0} of ${run.total}` : ""}
                  </div>
                )}
                {busy && phaseOf(run?.stage).phase && (
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    running · see the{" "}
                    {phaseOf(run?.stage).phase === "produce"
                      ? "draft" : "cuts"} column
                  </div>
                )}
                {/* `report`, which is what the runner stores. It was
                    `result` here -- a field nothing writes, so a
                    finished run showed no timings and no counts and
                    looked like a run that had done nothing. */}
                {!busy && run?.error && (
                  <div className="tiny err-text" style={{ marginTop: 4 }}>
                    {run.error}
                  </div>
                )}
                {!busy && run?.report && (
                  <>
                    <div className="tiny" style={{ marginTop: 4 }}>
                      {run.report.n_cuts ?? 0} cut
                      {run.report.n_cuts === 1 ? "" : "s"} ·{" "}
                      {run.report.n_clips ?? 0} draft
                      {run.report.n_clips === 1 ? "" : "s"}
                    </div>
                    {run.report.reason && (
                      <div className="tiny muted" style={{ marginTop: 2 }}>
                        {run.report.reason}
                      </div>
                    )}
                    <Phases phases={run.report.phases}
                            total={run.report.total_sec} />
                  </>
                )}
                {r.cuts > 0 && (
                  <button type="button" className="btn ghost small"
                          style={{ marginTop: 5, width: "100%" }}
                          onClick={() => setFocus(focus === r.id ? 0 : r.id)}>
                    {focus === r.id ? "showing this one" : "follow this one"}
                  </button>
                )}
              </div>
            );
          })}
        </Column>

        {/* ── 2. AUDIO CUTS ────────────────────────────────────── */}
        <Column title="Audio cuts" unit="cuts" count={countOf("cuts")}
                blurb="What the microphone heard, cut to size on both
                       angles. No swing judgement — only that it cleared
                       this tee box's gates.">
          <Working run={liveRun} phase="audio" />
          <Working run={liveRun} phase="cut" />
          {cuts === null && <div className="card tight"><div className="shimmer"
            style={{ height: 90 }} /></div>}
          {cuts?.length === 0 && !liveRun && (
            <div className="card tight"><p className="tiny muted">
              Nothing cut yet. Start the flow on a recording.</p></div>
          )}
          {shownCuts.map((k) => (
            <div key={k.id} className="card tight" style={{ margin: 0 }}>
              <div className="small">
                <b>#{k.long_upload_id} · swing {k.idx + 1}</b>
                {k.produced
                  ? <span className="pill ok small"
                          style={{ marginLeft: 6 }}>produced</span>
                  : <span className="pill small"
                          style={{ marginLeft: 6 }}>no clip</span>}
              </div>
              <div className="tiny muted">
                impact at {k.peak_time_sec?.toFixed?.(2) ?? "—"}s
                {k.ratio != null ? ` · ×${Number(k.ratio).toFixed(1)}` : ""}
                {k.attack_ms != null
                  ? ` · ${Number(k.attack_ms).toFixed(0)}ms attack` : ""}
              </div>
              <div className="tiny muted">
                {k.start_sec?.toFixed?.(1)}s–{k.end_sec?.toFixed?.(1)}s
                {k.trigger_zone != null
                  ? ` · ${zoneName(k.trigger_zone)}` : ""}
              </div>
              {/* LINKS, NOT THUMBNAILS. A pair of 110px tiles of a
                  dark tee box tells the operator nothing they could
                  not read from the numbers above, and costs two video
                  decodes per cut on a column that can hold dozens.
                  The viewer is where a cut is actually watched. */}
              <div className="row" style={{ gap: 6, marginTop: 5,
                                            flexWrap: "wrap" }}>
                <button type="button" className="btn ghost small"
                        disabled={!k.tee_url}
                        onClick={() => setViewer({
                          url: k.tee_url,
                          title: `#${k.long_upload_id} swing ${k.idx + 1}`
                                 + " — tee" })}>
                  tee angle
                </button>
                {k.green_url ? (
                  <button type="button" className="btn ghost small"
                          onClick={() => setViewer({
                            url: k.green_url,
                            title: `#${k.long_upload_id} swing ${k.idx + 1}`
                                   + " — green" })}>
                    green angle
                  </button>
                ) : (
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
        </Column>

        {/* ── 3. DRAFT ─────────────────────────────────────────── */}
        <Column title="Draft produced" unit="clips" count={countOf("draft")}
                blurb="Produced automatically from the audio impact —
                       ascent, then the ball on the green if it is
                       there, then tracer and graphics. Nobody has
                       looked yet.">
          <Working run={liveRun} phase="produce" />
          {drafts === null && <div className="card tight">
            <div className="shimmer" style={{ height: 120 }} /></div>}
          {shownDrafts.length === 0 && !liveRun && (
            <div className="card tight"><p className="tiny muted">
              No drafts yet.</p></div>
          )}
          {shownDrafts.map((c) => (
            <div key={c.id} className="card tight" style={{ margin: 0 }}>
              {c.url && (
                <video src={c.url} controls preload="metadata"
                       poster={c.thumbnail_url || undefined}
                       style={{ width: "100%", borderRadius: 6,
                                background: "#000" }} />
              )}
              <div className="small" style={{ marginTop: 5 }}>
                <b>Hole {c.hole_number}</b>
                {c.cut_idx != null ? (
                  <span className="muted"> · swing {c.cut_idx + 1} of
                    {" "}#{c.long_upload_id}</span>
                ) : null}
              </div>
              <div className="tiny muted">{fmtWhen(c.captured_at)}</div>
              <div className="row" style={{ gap: 6, marginTop: 6,
                                            flexWrap: "wrap" }}>
                {/* The same editor Production opens for a produced
                    clip -- the plot map -- not a second one. */}
                <Link to={`/admin/production?edit=${c.long_upload_id}`
                          + `&clip=${c.id}`}>
                  <button type="button" className="btn ghost small">
                    edit
                  </button>
                </Link>
                <button type="button" className="btn ghost small"
                        disabled={acting === `del-${c.id}`}
                        onClick={() => act(`del-${c.id}`,
                                           api.flowDeleteClip, c.id)}>
                  {acting === `del-${c.id}` ? "deleting…" : "delete"}
                </button>
                <button type="button" className="btn small"
                        disabled={acting === `ok-${c.id}`}
                        onClick={() => act(`ok-${c.id}`, api.flowAccept,
                                           c.id, "admin")}>
                  {acting === `ok-${c.id}` ? "approving…" : "approve"}
                </button>
              </div>
            </div>
          ))}
        </Column>

        {/* ── 4. FINAL ─────────────────────────────────────────── */}
        <Column title="Final produced" unit="clips" count={countOf("final")}
                blurb="Approved. Grouped by the seven minutes around the
                       first swing in each group.">
          {finals === null && <div className="card tight">
            <div className="shimmer" style={{ height: 120 }} /></div>}
          {finals?.length === 0 && (
            <div className="card tight"><p className="tiny muted">
              Nothing approved yet.</p></div>
          )}
          {shownFinals.map((c, i) => {
            const prev = shownFinals[i - 1];
            const newGroup = !prev || prev.group_label !== c.group_label;
            return (
              <div key={c.id}>
                {newGroup && c.group_label && (
                  <div className="small" style={{ margin: "4px 0 6px",
                                                  fontWeight: 600 }}>
                    {c.group_label}
                  </div>
                )}
                <div className="card tight" style={{ margin: 0 }}>
                  {c.url && (
                    <video src={c.url} controls preload="metadata"
                           poster={c.thumbnail_url || undefined}
                           style={{ width: "100%", borderRadius: 6,
                                    background: "#000" }} />
                  )}
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    {fmtWhen(c.captured_at)}
                    {c.broadcast_at && (
                      <span className="pill small"
                            style={{ marginLeft: 6 }}>broadcast</span>
                    )}
                  </div>
                  <button type="button"
                          className={c.broadcast_at ? "btn ghost small"
                            : "btn small"}
                          style={{ marginTop: 6, width: "100%" }}
                          disabled={acting === `b-${c.id}`}
                          onClick={() => act(`b-${c.id}`, api.flowBroadcast,
                                             c.id, !c.broadcast_at)}>
                    {acting === `b-${c.id}` ? "…"
                      : c.broadcast_at ? "un-broadcast" : "Broadcast"}
                  </button>
                </div>
              </div>
            );
          })}
        </Column>
      </div>

      <VideoLightbox
        url={viewer?.url}
        title={viewer?.title}
        startedAt={viewer?.startedAt}
        onClose={() => setViewer(null)}
      />
    </div>
  );
}
