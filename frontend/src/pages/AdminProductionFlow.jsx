import { Fragment, useCallback, useEffect, useRef, useState }
  from "react";
import { Link } from "react-router-dom";

import AudioGatesPanel from "../components/AudioGatesPanel.jsx";
import PlayerIdLink from "../components/PlayerIdLink.jsx";
import { api } from "../api.js";
import { readAdminPassword } from "../adminAuth.js";
import { Brand } from "../components/Brand.jsx";
import { VideoLightbox } from "../components/VideoLightbox.jsx";
// THE SAME WIZARD PRODUCTION OPENS, mounted here. Editing a draft
// used to navigate to Production and open it there, so the operator
// left the page they were working on to change one clip on it.
// THE SAME TWO PANELS PRODUCTION USES. The flow's produce IS
// Production's produce -- one function, `_produce_run` -- so the
// screen that explains it is the same screen, not a second one that
// can drift from it.
import { AscentProduceModal, ClickToPlotModal } from "./AdminProduction.jsx";
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

/*
 * WHICH RECORDING A CARD BELONGS TO, said in every column.
 *
 * The columns are four independent lists and the eye has no way to
 * join them: three cuts and a draft, all from #1915, sitting next to
 * five cuts from #1916 with nothing but a small grey "#1915" on each
 * card to tell them apart. The lineage is the thing the page is for.
 *
 * So every column groups by recording, under the same heading in the
 * same order, and the heading carries the count for THAT recording in
 * THAT column -- "#1915 · 3 cuts" beside "#1915 · 1 draft" is the
 * fan-out, readable across the row.
 */
/*
 * ONE ROW PER RECORDING, and the four columns of that row are what
 * became of it.
 *
 * The columns used to be four independent lists, so a reader joined
 * them by eye: three cuts and a draft from #1915 sat beside five cuts
 * from #1916 with nothing lining them up. The lineage is the whole
 * point of the page, and a table says it without anybody having to
 * read a card.
 */
function HeadCell({ title, unit, count, blurb }) {
  return (
    <div style={{ padding: "0 8px 8px" }}>
      <div className="small"><b>{title}</b>{" "}
        <span className="pill small" style={{ marginLeft: 4 }}>
          {count ?? "—"} {unit}
        </span>
      </div>
      <div className="tiny muted" style={{ marginTop: 2 }}>{blurb}</div>
    </div>
  );
}

/** One cell of a recording's row: its cards, or a visible nothing. */
function Cell({ children, empty, live, center, footer }) {
  const has = Array.isArray(children)
    ? children.some(Boolean) : !!children;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8,
                  // The recording sits against a cell as tall as its
                  // tallest neighbour -- four cuts, say -- and reads
                  // as the heading of the row rather than a card that
                  // happens to be at the top of it.
                  justifyContent: center ? "center" : "flex-start",
                  padding: "8px", borderTop: "1px solid var(--line)",
                  borderLeft: "1px solid var(--line)", minWidth: 0 }}>
      {live}
      {has ? children : (
        <div className="tiny muted" style={{ opacity: 0.6 }}>{empty}</div>
      )}
      {/* OUTSIDE THE EMPTY TEST. A control in this column is not
          content in it: counted as a child, an "add" button would
          make an empty column claim to have something in it, and the
          "no drafts" line -- the thing that tells an empty column
          from one still loading -- would never appear. */}
      {footer}
    </div>
  );
}

function Phases({ phases, total, show }) {
  // HIDDEN UNLESS ASKED FOR. A dozen rows of seconds under every
  // recording is the detail you want twice and read past the rest of
  // the time, and it pushed the thing the row is about off the
  // screen. One switch at the top, off by default.
  if (!show || !phases?.length) return null;
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
              {/* WHAT THE PHASE ACTUALLY DID, which for the audio
                  phase is the gates it ran at. The row carried this
                  all along and nothing rendered it, so "are we
                  cutting with the tee-box preset?" had no answer on
                  the page that was doing the cutting. */}
              {p.detail && (
                <div style={{ opacity: 0.75, marginTop: 1 }}>{p.detail}</div>
              )}
            </td>
            <td style={{ textAlign: "right", fontVariantNumeric:
                         "tabular-nums", whiteSpace: "nowrap",
                         verticalAlign: "top" }}>
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
  // PER RECORDING, AND OFF. One switch at the top turned on a dozen
  // rows of seconds under every row at once, which is not how the
  // question arrives: it is asked of the one run that was slow. Only
  // a run that has finished has any timings to show, so only those
  // rows carry the button.
  const [timingsFor, setTimingsFor] = useState({});
  // WHICH ROW'S GATES ARE OPEN. The same panel the long-upload page
  // uses, mounted here because this is where the cut is STARTED --
  // the thresholds and the audio lag that decide what the next run
  // finds, and which frame it calls the strike, were only reachable
  // from another page, so the answer to "why did it produce f2009"
  // lived somewhere other than the button that produced it.
  const [gatesFor, setGatesFor] = useState({});
  // RECORDINGS THIS PAGE STARTED A WIZARD PRODUCE ON. The clip it
  // writes is a NEW row with no flow stamp, so the columns -- which
  // filter on that stamp -- would not show the draft that was just
  // corrected here. The flow adopts it once the produce finishes,
  // and only for a produce THIS page asked for: a clip produced from
  // the Production page is not this flow's output.
  const adopting = useRef(new Set());
  // THE PRODUCE'S OWN REPORT, for whichever recording was asked
  // about. Fetched on click, not with the list: it is the biggest
  // thing on the row and nobody reads it until they ask.
  const [work, setWork] = useState(null);
  const [note, setNote] = useState(null);
  // ── THE MANUAL PRODUCE IN PROGRESS ────────────────────────────
  //
  // {cutId, swing, uploadId, had} — which cut it is from, which swing
  // that is, and whether a draft of that swing already existed (so
  // "producing" can be told from "re-producing").
  //
  // THE PAGE IS THE ONLY THING THAT KNOWS. The produce runs on a
  // thread under the recording's status key and says nothing about
  // which cut an operator started it from, and without that the cut
  // tile and the draft tile both just change underneath them: the cut
  // appears to vanish and a second draft of the same swing appears.
  const [producingCut, _setProducingCut] = useState(null);
  // ── AND THE SAME THING IN A REF ───────────────────────────────
  //
  // `poll` is a useCallback whose deps do not include this, so the
  // copy of it that is running captured whatever the state was when
  // it was memoised -- which is `null`, because the poll is created
  // long before anybody presses Produce Clip. Read from there, the
  // note would never name the swing and the clear would never fire,
  // leaving the cut greyed out for good.
  //
  // The state drives the render and the ref is what the poll reads.
  // Set through one function so the two cannot drift.
  const producingRef = useRef(null);
  const setProducingCut = useCallback((v) => {
    producingRef.current = v;
    _setProducingCut(v);
  }, []);
  // ── THE TWO FILTERS ───────────────────────────────────────────
  //
  // A day and a tee box. Both are server-side: the list is
  // paginated, so filtering what has already arrived would hide rows
  // from the page and leave the ones it wanted on the next one.
  const [day, setDay] = useState("");
  const [zone, setZone] = useState("");
  const [days, setDays] = useState([]);
  // RECORDINGS LEFT OUT BECAUSE THEIR VIDEO IS GONE, and which ones.
  // A row quietly missing from a list is the same shape of silence as
  // a filter that matched nothing, so the page says so — and a band
  // that still has cuts or drafts can name the reason instead of
  // shrugging "not on this page of recordings".
  const [gone, setGone] = useState({ n: 0, ids: [] });
  // ZONE OPTIONS ACCUMULATE. A page filtered to one zone holds only
  // that zone, so a dropdown rebuilt from it would drop every other
  // option the moment it was used. `zones.js` keeps this Map for the
  // two other pages that filter by zone, for the same reason.
  const zoneSeen = useRef(new Map());
  const [zoneOpts, setZoneOpts] = useState([]);
  // WHICH RECORDING IS ONE CLICK FROM BEING DELETED. Deleting one
  // takes its cuts, its drafts and its source files with it, and
  // nothing on this page is undoable -- so the button arms first and
  // deletes second, and arming one disarms any other.
  const [armed, setArmed] = useState(0);
  // {row, swingPos} while the edit wizard is open on this page.
  const [editing, setEditing] = useState(null);
  const [editErr, setEditErr] = useState(null);
  const [viewer, setViewer] = useState(null);
  const polling = useRef(new Set());

  const load = useCallback(async () => {
    if (!adminPassword) return;
    try {
      // ALL FOUR TAKE THE FILTER. Sent on the raw list alone, the
      // other three columns go on showing every other day -- which is
      // how the page came to say "No recordings" beside a cut card
      // and a draft from a fortnight earlier, with the raw cell
      // explaining "#1896 — not on this page of recordings".
      const _f = { day, zone };
      const [c, r, k, d, f] = await Promise.all([
        api.flowCounts(adminPassword, _f),
        api.flowRaw(adminPassword, { limit: 40, ..._f }),
        api.flowCuts(adminPassword, { limit: 120, ..._f }),
        api.flowClips(adminPassword, { stage: "draft", limit: 60, ..._f }),
        api.flowClips(adminPassword, { stage: "final", limit: 60, ..._f }),
      ]);
      setCounts(c); setRaw(r.raw); setCuts(k.cuts);
      setDrafts(d.clips); setFinals(f.clips);
      setDays(r.days || []);
      setGone({ n: r.hidden_no_source || 0, ids: r.hidden_ids || [] });
      // GROWN, NOT REPLACED. See `zoneSeen` above.
      let _grew = false;
      for (const z of (r.zones || [])) {
        const _k = String(z.zone);
        const _prev = zoneSeen.current.get(_k);
        if (_prev === undefined || (!_prev && z.label)) {
          zoneSeen.current.set(_k, z.label || "");
          _grew = true;
        }
      }
      if (_grew) {
        setZoneOpts(Array.from(zoneSeen.current.entries())
          .sort((a, b) => Number(a[0]) - Number(b[0])));
      }
      setErr(null);
    } catch (e) { setErr(e.message); }
  }, [adminPassword, day, zone]);

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
        api.flowCounts(adminPassword, { day, zone }),
        api.flowCuts(adminPassword, { limit: 120, day, zone }),
        api.flowClips(adminPassword, { stage: "draft", limit: 60,
                                       uploadId, flowOnly: false }),
      ]);
      setCounts(c); setCuts(k.cuts); setLiveDrafts(d.clips);
    } catch { /* a tick that fails is a tick; the next one will do */ }
    // THE FILTER IS A DEPENDENCY. Left out, the tick keeps calling
    // the version of this that closed over the old day, and the
    // columns drift back to everything while a run is going.
  }, [adminPassword, day, zone]);

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
        if (adopting.current.has(uploadId)) {
          adopting.current.delete(uploadId);
          try {
            const _c = await api.flowClaimClips(adminPassword, uploadId);
            // ── WHAT THE RUN ACTUALLY DID, IN WORDS ──────────────
            //
            // The clip the produce writes is a NEW row, so "it
            // worked" and "nothing happened" look identical on a
            // column that has just reloaded. This names the swing,
            // says whether a draft was replaced, and says which
            // older draft was removed for being of the same swing --
            // which is the one thing an operator cannot see, because
            // the tile is already gone by the time they look.
            const _p = producingRef.current;
            const _sup = (_c?.superseded || []).length;
            setNote(
              (_p
                ? `Swing ${_p.swing} of #${uploadId} `
                  + `${_p.had ? "re-produced" : "produced"}.`
                : `#${uploadId} produced.`)
              + (_sup
                ? ` ${_sup} older draft${_sup === 1 ? "" : "s"} of the same `
                  + `swing removed — a swing has one draft.`
                : "")
              + (_c?.claimed
                ? ` ${_c.claimed} clip${_c.claimed === 1 ? "" : "s"} `
                  + `adopted into this flow.`
                : "")
              + (_c && _c.matched === 0 && _c.claimed
                ? " It could not be matched to a cut, so it shows as"
                  + " 'swing unknown' — the cut windows and the clip's"
                  + " own time do not overlap."
                : ""));
          } catch (e) {
            // NOT FATAL AND NOT SILENT. The clip exists either way;
            // without the stamp it simply will not be in this
            // column, and that needs saying rather than looking like
            // the edit having lost it.
            setErr(`the clip was produced but this page could not `
                   + `claim it: ${e.message}`);
          }
        }
        // THE TILE COMES BACK NOW, not before: clearing this any
        // earlier would un-grey the cut and restore the old draft
        // while the new one was still being written.
        if (producingRef.current?.uploadId === uploadId) {
          setProducingCut(null);
        }
        await load();
      } catch {
        polling.current.delete(uploadId);
      }
    };
    setTimeout(tick, 600);
  }, [adminPassword, load, loadLive]);

  // ONE STARTER, TWO DEPTHS. The whole flow, or the cuts and stop.
  // The same status key either way, because it is the same recording
  // -- two keys would let one recording look like it was running
  // twice, and the second to finish would be the one the columns
  // believed.
  async function start(uploadId, cutsOnly = false) {
    // NOT `cut-${id}`: that key already belongs to Produce Clip on a
    // CUT card, which is keyed by the cut's id — and an upload and a
    // cut can share a number, which would grey out an unrelated
    // button in another column.
    const _k = `${cutsOnly ? "cutsonly" : "raw"}-${uploadId}`;
    setActing(_k); setErr(null);
    try {
      const got = await (cutsOnly ? api.flowStartCuts : api.flowStart)(
        adminPassword, uploadId);
      setRuns((s) => ({ ...s, [uploadId]: { ...got, running: true } }));
      poll(uploadId);
    } catch (e) { setErr(e.message); } finally { setActing(null); }
  }

  // OPEN THE WIZARD HERE. It needs the upload row the Production page
  // hands it -- the same shape, from the same builder -- so it is
  // fetched by id rather than rebuilt into something slimmer that
  // would then have to be kept in step with the wizard's needs.
  // ── OPEN THE SAME MAP ON A NEW SWING ─────────────────────────
  //
  // `addNew` points the modal one past the end of the swings array,
  // which is what makes every field blank and makes the save APPEND
  // rather than patch. A recording whose cutter missed a strike -- or
  // whose produce refused one -- had no route to a draft from this
  // page at all: the only way was to find the recording on the
  // Production page and add it there.
  // ── THE PRODUCE'S REASONING, ON DEMAND ──────────────────────
  //
  // A report that is absent is not a failure: a recording produced
  // before the report was kept simply has nothing to show, and the
  // endpoint says which so this can say it too.
  // ── THROW THE WHOLE RECORDING AWAY ────────────────────────────
  //
  // The server refuses when a clip from it has been broadcast, and
  // says which -- a retraction is a decision made on that clip, not a
  // side effect of deleting the thing it came from.
  async function removeRaw(uploadId) {
    setActing(`rm-${uploadId}`);
    setErr(null);
    try {
      const got = await api.flowDeleteRaw(adminPassword, uploadId);
      setArmed(0);
      await load();
      setErr(null);
      // NOT AN ERROR, AND WORTH SAYING. A row vanishing is the only
      // other evidence that anything happened, and on a filtered page
      // it can vanish for the wrong reason.
      setNote(`recording #${uploadId} deleted — ${got.clips || 0} draft(s), `
              + `${got.cuts || 0} cut(s) and `
              + `${Math.round((got.freed_bytes || 0) / 1e6)}MB of source`);
    } catch (e) {
      setErr(e.message);
      setArmed(0);
    } finally {
      setActing(null);
    }
  }

  async function openWork(uploadId) {
    setActing(`work-${uploadId}`);
    setEditErr(null);
    try {
      const got = await api.flowProduceReport(adminPassword, uploadId);
      if (!got?.report) {
        setEditErr(got?.reason || "no produce report for this recording");
        return;
      }
      setWork({ uploadId, report: got.report, at: got.at });
    } catch (e) {
      setEditErr(e.message);
    } finally {
      setActing(null);
    }
  }

  async function openAdder(uploadId, seed = null, key = null) {
    setActing(key || `add-${uploadId}`);
    setEditErr(null);
    try {
      const got = await api.listLongUploads(adminPassword, 1, 0,
                                            { uploadId });
      const row = Array.isArray(got) ? got[0] : null;
      if (!row) throw new Error(`could not load recording #${uploadId}`);
      setEditing({ row, swingPos: null, addNew: true, seed });
    } catch (e) {
      setEditErr(e.message);
      // The wizard never opened, so nothing is being produced.
      setProducingCut(null);
    } finally {
      setActing(null);
    }
  }

  async function openEditor(clip) {
    setActing(`edit-${clip.id}`);
    setEditErr(null);
    try {
      const got = await api.listLongUploads(adminPassword, 1, 0,
                                            { uploadId: clip.long_upload_id });
      // The endpoint returns a bare array. Guessing at two or three
      // possible shapes here would just hide the day it changes.
      const row = Array.isArray(got) ? got[0] : null;
      if (!row) throw new Error("could not load recording "
                                + `#${clip.long_upload_id}`);
      const arr = row.edit_metrics?.swings || [];
      const pos = arr.findIndex((sw) => sw?.clip_id != null
                                && sw.clip_id === clip.id);
      setEditing({ row, swingPos: pos >= 0 ? pos : 0, addNew: false });
    } catch (e) {
      setEditErr(e.message);
    } finally {
      setActing(null);
    }
  }

  async function act(key, fn, ...args) {
    setActing(key); setErr(null);
    try { await fn(adminPassword, ...args); await load(); }
    catch (e) { setErr(e.message); } finally { setActing(null); }
  }

  // ── THE ROWS OF THE TABLE ────────────────────────────────────
  //
  // The recordings, newest first, plus any upload that has cuts or
  // clips but is not on this page of raw rows -- without that, a
  // draft from an older recording would simply vanish from the page
  // rather than appear with a row of its own.
  const rawById = Object.fromEntries((raw || []).map((r) => [r.id, r]));
  const bands = (() => {
    const seen = [];
    const push = (id) => {
      if (id != null && !seen.includes(id)) seen.push(id);
    };
    (raw || []).forEach((r) => push(r.id));
    [cuts, drafts, finals].forEach((rows) =>
      (rows || []).forEach((x) => push(x.long_upload_id)));
    // EVERY BAND, ALWAYS. This used to narrow to one recording, and
    // a page whose whole point is the fan-out from one recording to
    // its cuts and its drafts reads worse one row at a time: the
    // comparison between neighbouring swings is the thing being
    // looked at. Narrowing is what the day and tee-box filters are
    // for, and they say so in the header where a row-level toggle
    // did not.
    return seen;
  })();

  const rawCard = (r, run, busy) => (
    <div className="card tight" style={{ margin: 0 }}>
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
        {r.trigger_zone == null ? "no zone recorded"
          : zoneName(r.trigger_zone, r.trigger_zone_label)}
      </div>
      <div className="row" style={{ gap: 6, marginTop: 5, flexWrap: "wrap" }}>
        <button type="button" className="btn ghost small"
                disabled={!r.tee_url}
                onClick={() => setViewer({ url: r.tee_url,
                                           title: `#${r.id} tee angle`,
                                           startedAt: r.captured_at })}>
          tee angle
        </button>
        <button type="button" className="btn ghost small"
                disabled={!r.green_url}
                title={r.has_green ? "" : "this pair has no green"}
                onClick={() => setViewer({ url: r.green_url,
                                           title: `#${r.id} green angle`,
                                           startedAt: r.captured_at })}>
          green angle
        </button>
      </div>
      <button type="button" className="btn small"
              style={{ marginTop: 6, width: "100%" }}
              disabled={busy || acting === `raw-${r.id}`}
              onClick={() => start(r.id)}>
        {busy ? "running…" : acting === `raw-${r.id}` ? "starting…"
          : "Start production flow"}
      </button>
      {/* ── THE CUTS, AND STOP THERE ───────────────────────────────
          The produce is the expensive half by a wide margin —
          minutes of sky searching per swing against seconds of
          ffmpeg — and it is the half most often done by hand. On a
          recording where the ascent search is going to find nothing
          anyway, waiting for it to fail four times before being
          allowed to plot the swings is the long way round to the
          same place. This fills the cuts column and leaves every cut
          waiting for its own Produce Clip. */}
      <button type="button" className="btn ghost small"
              style={{ marginTop: 5, width: "100%" }}
              disabled={busy || acting === `cutsonly-${r.id}`}
              title={"cut on audio and stop — the same cuts the full "
                     + "flow makes, with no produce after them, so each "
                     + "one can be plotted by hand"}
              onClick={() => start(r.id, true)}>
        {acting === `cutsonly-${r.id}` ? "starting…" : "Audio cuts only"}
      </button>
      {/* Only the runner's OWN lines -- "starting", "queued behind 2
          uploads". Everything tagged with a phase belongs in the cell
          doing that work, which is the point of a row band. */}
      {busy && !phaseOf(run?.stage).phase && (
        <div className="tiny muted" style={{ marginTop: 4 }}>
          {phaseOf(run?.stage).text || "working"}
          {run?.total ? ` · ${run.done ?? 0} of ${run.total}` : ""}
        </div>
      )}
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
            {run.report.cleared?.cuts > 0 && (
              <span className="muted">
                {" "}· replaced {run.report.cleared.cuts} from an
                earlier run
              </span>
            )}
          </div>
          {run.report.reason && (
            <div className="tiny muted" style={{ marginTop: 2 }}>
              {run.report.reason}
            </div>
          )}
          <button type="button" className="btn ghost small"
                  style={{ marginTop: 5, width: "100%" }}
                  onClick={() => setTimingsFor((t) => ({
                    ...t, [r.id]: !t[r.id] }))}>
            {timingsFor[r.id] ? "hide timings" : "show timings"}
          </button>
          <Phases phases={run.report.phases} total={run.report.total_sec}
                  show={!!timingsFor[r.id]} />
        </>
      )}
      {/* THE GATES AND THE LAG, BESIDE THE BUTTON THAT USES THEM.
          Open for whichever recording is being looked at, not for the
          page: these are per tee box, and showing one hole's numbers
          over another hole's row is how a cut gets verified at a
          threshold the cutter would never run at. */}
      {/* ── WHAT THE PRODUCE WAS TRYING TO DO ──────────────────────
          The flow's phase 3 and Production's "Produce from ascents"
          are the same function; only the key the live progress is
          filed under differs, and that is what decided whether the
          reasoning was shown or thrown away. Kept on the row now, so
          this opens the same panel afterwards. */}
      {r.produce_report_at && (
        <button type="button" className="btn ghost small"
                style={{ marginTop: 5, width: "100%" }}
                disabled={acting === `work-${r.id}`}
                title={"the four steps of the produce, what each looked "
                       + "at and the verdict on every candidate — the "
                       + "same panel Production's Produce from ascents "
                       + "shows"}
                onClick={() => openWork(r.id)}>
          {acting === `work-${r.id}` ? "reading…" : "show work"}
        </button>
      )}
      <button type="button" className="btn ghost small"
              style={{ marginTop: 5, width: "100%" }}
              title={"the thresholds and the audio lag the next run of "
                     + "this recording will use"}
              onClick={() => setGatesFor((t) => ({
                ...t, [r.id]: !t[r.id] }))}>
        {gatesFor[r.id] ? "hide gates & lag" : "gates & lag"}
      </button>
      {gatesFor[r.id] && (
        <AudioGatesPanel uploadId={r.id} adminPassword={adminPassword} />
      )}
      {/* ── AND THE WAY OUT ────────────────────────────────────────
          Armed first, because this takes the cuts, the drafts and
          the source files with it and nothing here is undoable. The
          armed state says exactly what is about to go. */}
      {armed === r.id ? (
        <div className="tiny" style={{ marginTop: 5, padding: "4px 6px",
                                       borderRadius: 4,
                                       background: "var(--warn-bg, #fdf3d7)" }}>
          <b>Delete #{r.id}?</b> This removes {nCuts[r.id] || 0} cut
          {(nCuts[r.id] || 0) === 1 ? "" : "s"}, {nDrafts[r.id] || 0} draft
          {(nDrafts[r.id] || 0) === 1 ? "" : "s"} and both source videos.
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <button type="button" className="btn small"
                    disabled={acting === `rm-${r.id}`}
                    onClick={() => removeRaw(r.id)}>
              {acting === `rm-${r.id}` ? "deleting…" : "yes, delete"}
            </button>
            <button type="button" className="btn ghost small"
                    onClick={() => setArmed(0)}>
              cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn ghost small"
                style={{ marginTop: 5, width: "100%" }}
                disabled={busy}
                title={busy ? "a run is going on this recording"
                  : "delete this recording, its cuts and its drafts"}
                onClick={() => setArmed(r.id)}>
          delete recording
        </button>
      )}
      {/* WHAT THIS RECORDING SPAWNED. A line, not a button: it used
          to narrow the page to this one recording, which is the
          opposite of what the page is for. The numbers were always
          the useful half of it. */}
      {(nCuts[r.id] || nDrafts[r.id] || nFinals[r.id]) ? (
        <div className="tiny muted" style={{ marginTop: 6 }}>
          → {nCuts[r.id] || 0} cut{(nCuts[r.id] || 0) === 1 ? "" : "s"}
          {" · "}{nDrafts[r.id] || 0} draft
          {(nDrafts[r.id] || 0) === 1 ? "" : "s"}
          {(nFinals[r.id] || 0) > 0 && ` · ${nFinals[r.id]} final`}
        </div>
      ) : null}
    </div>
  );

  const cutCard = (k) => {
    // ── IT NEVER GOES AWAY WHILE IT IS BEING PRODUCED FROM ───────
    //
    // The cut is the thing the operator is working ON. Watching it
    // disappear mid-produce is the worst reading available: it looks
    // like the produce consumed it, and when the column settles
    // afterwards there is no way to tell what happened.
    const making = producingCut?.cutId === k.id;
    return (
    <div key={k.id} className="card tight"
         style={{ margin: 0, opacity: making ? 0.55 : 1 }}>
      {making && (
        <div className="tiny" style={{ marginBottom: 4, padding: "3px 6px",
                                       borderRadius: 4,
                                       background: "var(--warn-bg, #fdf3d7)",
                                       opacity: 1 }}>
          <b>producing swing {k.idx + 1}…</b>{" "}
          <span className="muted">
            this cut stays exactly as it is — the draft is being made
            from it, not out of it
          </span>
        </div>
      )}
      <div className="small">
        <b>swing {k.idx + 1}</b>
        {/* WHICH CUTS BECAME CLIPS is the question this column
            exists to answer, and the row band answers "whose". */}
        {k.produced
          ? <span className="pill ok small"
                  style={{ marginLeft: 6 }}>→ draft</span>
          : <span className="pill small"
                  style={{ marginLeft: 6 }}>no clip</span>}
      </div>
      <div className="tiny muted">
        {/* THE PEAK, NOT THE IMPACT. This said "impact at 33.48s",
            which is the microphone's peak -- the strike plus whatever
            the mic, the encoder and the ring buffer add. Calling it
            the impact is how a lag of zero goes unnoticed. */}
        peak at {k.peak_time_sec?.toFixed?.(2) ?? "—"}s
        {k.ratio != null ? ` · ×${Number(k.ratio).toFixed(1)}` : ""}
        {k.attack_ms != null
          ? ` · ${Number(k.attack_ms).toFixed(0)}ms attack` : ""}
      </div>
      {/* ── AND THE SAME THING IN FRAMES ───────────────────────────
          The cut is recorded in seconds and every produce question is
          about frames, so "the cut is at 33.48s" and "it produced
          f2009" could not be compared without knowing this
          recording's frame rate and this hole's lag by heart. Both
          are on the row now, as they were when the cut was made. */}
      {k.peak_frame != null && (
        <div className="tiny">
          <span className="muted">f{k.peak_frame}</span>
          {k.fps ? <span className="muted"> @ {k.fps}fps</span> : null}
          {k.lag_frames ? (
            <span className="muted">
              {" "}· less {Number(k.lag_sec).toFixed(2)}s lag
              {" "}({k.lag_frames}f) →{" "}
              <b>strike f{k.impact_frame}</b>
            </span>
          ) : (
            <span className="muted">
              {" "}· lag 0.00s, so the strike is taken as{" "}
              <b>f{k.impact_frame}</b>
            </span>
          )}
        </div>
      )}
      <div className="tiny muted">
        {k.start_sec?.toFixed?.(1)}s–{k.end_sec?.toFixed?.(1)}s
        {k.trigger_zone != null
          ? ` · ${zoneName(k.trigger_zone, k.trigger_zone_label)}` : ""}
      </div>
      {/* LINKS, NOT THUMBNAILS: two 110px tiles of a dark tee box
          tell an operator nothing the numbers above do not, and cost
          two video decodes per cut. */}
      <div className="row" style={{ gap: 6, marginTop: 5, flexWrap: "wrap" }}>
        <button type="button" className="btn ghost small"
                disabled={!k.tee_url}
                onClick={() => setViewer({
                  url: k.tee_url,
                  title: `#${k.long_upload_id} swing ${k.idx + 1} — tee` })}>
          tee angle
        </button>
        {k.green_url && (
          <button type="button" className="btn ghost small"
                  onClick={() => setViewer({
                    url: k.green_url,
                    title: `#${k.long_upload_id} swing ${k.idx + 1}`
                           + " — green" })}>
            green angle
          </button>
        )}
      </div>
      {/* ── WHY THERE IS NO FOOTAGE, WHERE THERE IS NONE ───────────
          A disabled button is not an answer, and there are four
          reasons with four different next moves: the cuts are
          scratch and are swept after three weeks, the pair may have
          had no green half, the cut may have failed, or the file may
          be gone from under a row that still names it. The button
          looked identical for all four. */}
      {(k.tee_why || k.green_why) && (
        <div className="tiny muted" style={{ marginTop: 4 }}>
          {k.tee_why && <div>no tee angle: {k.tee_why}</div>}
          {k.green_why && <div>no green angle: {k.green_why}</div>}
        </div>
      )}
      {/* ── A CUT WITH NO CLIP IS THE INTERESTING ONE ──────────────
          Either the producer failed on a real swing or the sound was
          never a swing, and the only way to settle it was to re-run
          the whole flow. This opens the add wizard on the recording
          with this cut's own impact frame already set, so the map
          opens on the strike the microphone heard rather than on
          frame zero of a three-minute recording. */}
      {!k.produced && (
        <button type="button" className="btn small"
                style={{ marginTop: 5, width: "100%" }}
                disabled={acting === `cut-${k.id}`}
                title={"plot this swing by hand and produce a draft of "
                       + "it, starting from the frame the strike was "
                       + "heard on"}
                onClick={() => { setProducingCut({
                  cutId: k.id, swing: k.idx + 1,
                  uploadId: k.long_upload_id,
                  // A DRAFT OF THIS SWING ALREADY? Then this is a
                  // RE-produce, the old tile is about to be replaced,
                  // and saying so is the difference between a tile
                  // disappearing and a tile being superseded.
                  had: (drafts || []).some(
                    (c) => c.long_upload_id === k.long_upload_id
                      && c.cut_idx === k.idx),
                }); openAdder(
                  k.long_upload_id,
                  // ── THE CUT'S OWN WINDOW, NOT THE WHOLE VIDEO ──
                  //
                  // `start_frame`/`end_frame` bound the wizard's
                  // frame stepper, so it opens on the twelve seconds
                  // this cut is of instead of handing the operator a
                  // three-minute recording — "1–9239" in the frame
                  // box — and asking them to find a strike the
                  // microphone had already located.
                  //
                  // In the RECORDING's frame numbers: the produce
                  // reads the full-resolution source and every number
                  // on the swing record indexes into it.
                  (k.impact_frame != null || k.start_frame != null)
                    ? { impact_frame: k.impact_frame,
                        start_frame: k.start_frame,
                        end_frame: k.end_frame }
                    : null,
                  `cut-${k.id}`); }}>
          {acting === `cut-${k.id}` ? "opening…" : "Produce Clip"}
        </button>
      )}
      {k.produce_error && (
        <div className="tiny err-text" style={{ marginTop: 4 }}>
          {k.produce_error}
        </div>
      )}
    </div>
    );
  };

  const draftCard = (c) => (
    <div key={c.id} className="card tight" style={{ margin: 0 }}>
      <div className="small">
        {/* THE SWING FIRST: it is what joins this card to the cut in
            the cell to its left. */}
        <b>{c.cut_idx != null ? `swing ${c.cut_idx + 1}` : "swing unknown"}</b>
        <span className="muted"> · Hole {c.hole_number}</span>
        {c.cut_idx == null && (
          <span className="muted tiny">
            {" "}· could not be matched to a cut
          </span>
        )}
      </div>
      <div className="tiny muted">{fmtWhen(c.captured_at)}</div>
      {/* WHICH FRAMES THIS WAS CUT FROM, on the card. Otherwise the
          only way to see that a clip was cut around another swing's
          ascent is to open the wizard and read a scrubber against a
          panel -- which is how it went unnoticed through several
          runs. */}
      {/* A LINK, LIKE THE TEE AND GREEN ANGLES BESIDE IT. An inline
          <video> per draft is a decode and a poster fetch per card,
          four cards to a recording, and it told an operator nothing
          the numbers under it do not -- the question at this column
          is "is this the right swing", and that is answered by the
          frames and the impact, not by a 110px still of a tee box. */}
      <div className="row" style={{ gap: 6, marginTop: 5,
                                    flexWrap: "wrap" }}>
        <button type="button" className="btn ghost small"
                disabled={!c.url}
                onClick={() => setViewer({
                  url: c.url,
                  title: `#${c.long_upload_id} ${c.cut_idx != null
                    ? `swing ${c.cut_idx + 1}` : "draft"} — draft`,
                })}>
          Draft Production
        </button>
      </div>
      {c.cut_frames && (
        <div className="tiny muted" style={{ marginTop: 4 }}>
          cut f{c.cut_frames[0]}–f{c.cut_frames[1]}
          {c.impact_frame != null && <> · impact f{c.impact_frame}</>}
        </div>
      )}
      {/* ── WHOSE FRAME THAT IS ────────────────────────────────────
          "It identified impact frame 2009 instead of 1920" is only
          answerable next to what the AUDIO said. When the two agree
          there is nothing to say and this stays quiet; when they
          differ the gap is the whole story of the clip, so it is
          spelled out in frames, in seconds, and with where the
          produce's number came from. */}
      {c.impact_differs ? (
        <div className="tiny" style={{ marginTop: 2 }}>
          <span className="pill small">impact moved</span>{" "}
          <span className="muted">{c.impact_differs}</span>
        </div>
      ) : c.audio_impact_frame != null && c.impact_frame != null ? (
        <div className="tiny muted" style={{ marginTop: 2 }}>
          the audio put the strike at f{c.audio_impact_frame} too
          {c.audio_peak_sec != null && (
            <> (peak {Number(c.audio_peak_sec).toFixed(2)}s
              {" "}less {Number(c.audio_lag_sec ?? 0).toFixed(2)}s lag)</>
          )}
        </div>
      ) : null}
      {/* AND HOW IT WAS ARRIVED AT, agreement or not: a frame measured
          off the ball leaving its spot and one subtracted from a peak
          are different kinds of evidence and looked identical. */}
      {c.impact_from && (
        <div className="tiny muted" style={{ marginTop: 2 }}>
          {c.impact_from}
        </div>
      )}
      {c.no_tracer && (
        <div className="tiny" style={{ marginTop: 2 }}>
          <span className="pill small">no tracer</span>{" "}
          <span className="muted">{c.no_tracer} — the clip is cut from
            the audio impact; plot the ascent by hand to draw it.</span>
        </div>
      )}
      {c.frames_disagree && (
        <div className="tiny err-text" style={{ marginTop: 2 }}>
          {c.frames_disagree}
        </div>
      )}
      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
        {/* The same editor Production opens for a produced clip. */}
        {/* OPENS HERE. No navigation: the operator came to change one
            clip on this page and should still be on it afterwards. */}
        <button type="button" className="btn ghost small"
                disabled={acting === `edit-${c.id}`}
                onClick={() => openEditor(c)}>
          {acting === `edit-${c.id}` ? "opening…" : "edit"}
        </button>
        <button type="button" className="btn ghost small"
                disabled={acting === `del-${c.id}`}
                onClick={() => act(`del-${c.id}`, api.flowDeleteClip, c.id)}>
          {acting === `del-${c.id}` ? "deleting…" : "delete"}
        </button>
        <button type="button" className="btn small"
                disabled={acting === `ok-${c.id}`}
                onClick={() => act(`ok-${c.id}`, api.flowAccept, c.id,
                                   "admin")}>
          {acting === `ok-${c.id}` ? "approving…" : "approve"}
        </button>
      </div>
    </div>
  );

  const finalCard = (c, newGroup) => (
    <div key={c.id}>
      {newGroup && c.group_label && (
        <div className="small" style={{ margin: "2px 0 6px",
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
          {c.cut_idx != null ? `swing ${c.cut_idx + 1} · ` : ""}
          {fmtWhen(c.captured_at)}
          {c.broadcast_at && (
            <span className="pill small" style={{ marginLeft: 6 }}>
              broadcast
            </span>
          )}
        </div>
        <button type="button"
                className={c.broadcast_at ? "btn ghost small" : "btn small"}
                style={{ marginTop: 6, width: "100%" }}
                disabled={acting === `b-${c.id}`}
                onClick={() => act(`b-${c.id}`, api.flowBroadcast, c.id,
                                   !c.broadcast_at)}>
          {acting === `b-${c.id}` ? "…"
            : c.broadcast_at ? "un-broadcast" : "Broadcast"}
        </button>
      </div>
    </div>
  );

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
  // The run the columns are narrating: whichever one is going.
  const liveId = Number(
    Object.keys(runs).find((id) => runs[id]?.running) || 0);
  const liveRun = liveId ? runs[liveId] : null;

  // WHAT EACH RECORDING SPAWNED, counted once for every column that
  // needs it. This is the fan-out the page exists to show: one
  // recording, three cuts, one of which became a clip.
  const tally = (rows, idOf) => {
    const m = {};
    (rows || []).forEach((r) => {
      const id = idOf(r);
      m[id] = (m[id] || 0) + 1;
    });
    return m;
  };
  const nCuts = tally(cuts, (k) => k.long_upload_id);
  const nDrafts = tally(drafts, (c) => c.long_upload_id);
  const nFinals = tally(finals, (c) => c.long_upload_id);

  const shownCuts = cuts || [];
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
  const shownDrafts = draftSource || [];
  const shownFinals = finals || [];

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
              · one row per recording · only what this flow made
            </span>
          </div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            {/* ── THE DAY AND THE TEE BOX ────────────────────────────
                Both named the way the operator reads them: the day in
                the course's own clock, and the zone by the name they
                typed on the box rather than the index the rig counts
                it by. */}
            <label className="tiny muted" style={{ display: "inline-flex",
                                                   gap: 4,
                                                   alignItems: "center" }}>
              day
              <select value={day} className="small"
                      onChange={(e) => setDay(e.target.value)}>
                <option value="">all days</option>
                {days.map((d) => (
                  <option key={d.day} value={d.day}>
                    {d.day} ({d.n})
                  </option>
                ))}
              </select>
            </label>
            <label className="tiny muted" style={{ display: "inline-flex",
                                                   gap: 4,
                                                   alignItems: "center" }}>
              tee box
              <select value={zone} className="small"
                      onChange={(e) => setZone(e.target.value)}>
                <option value="">all tee boxes</option>
                {zoneOpts.map(([z, label]) => (
                  <option key={z} value={z}>
                    {zoneName(z, label)}
                  </option>
                ))}
              </select>
            </label>
            {gone.n > 0 && (
              <span className="tiny muted"
                    title={"a recording with no video can neither be cut "
                           + "nor produced, and has no angle to open — "
                           + "the disk here is wiped on every deploy, so "
                           + "this is checked against object storage too"}>
                {gone.n} recording{gone.n === 1 ? "" : "s"} hidden — no
                {" "}source video
              </span>
            )}
            {(day || zone !== "") && (
              <button type="button" className="btn ghost small"
                      onClick={() => { setDay(""); setZone(""); }}>
                clear filters
              </button>
            )}
            <button type="button" className="btn ghost small" onClick={load}>
              refresh
            </button>
          </div>
        </div>
      </div>

      {err && <div className="card err-text small">{err}</div>}

      {/* ── THE TABLE: ONE ROW PER RECORDING ──────────────────
          Four cells across, so what a recording became is read
          along its own row instead of reconstructed from four
          lists. Cell borders make the band, which is the only
          thing that actually separates #1915's cuts from
          #1916's when both have several. */}
      <div style={{ display: "grid", alignItems: "stretch",
                    gridTemplateColumns:
                      "minmax(230px, 1fr) minmax(210px, 1fr) "
                      + "minmax(230px, 1.1fr) minmax(210px, 1fr)",
                    borderRight: "1px solid var(--line)",
                    borderBottom: "1px solid var(--line)" }}>
        <HeadCell title="Raw clips" unit="recordings" count={countOf("raw")}
                  blurb="Both angles off the camera pair. Open either in
                         the console's viewer, then start the flow." />
        <HeadCell title="Audio cuts" unit="cuts" count={countOf("cuts")}
                  blurb="What the microphone heard, both angles. No
                         swing judgement — only that it cleared this
                         tee box's gates." />
        <HeadCell title="Draft produced" unit="clips"
                  count={countOf("draft")}
                  blurb="Produced from the audio impact — ascent, the
                         ball on the green if it is there, then tracer
                         and graphics." />
        <HeadCell title="Final produced" unit="clips"
                  count={countOf("final")}
                  blurb="Approved, and grouped by the seven minutes
                         around the first swing in each group." />

        {raw === null && (
          <div style={{ gridColumn: "1 / -1", padding: 10 }}>
            <div className="shimmer" style={{ height: 90 }} />
          </div>
        )}
        {raw?.length === 0 && (
          <div style={{ gridColumn: "1 / -1", padding: 10 }}
               className="tiny muted">No recordings.</div>
        )}

        {bands.map((uid) => {
          const r = rawById[uid];
          const run = runs[uid];
          const busy = !!run?.running;
          const mine = (rows) => (rows || [])
            .filter((x) => x.long_upload_id === uid);
          const myFinals = mine(shownFinals);
          return (
            <Fragment key={uid}>
              {/* 1. the recording */}
              <Cell center empty="this recording is gone from the list">
                {r ? rawCard(r, run, busy) : (
                  <div className="tiny muted">
                    <b>#{uid}</b>
                    {/* WHY IT IS NOT HERE, where that is knowable. A
                        recording whose video has been swept still has
                        its cuts and its drafts, and those are worth
                        keeping on screen — but "not on this page"
                        reads like paging, which is a different
                        problem with a different fix. */}
                    {gone.ids.includes(uid)
                      ? " — its source video is gone; the cuts and "
                        + "drafts it made are still here"
                      : " — not on this page of recordings"}
                  </div>
                )}
              </Cell>

              {/* 2. its cuts */}
              <Cell empty="no cuts"
                    live={busy ? (
                      <>
                        <Working run={run} phase="audio" />
                        <Working run={run} phase="cut" />
                      </>
                    ) : null}>
                {mine(shownCuts).map(cutCard)}
              </Cell>

              {/* 3. its drafts */}
              <Cell empty="no drafts"
                    live={(() => {
                      const _p = producingCut?.uploadId === uid
                        ? producingCut : null;
                      return (
                        <>
                          {/* ── THE SWING BEING MADE, BY NAME ──────
                              The tile for it is pulled out below
                              while this runs, because for a
                              re-produce the one on screen is about
                              to be replaced and leaving it there
                              means watching a stale draft sit under
                              a progress bar. Saying WHICH swing, and
                              whether this replaces one, is the whole
                              difference between that and a tile
                              quietly vanishing. */}
                          {_p && (
                            <div className="tiny" style={{
                              padding: "4px 6px", borderRadius: 4,
                              background: "var(--warn-bg, #fdf3d7)" }}>
                              <b>
                                Swing {_p.swing} is
                                {_p.had ? " re-producing" : " producing"}…
                              </b>
                              <div className="muted">
                                {_p.had
                                  ? "the draft that was here is still on "
                                    + "disk and is replaced only when this "
                                    + "one succeeds — there is never more "
                                    + "than one draft of a swing"
                                  : "plotted by hand from the audio cut "
                                    + "beside it"}
                              </div>
                            </div>
                          )}
                          {busy
                            ? <Working run={run} phase="produce" /> : null}
                        </>
                      );
                    })()}
                    footer={r ? (
                      <button type="button" className="btn ghost small"
                              disabled={acting === `add-${uid}` || busy}
                              title={"plot a swing the cutter missed, or "
                                     + "one the produce refused, and make "
                                     + "a draft of it"}
                              onClick={() => openAdder(uid)}>
                        {acting === `add-${uid}`
                          ? "opening…" : "+ add a swing"}
                      </button>
                    ) : null}>
                {/* ONE TILE PER SWING. The one being re-produced is
                    pulled out while that runs, so the column never
                    shows the old draft and the new one together. */}
                {mine(shownDrafts)
                  .filter((c) => !(producingCut
                    && producingCut.uploadId === uid
                    && c.cut_idx != null
                    && c.cut_idx + 1 === producingCut.swing))
                  .map(draftCard)}
              </Cell>

              {/* 4. and what was approved */}
              <Cell empty="nothing approved">
                {myFinals.map((c, n) => finalCard(
                  c, n === 0 || myFinals[n - 1].group_label
                    !== c.group_label))}
              </Cell>
            </Fragment>
          );
        })}
      </div>

      {note && (
        <div className="card tight small" style={{ marginTop: 6 }}>
          {note}{" "}
          <button type="button" className="btn ghost small"
                  onClick={() => setNote(null)}>dismiss</button>
        </div>
      )}
      {editErr && <div className="card err-text small">{editErr}</div>}
      {editing && (
        <ClickToPlotModal
          row={editing.row}
          swingPos={editing.swingPos}
          addNew={!!editing.addNew}
          // WHAT THE CUT ALREADY KNEW. Only read for a new swing --
          // see `seed` in the modal.
          seed={editing.seed || null}
          adminPassword={adminPassword}
          // Cancel and save both land here, and both reload the
          // columns: the point of editing a draft is that the draft
          // changes.
          //
          // AND THE SAVE IS NOT THE RENDER. `wizardProduce` returns
          // the moment the job is QUEUED -- the clip takes a minute on
          // a thread -- so `load()` on its own read the draft that was
          // still being replaced and then never asked again. The
          // corrected clip only appeared on a manual page reload,
          // which is indistinguishable from the edit having done
          // nothing. `poll` watches the produce and reloads the
          // columns when it stops.
          // CANCELLED. Nothing was produced, so nothing is
          // producing -- without this the cut stays greyed out and
          // the drafts column goes on announcing a swing that is not
          // being made, for as long as the page is open.
          onClose={() => {
            setEditing(null);
            setProducingCut(null);
            load();
          }}
          onBackground={() => {
            const _id = editing.row?.id;
            setEditing(null);
            load();
            if (_id) { adopting.current.add(_id); poll(_id); }
          }}
          onDone={(ok, msg) => {
            const _id = editing.row?.id;
            setEditing(null);
            // THE MODAL IS GONE, so a save that was REFUSED has
            // nowhere to say so. It reported through these arguments
            // all along and this page threw them away, so a rejected
            // plot looked identical to one that had been accepted and
            // was still rendering.
            if (ok === false) {
              setEditErr(msg || "the produce was refused");
              setProducingCut(null);
              load();
              return;
            }
            load();
            if (_id) { adopting.current.add(_id); poll(_id); }
          }}
        />
      )}

      {work && (
        <AscentProduceModal
          // NOT RUNNING. The panel reads `state.running` to decide
          // whether to narrate progress or show a finished report,
          // and this is always the latter -- the run ended before
          // anybody pressed the button.
          state={{ running: false, uploadId: work.uploadId,
                   report: work.report, error: null }}
          adminPassword={adminPassword}
          onClose={() => setWork(null)}
        />
      )}

      <VideoLightbox
        url={viewer?.url}
        title={viewer?.title}
        startedAt={viewer?.startedAt}
        onClose={() => setViewer(null)}
      />
    </div>
  );
}
