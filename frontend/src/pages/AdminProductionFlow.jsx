import { Fragment, useCallback, useEffect, useRef, useState }
  from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

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
 * The production flow, a page at a time: one recording becomes
 * drafts.
 *
 *   raw     a recording off a camera pair, both angles openable
 *   cuts    what the microphone heard, cut to size, nothing judged
 *   draft   produced automatically, nobody has looked
 *   final   somebody looked and approved it
 *
 * ONE STAGE PER PAGE, IN ORDER, with a stepper across the top.
 *
 * This was four columns side by side. The reasoning there was that
 * the operator's question is "what happened to THIS recording" and
 * the answer is spread across all four, which tabs made them hold in
 * their head across four screens. That reasoning was right about
 * tabs and wrong about columns: the four stages are never the same
 * size -- five hundred recordings against twenty-nine cuts against
 * fifteen drafts -- so in practice it was one tall column beside
 * three stripes of white, each a quarter as wide as the cards in it
 * needed.
 *
 * THE JOIN DID NOT GO AWAY WITH THE COLUMNS. Every stage after the
 * first groups its cards under the recording they came from, with
 * that recording's count in the heading, so the fan-out still reads
 * -- it reads down the page now instead of across it. And the
 * stepper says the two things tabs never did: where this stage sits
 * in the sequence, and how much is waiting in the other three. A run
 * going on a stage that is not on screen says so in a banner with a
 * button that goes there, because a stage at a time is the one layout
 * where work can be happening somewhere the operator cannot see.
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
// HOW LONG A WATCH WILL WAIT FOR THE WORK TO BEGIN. The wizard hands
// the run over before it has made the call that starts it -- there is
// a tracer re-render and a finalize in between -- so a watch that
// treated the first idle status as "finished" announced the produce
// done before it had started. See `poll`.
const START_GRACE_MS = 30000;
// HOW OFTEN THE PAGE ASKS WHAT EVERYONE ELSE IS DOING. Slower than a
// watch on this page's own run -- it is one call for the whole site
// and nobody is staring at it waiting for a number to move.
const ACTIVE_MS = 5000;

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
 * THE HEADING OF A STAGE: its name, how much is in it across the
 * whole filter, and what it is. The count is the server's, not the
 * length of what is on screen -- "Raw clips · 532 recordings" with
 * fifty cards under it is one page of five hundred, and saying 50
 * there would hide that.
 */
function HeadCell({ n, title, unit, count, blurb, on, onPick }) {
  return (
    <button type="button"
            onClick={onPick}
            aria-current={on ? "true" : undefined}
            title={on ? `${title} is the stage being worked on`
              : `mark ${title} as the stage being worked on`}
            style={{ padding: "8px", textAlign: "left", minWidth: 0,
                     border: "none", borderLeft: "1px solid var(--line)",
                     borderTop: on ? "3px solid var(--accent, #1a56db)"
                       : "3px solid transparent",
                     background: on ? "var(--surface-alt, rgba(0,0,0,0.03))"
                       : "transparent",
                     cursor: "pointer", font: "inherit", color: "inherit",
                     display: "block", width: "100%" }}>
      <div className="small">
        {n != null && <span className="muted">{n}. </span>}
        <b>{title}</b>{" "}
        <span className="pill small" style={{ marginLeft: 4 }}>
          {count ?? "—"} {unit}
        </span>
      </div>
      {/* ON EVERY HEADING, not just the picked one: the columns are
          all the same width now, so there is the same room in each,
          and what a column holds should not need a click to say. */}
      <div className="tiny muted" style={{ marginTop: 2 }}>{blurb}</div>
    </button>
  );
}

/*
 * ONE CELL OF A ROW BAND. The columns are back because the LINEAGE is
 * the thing the page is for: which cuts came from which recording,
 * which of those became a draft. A stage at a time cannot show that
 * -- it shows one stage of everything, and the join has to be
 * reconstructed by reading headings.
 *
 * AND EVERY COLUMN IS THE SAME, always: same width, same weight.
 * Resizing the picked column and shrinking the other three moved the
 * whole page every time a stage was picked, which is harder to read
 * past than a narrow column is to read. The stage strip marks where
 * the work is; it does not rearrange the table to say so.
 */
function Cell({ children, empty, live, center, footer }) {
  const has = Array.isArray(children)
    ? children.some(Boolean) : !!children;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8,
                  // The recording sits against a cell as tall as its
                  // tallest neighbour, so it reads as the heading of
                  // the row rather than a card at the top of it.
                  justifyContent: center ? "center" : "flex-start",
                  padding: "8px", borderTop: "1px solid var(--line)",
                  borderLeft: "1px solid var(--line)",
                  minWidth: 0 }}>
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

/*
 * THE FOUR STAGES, IN ORDER. The titles and the blurbs are the ones
 * that were on the four column headings, word for word: the operator
 * has been reading "Audio cuts" and "Draft produced" for months and
 * the layout changing is no reason for the labels to. See the top of
 * the file for why they are pages now.
 */
const STEPS = [
  { key: "raw", title: "Raw clips", unit: "recordings",
    blurb: "Both angles off the camera pair. Open either in the "
           + "console's viewer, then start the flow." },
  { key: "cuts", title: "Audio cuts", unit: "cuts",
    blurb: "What the microphone heard, both angles. No swing "
           + "judgement — only that it cleared this tee box's gates." },
  { key: "draft", title: "Draft produced", unit: "clips",
    blurb: "Produced from the audio impact — ascent, the ball on the "
           + "green if it is there, then tracer and graphics." },
  { key: "final", title: "Final produced", unit: "clips",
    blurb: "Approved, and grouped by the seven minutes around the "
           + "first swing in each group." },
];

/*
 * WHICH STAGE A RUNNING PHASE IS HAPPENING ON, so a run the operator
 * started on one page can still be seen from another. Untagged runner
 * lines ("queued behind 2 uploads") belong to the recording's own
 * card, which lives on stage 0.
 */
const PHASE_STEP = { audio: 1, cut: 1, produce: 2 };

/*
 * HOW WIDE EACH COLUMN IS: a quarter, all four, always.
 *
 * A single stage at full width loses the lineage, which is the one
 * thing a column view is for -- so all four stay. And picking one
 * changes nothing about the layout: widening it and shrinking its
 * neighbours reflowed every card on the page on each click, which is
 * worse to read past than a quarter-width column is to read.
 *
 * ONE WIDTH, WHATEVER IS PICKED. Four equal quarters, every row the
 * same, so a column is where it was the last time it was looked at.
 *
 * `minmax(0, …)` ON EVERY TRACK, because a grid column's default
 * minimum is its content, not zero -- so one long filename or an
 * unbroken URL in a cell would push its track past a quarter and
 * pull every row out of line with the headings.
 */
const GRID = "minmax(0, 1fr) minmax(0, 1fr) "
  + "minmax(0, 1fr) minmax(0, 1fr)";

/** Where in the sequence this is, and what is waiting in the rest. */
function Stepper({ step, onStep, countOf }) {
  return (
    <div className="card tight"
         style={{ display: "flex", flexWrap: "wrap", gap: 6,
                  alignItems: "center" }}
         title="where the work is -- every column stays on screen">
      {STEPS.map((s, i) => (
        <Fragment key={s.key}>
          {i > 0 && <span className="tiny muted">→</span>}
          <button type="button"
                  className={`btn ${i === step ? "" : "ghost"} small`}
                  aria-current={i === step ? "true" : undefined}
                  title={i === step ? `${s.title} is the stage being `
                    + "worked on" : `mark ${s.title}`}
                  onClick={() => onStep(i)}>
            <span className="tiny muted">{i + 1}.</span> {s.title}{" "}
            <span className="pill small">{countOf(s.key) ?? "—"}</span>
          </button>
        </Fragment>
      ))}
    </div>
  );
}

/*
 * THE RECORDING A GROUP OF CARDS CAME FROM, on every stage but the
 * first. This is the join the columns used to make with cell borders:
 * without it, three cuts from #1915 and five from #1916 are one list
 * of eight.
 */
function BandHead({ uid, r, n, unit, missing }) {
  return (
    <div className="small" style={{ borderTop: "1px solid var(--line)",
                                    padding: "8px 8px 2px",
                                    display: "flex", flexWrap: "wrap",
                                    gap: 6, alignItems: "baseline" }}>
      <b>#{uid}</b>
      {n != null && (
        <span className="pill small">{n} {unit}</span>
      )}
      {r ? (
        <span className="muted tiny">
          {r.hole_number ? `Hole ${r.hole_number} · ` : ""}
          {r.course_name || `course #${r.course_id}`}
          {r.captured_at ? ` · ${fmtWhen(r.captured_at)}` : ""}
        </span>
      ) : (
        <span className="muted tiny">
          {missing
            ? "its source video is gone; what it made is still here"
            : "not on this page of recordings"}
        </span>
      )}
    </div>
  );
}

/** The cards of one group, laid out across the page's full width. */
function BandCards({ children }) {
  return (
    <div style={{ display: "grid", gap: 8, padding: "6px 8px 10px",
                  gridTemplateColumns:
                    "repeat(auto-fill, minmax(300px, 1fr))" }}>
      {children}
    </div>
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
  // that is, and whether a draft of that swing already existed, so
  // "producing" can be told from "re-producing".
  //
  // THE PAGE IS THE ONLY THING THAT KNOWS. The produce runs on a
  // thread under the recording's status key and says nothing about
  // which cut an operator started it from, and without that the cut
  // tile and the draft tile both change underneath them: the cut
  // appears to vanish and a second draft of the same swing appears.
  const [producingCut, _setProducingCut] = useState(null);
  // AND THE SAME THING IN A REF. `poll` is a useCallback whose deps
  // do not include this, so the copy of it that is running captured
  // whatever the state was when it was memoised — `null`, because
  // the poll is created long before anybody presses Produce Clip.
  // Read from there the note would never name the swing and the
  // clear would never fire, leaving the cut greyed out for good.
  const producingRef = useRef(null);
  // ── SAVED, OR CANCELLED? ──────────────────────────────────────
  //
  // The wizard calls `onBackground` and then `onClose` on Save &
  // close -- it hands the run over and dismisses itself. This page
  // read that `onClose` as a CANCEL and cleared the producing state
  // the save had just set, so the cut tile came back, the "Swing N is
  // producing" tile never appeared, and what the operator got instead
  // was the generic notice for a produce started somewhere else.
  //
  // Set by `onBackground`, read and cleared by `onClose`.
  const backgrounded = useRef(false);
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
  // ── START OVER ───────────────────────────────────────────────
  // Two steps, like "delete recording" below, and for the same
  // reason: the armed state is where the numbers go, so nobody
  // deletes a day's work from a button whose label is three words.
  const [resetArmed, setResetArmed] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [editing, setEditing] = useState(null);
  const [editErr, setEditErr] = useState(null);
  const [viewer, setViewer] = useState(null);

  // ── WHICH STAGE IS ON SCREEN ─────────────────────────────────
  //
  // Starts on the raw clips, because that is where the work starts
  // and it is the only stage that is never empty.
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // ?stage=cuts OPENS THERE. The hand cutter sends the operator back
  // when a recording has been gone through, and "back to the flow"
  // meaning "back to the five hundred raw clips you just finished
  // with" is the wrong place to land.
  const [step, setStep] = useState(() => {
    const i = STEPS.findIndex((x) => x.key === params.get("stage"));
    return i < 0 ? 0 : i;
  });
  // A NEW PAGE STARTS AT THE TOP. The stages are different lengths --
  // five hundred recordings, then twenty-nine cuts -- so a click from
  // a scrolled-down Raw clips landed halfway down a page that had
  // nothing there.
  useEffect(() => { window.scrollTo({ top: 0, behavior: "smooth" }); },
            [step]);
  // THE URL FOLLOWS THE STAGE, so a reload or a shared link comes
  // back to the stage that was being worked on. `replace` because
  // walking four stages should not put four entries in the history
  // between the operator and the page they came from.
  useEffect(() => {
    const want = STEPS[step].key;
    if (params.get("stage") !== want) {
      setParams({ stage: want }, { replace: true });
    }
  }, [step]);   // eslint-disable-line react-hooks/exhaustive-deps
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

  // ── WHO ELSE IS WORKING, AND ON WHAT ──────────────────────────
  //
  // `flowStatus` answers for ONE recording and this page only asked
  // about the ones it had started itself. So a produce begun in
  // another tab, by another operator, or on another backend was
  // invisible here: the card sat offering Produce Clip for a swing
  // already being produced, and the draft turned up later with
  // nothing having said it was coming.
  //
  // One call for all of it. Produce serialises site-wide, so the
  // answer is almost always one recording and never more than a
  // handful -- asking per card would be forty requests a tick to
  // learn the same thing.
  const activeIds = useRef(new Set());
  useEffect(() => {
    if (!adminPassword) return undefined;
    let dead = false;
    const ask = async () => {
      try {
        const got = await api.flowActive(adminPassword);
        if (dead) return;
        const rows = got?.active || [];
        const now = new Set(rows.map((a) => a.upload_id));
        setRuns((s) => {
          const next = { ...s };
          for (const a of rows) {
            // THE WATCH'S OWN STATUS WINS where there is one: it is
            // this page's running job, it is asked for more often,
            // and it carries the report this page prints when the
            // run ends.
            if (polling.current.has(a.upload_id)) continue;
            next[a.upload_id] = {
              ...(next[a.upload_id] || {}),
              running: !!a.flow_running,
              flow_running: !!a.flow_running,
              produce_running: !!a.produce_running,
              produce_stage: a.produce_running ? a.stage : null,
              produce_queued: a.produce_queued || 0,
              busy: !!(a.flow_running || a.produce_running),
              stage: a.stage, done: a.done, total: a.total,
              elsewhere: !a.known_here,
            };
          }
          // AND WHEN SOMEBODY ELSE'S WORK STOPS, the row it wrote is
          // new on this page. Clear the flags and reload, or the
          // card goes on saying "producing" until something else
          // happens to reload the columns.
          for (const id of activeIds.current) {
            if (now.has(id) || polling.current.has(id)) continue;
            next[id] = {
              ...(next[id] || {}), running: false, flow_running: false,
              produce_running: false, produce_stage: null, busy: false,
              produce_queued: 0,
            };
          }
          return next;
        });
        const stopped = [...activeIds.current].filter(
          (id) => !now.has(id) && !polling.current.has(id));
        activeIds.current = now;
        if (stopped.length) await load();
      } catch {
        // SILENT. This is a background question about other people's
        // work; a failed one is answered by the next tick, and an
        // error banner over the page for it would be noise.
      }
    };
    ask();
    const id = setInterval(ask, ACTIVE_MS);
    return () => { dead = true; clearInterval(id); };
  }, [adminPassword, load]);

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
    // ── NOT FINISHED UNTIL IT HAS STARTED ────────────────────────
    //
    // "Nothing is running" is the state BEFORE a job as well as
    // after it, and this read the first one as the second: it
    // announced the produce finished, claimed, reloaded and showed
    // the old draft as though it were the new one — and then the
    // produce actually started and announced itself all over again.
    // Working, done, working, done, for one clip.
    //
    // So an idle status only ends the watch once a running one has
    // been seen. A short grace before that, because the start is a
    // server round trip away, and after it the watch gives up rather
    // than hanging on a job that never began.
    let seen = false;
    let waitedMs = 0;
    const tick = async () => {
      try {
        const st = await api.flowStatus(adminPassword, uploadId);
        setRuns((s) => ({ ...s, [uploadId]: st }));
        if (!(st?.busy ?? st?.running) && !seen) {
          waitedMs += POLL_MS;
          if (waitedMs < START_GRACE_MS) {
            setTimeout(tick, POLL_MS);
            return;
          }
          // IT NEVER STARTED. Say so: the alternative is the page
          // reporting a clip that was never produced.
          polling.current.delete(uploadId);
          setLiveDrafts(null);
          adopting.current.delete(uploadId);
          if (producingRef.current?.uploadId === uploadId) {
            setProducingCut(null);
          }
          setErr(`#${uploadId}: nothing started producing within `
                 + `${Math.round(START_GRACE_MS / 1000)}s — the save may `
                 + `have been refused. Nothing has been claimed.`);
          await load();
          return;
        }
        if (st?.busy ?? st?.running) seen = true;
        // `busy`, NOT `running`: the poll has to keep asking until
        // the flow run AND any hand-made produce have stopped. On
        // `running` alone it would give up while a produce was still
        // writing and never claim the clip it made.
        if (st?.busy ?? st?.running) {
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
            // The clip a produce writes is a NEW row, so "it worked"
            // and "nothing happened" look identical on a stage that
            // has just reloaded. This names the swing, says whether
            // a draft was replaced, and says which older draft was
            // removed for being of the same swing — the one thing an
            // operator cannot see, because the tile is already gone
            // by the time they look.
            const _p = producingRef.current;
            const _sup = (_c?.superseded || []).length;
            setNote(
              (_p && _p.swing != null
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
    backgrounded.current = false;
    // "+ ADD A SWING" COMES WITH NO SEED, so the swing number is not
    // known until the operator saves. Still worth saying that THIS
    // recording is producing, and from this page: without it the only
    // notice is the one for a produce nobody here started.
    if (!producingRef.current) {
      setProducingCut({ cutId: null, swing: null, slot: null,
                        uploadId, had: false, from: "add" });
    }
    try {
      const got = await api.listLongUploads(adminPassword, 1, 0,
                                            { uploadId });
      const row = Array.isArray(got) ? got[0] : null;
      if (!row) throw new Error(`could not load recording #${uploadId}`);
      // ── THE SWING THIS IS, IF THE CALLER KNOWS ──────────────────
      //
      // Produce Clip is pressed ON swing 1's cut, so it produces
      // SWING 1 — not a new swing that happens to be plotted from
      // swing 1's seconds. Appending gave "swing 5" from the cut for
      // swing 1 and left the old draft beside the new one.
      //
      // WHERE THAT SWING ALREADY HAS A RECORD, the map opens on it
      // and the save patches it. Re-producing a swing replaces it:
      // there is one draft of a swing, and pressing Produce Clip on
      // it again is asking for a better one, not a second one.
      const _arr = row.edit_metrics?.swings || [];
      const _pos = seed?.idx == null ? -1
        : _arr.findIndex((sw) => (sw?.idx ?? -1) === seed.idx);
      if (_pos >= 0) {
        setEditing({ row, swingPos: _pos, addNew: false, seed: null });
      } else {
        setEditing({ row, swingPos: null, addNew: true, seed });
      }
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
    backgrounded.current = false;
    // WHICH SWING IS ABOUT TO BE REBUILT. Editing a draft re-produces
    // it, and without this the drafts column had nothing to say while
    // that ran -- the tile sat there looking finished and the only
    // notice came from the status poll seconds later.
    setProducingCut({
      cutId: clip.audio_cut_id ?? null,
      swing: clip.cut_idx != null ? clip.cut_idx + 1 : null,
      slot: clip.cut_idx ?? null,
      uploadId: clip.long_upload_id,
      had: true,
      from: "edit",
    });
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
      // The wizard never opened, so nothing is being produced -- and
      // the state set on the click would otherwise grey the tile and
      // announce a swing nobody is making, for as long as the page
      // is open.
      setProducingCut(null);
    } finally {
      setActing(null);
    }
  }

  async function act(key, fn, ...args) {
    setActing(key); setErr(null);
    try { await fn(adminPassword, ...args); await load(); }
    catch (e) { setErr(e.message); } finally { setActing(null); }
  }

  async function startOver() {
    setResetting(true);
    setErr(null);
    try {
      const out = await api.flowReset(adminPassword, { day, zone });
      setResetArmed(false);
      setNote(
        `Started over: ${out.cuts} cut${out.cuts === 1 ? "" : "s"} and `
        + `${out.clips} draft${out.clips === 1 ? "" : "s"} deleted`
        + (out.failed ? `, ${out.failed} could not be` : "")
        + `. The ${out.legacy_after} clip`
        + `${out.legacy_after === 1 ? "" : "s"} from before this flow `
        + `${out.legacy_after === 1 ? "was" : "were"} left alone.`);
      // BACK TO THE RAW CLIPS, because that is what was asked for and
      // the stage they are standing on is now empty.
      setStep(0);
      await load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setResetting(false);
    }
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

  const rawCard = (r, run, busy) => {
    // ── TWO DIFFERENT "DO NOT TOUCH THIS" ─────────────────────────
    //
    // `busy` is the FLOW running on this recording, and it is what
    // the card SAYS: RUNNING, greyed, because the recording itself
    // is being cut. A hand-made produce is not that — it is one
    // swing being drafted at stage 3 — and letting it grey out stage
    // 1 made the page look like it had gone backwards.
    //
    // Starting a flow ON TOP of either is still wrong: the flow
    // clears the cuts and the produce writes clips, and the two
    // racing is how a draft ends up naming a cut that was deleted
    // underneath it. So the BUTTONS go by `anyWork` and only the
    // LABEL goes by `busy`.
    const anyWork = busy || !!run?.produce_running;
    return (
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
      {/* ── TO THE HAND CUTTER, NOT TO THE CUTTER ──────────────────
          This used to run the whole automatic flow -- listen for
          strikes, cut around each, produce a draft from every one.
          It now opens the recording in the manual cutter, where
          somebody watches it and says where each clip starts.

          THE AUTOMATIC PATH IS STILL HERE, under "Audio cuts only"
          directly below. Removing it would have thrown away the
          thing that is right most of the time to fix the times it is
          wrong; they are two ways to fill the same stage and the
          operator picks per recording. */}
      <button type="button" className="btn small"
              style={{ marginTop: 6, width: "100%" }}
              disabled={anyWork}
              title={anyWork && !busy
                ? "a draft is being produced from this recording"
                : "watch this recording and mark where each clip "
                  + "starts, 13 seconds at a time"}
              onClick={() => navigate(
                `/admin/production-flow/manual/${r.id}`)}>
        {busy ? "running…" : "Start production flow"}
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
              disabled={anyWork || acting === `cutsonly-${r.id}`}
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
  };

  const cutCard = (k) => {
    // ── IT NEVER GOES AWAY WHILE IT IS BEING PRODUCED FROM ───────
    //
    // The cut is the thing being worked ON. Watching it disappear
    // mid-produce reads as the produce having consumed it, and when
    // the stage settles afterwards there is no way to tell what
    // happened.
    const making = producingCut?.cutId === k.id;
    return (
    <div key={k.id} className="card tight"
         style={{ margin: 0, opacity: making ? 0.55 : 1 }}>
      {making && (
        <div className="tiny" style={{ marginBottom: 4, padding: "3px 6px",
                                       borderRadius: 4,
                                       background: "var(--warn-bg, #fdf3d7)" }}>
          <b>producing swing {k.idx + 1}…</b>{" "}
          <span className="muted">
            this cut stays exactly as it is — the draft is made FROM
            it, not out of it
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
                  slot: k.idx,
                  uploadId: k.long_upload_id,
                  // A DRAFT OF THIS SWING ALREADY? Then this is a
                  // RE-produce: the tile on screen is about to be
                  // replaced, and saying so is the difference between
                  // that and a tile quietly vanishing.
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
                  {
                    // WHICH SWING THIS IS. The cut's own index is the
                    // swing number on screen less one, and it is what
                    // stops a produce FROM swing 1 becoming swing 5.
                    idx: k.idx,
                    impact_frame: k.impact_frame,
                    start_frame: k.start_frame,
                    end_frame: k.end_frame,
                  },
                  `cut-${k.id}`); }}>
          {acting === `cut-${k.id}` ? "opening…" : "Produce Clip"}
        </button>
      )}
      {k.produce_error && (
        <div className="tiny err-text" style={{ marginTop: 4 }}>
          {k.produce_error}
        </div>
      )}
      {/* ── ONE STAGE BACK ─────────────────────────────────────────
          A cut is a claim that there was a swing here. When it is
          wrong -- a practice swing, a car door, a hand-marked clip
          half a second late -- the only ways back were to re-cut the
          whole recording, which throws away every OTHER cut on it
          too, or to leave the wrong one in the column and remember
          it was wrong.

          IT REALLY DELETES. "Back" is the direction, not a promise
          that nothing is lost: the row and both angle files go, and
          the swing has to be found again by the cutter or by hand.
          What survives is the recording, which is the stage this
          sends it back to.

          NOT WHILE A DRAFT EXISTS. Deleting the cut under a draft
          leaves that draft in the next column pointing at a swing
          that no longer exists -- provenance that reads as real and
          is not. Send the draft back first; one stage at a time, in
          the order they were made. */}
      <button type="button" className="btn ghost small"
              style={{ marginTop: 5, width: "100%" }}
              disabled={making || !!k.produced || acting === `cutdel-${k.id}`}
              title={k.produced
                ? "this swing has a draft — send that back to Audio "
                  + "cuts first, then this can go back to the recording"
                : "delete this cut and its two angle files; the "
                  + "recording itself is untouched"}
              onClick={() => act(`cutdel-${k.id}`, api.flowDeleteCut, k.id)}>
        {acting === `cutdel-${k.id}` ? "removing…" : "← back to Raw clips"}
      </button>
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
        {/* ── ONE STAGE BACK ───────────────────────────────────
            This was labelled "delete", which is what it does to the
            clip and not what it does to the swing: the cut survives
            and goes back to unproduced, so the tile reappears in
            Audio cuts with Produce Clip on it. An operator looking
            for a way to undo a bad produce had no reason to think
            "delete" was it, and every reason to think it would take
            the swing with it. The name is the direction now; the
            title says what is actually thrown away. */}
        <button type="button" className="btn ghost small"
                disabled={acting === `del-${c.id}`}
                title={"throw this draft away — the swing goes back to "
                       + "Audio cuts and can be produced again"}
                onClick={() => act(`del-${c.id}`, api.flowDeleteClip, c.id)}>
          {acting === `del-${c.id}` ? "sending back…" : "← back to Audio cuts"}
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
  // AND WHICH STAGE THAT RUN IS ON, so a run started on one stage can
  // still be seen from another.
  const liveStep = liveRun
    ? (PHASE_STEP[phaseOf(liveRun.stage).phase] ?? null) : null;

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
              · one stage at a time · only what this flow made
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
            {/* ── START OVER ─────────────────────────────────────
                Deletes what the flow made and leaves the recordings,
                so the stepper reads "532 / 0 / 0 / 0" again. Armed,
                with the counts said out loud: the numbers live in
                the confirmation because that is the only place a
                reader is looking at the moment it matters. */}
            {!resetArmed ? (
              <button type="button" className="btn ghost small"
                      disabled={!countOf("cuts") && !countOf("draft")}
                      title={"delete this flow's cuts and drafts and go "
                             + "back to the raw clips"}
                      onClick={() => setResetArmed(true)}>
                start over
              </button>
            ) : (
              <span className="tiny"
                    style={{ padding: "4px 6px", borderRadius: 4,
                             background: "var(--warn-bg, #fdf3d7)",
                             display: "inline-flex", gap: 6,
                             alignItems: "center", flexWrap: "wrap" }}>
                <b>Start over?</b> Deletes {countOf("cuts") ?? "—"} cut
                {countOf("cuts") === 1 ? "" : "s"} and{" "}
                {countOf("draft") ?? "—"} draft
                {countOf("draft") === 1 ? "" : "s"}
                {(day || zone !== "") ? " for the current filter" : ""}.
                {" "}No recording is deleted. This cannot be undone.
                <button type="button" className="btn small"
                        disabled={resetting}
                        onClick={startOver}>
                  {resetting ? "deleting…" : "yes, start over"}
                </button>
                <button type="button" className="btn ghost small"
                        disabled={resetting}
                        onClick={() => setResetArmed(false)}>
                  cancel
                </button>
              </span>
            )}
          </div>
        </div>
      </div>

      {err && <div className="card err-text small">{err}</div>}

      {/* ── THE WHOLE SEQUENCE, AND WHERE THE WORK IS ──────────
             It reads as the overview it is -- raw clips become cuts
             become drafts become finals, with what is waiting in
             each. Picking one marks it and leaves the table alone:
             nothing is hidden and nothing is resized. The same mark
             the column headings carry; one state, so they cannot
             disagree. */}
      <Stepper step={step} onStep={setStep} countOf={countOf} />

      {/* A RUN GOING IN A COLUMN THAT IS NOT THE MARKED ONE. Every
          column is on screen and the same size, so this says where
          to look and nothing more -- there is no column to bring
          forward and no button that would do anything. */}
      {liveRun && liveStep != null && liveStep !== step && (
        <div className="card tight small"
             style={{ display: "flex", gap: 8, flexWrap: "wrap",
                      alignItems: "center" }}>
          <span className="pill small">working</span>
          <span>
            #{liveId} on <b>{STEPS[liveStep].title}</b>
            {" — "}{phaseOf(liveRun.stage).text || "…"}
            {liveRun.total
              ? ` · ${liveRun.done ?? 0} of ${liveRun.total}` : ""}
          </span>
        </div>
      )}
      {/* ── THE TABLE: ONE ROW PER RECORDING, FOUR CELLS ─────────
             THE LINEAGE IS WHAT THE PAGE IS FOR. Which cuts came
             from which recording, which of those became a draft,
             which draft was approved — a stage at a time cannot show
             that. It shows one stage of everything, and the join has
             to be rebuilt by reading headings.

             AND THE FOUR ARE EQUAL. The numbered headings mark
             where the work is; they do not resize the table to say
             so, because that moved every card on the page each time
             a stage was picked. */}
      <div style={{ display: "grid",
                    gridTemplateColumns: GRID,
                    borderRight: "1px solid var(--line)",
                    borderBottom: "1px solid var(--line)" }}>
        {STEPS.map((st, i) => (
          <HeadCell key={st.key} n={i + 1} title={st.title}
                    unit={st.unit} count={countOf(st.key)}
                    blurb={st.blurb} on={i === step}
                    onPick={() => setStep(i)} />
        ))}

        {raw === null && (
          <div style={{ gridColumn: "1 / -1", padding: 10 }}>
            <div className="shimmer" style={{ height: 90 }} />
          </div>
        )}
        {raw?.length === 0 && (
          <div style={{ gridColumn: "1 / -1", padding: 10 }}
               className="tiny muted">No recordings.</div>
        )}

        {raw !== null && bands.map((uid) => {
          const r = rawById[uid];
          const run = runs[uid];
          // ── WHAT "BUSY" MEANS PER COLUMN ──────────────────────
          //
          // THE RAW CARD IS ABOUT THE RECORDING. It greys out and
          // says RUNNING while the FLOW is cutting it, which is work
          // on the recording itself. Producing one swing by hand
          // from its cut is not that: it is stage 3 work, and
          // letting it grey out stage 1 made the page look like it
          // had gone backwards.
          const busy = !!run?.running;
          // The drafts column, where a hand-made produce does show.
          const producing = !!run?.produce_running
            || producingCut?.uploadId === uid;
          const mine = (rows) => (rows || [])
            .filter((x) => x.long_upload_id === uid);
          const myFinals = mine(shownFinals);
          const making = producingCut?.uploadId === uid
            ? producingCut : null;
          return (
            <Fragment key={uid}>
              {/* 1. the recording */}
              <Cell center
                    empty="this recording is gone from the list">
                {r ? rawCard(r, run, busy) : (
                  <div className="tiny muted">
                    <b>#{uid}</b>
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
                    live={(
                      <>
                        {/* ── THE SWING BEING MADE, BY NAME ───────
                            The tile for it is pulled out below while
                            this runs: on a re-produce the one on
                            screen is about to be replaced, and
                            leaving it there means watching a stale
                            draft sit beside a progress bar. */}
                        {making && (
                          <div className="tiny" style={{
                            padding: "4px 6px", borderRadius: 4,
                            background: "var(--warn-bg, #fdf3d7)" }}>
                            {/* A SWING NUMBER ONLY WHERE THERE IS
                                ONE. A clip that never matched a cut
                                has no swing number -- the card for it
                                says "swing unknown" -- and this read
                                "Swing  is producing…", a blank where
                                the operator looks for the answer. */}
                            <b>
                              {making.swing != null
                                ? `Swing ${making.swing} is`
                                : "A draft is"}
                              {making.had ? " re-producing" : " producing"}…
                            </b>
                            <div className="muted">
                              {making.had
                                ? "the draft that was here is replaced "
                                  + "only when this one succeeds — a "
                                  + "swing never has two drafts"
                                : "plotted by hand from the audio cut "
                                  + "it came from"}
                            </div>
                            {/* ── WHAT THEY MAY DO WHILE IT RUNS ───
                                Asked for, and the answer is not
                                obvious: one clip is built at a time
                                site-wide, a second plot is QUEUED
                                rather than refused, and the clip is
                                filed by the server, so closing the
                                tab does not lose it. Without this an
                                operator either sits and watches or
                                clicks again and wonders. */}
                            <div className="muted" style={{ marginTop: 3 }}>
                              Keep working: plot another cut now and it
                              queues — one clip is built at a time.
                              {run?.produce_queued > 1
                                ? ` ${run.produce_queued} are queued on `
                                  + "this recording."
                                : ""}
                              {" "}It is filed into this column when it
                              lands, whether or not this page is open.
                            </div>
                          </div>
                        )}
                        {/* The flow's own third phase. */}
                        {busy ? <Working run={run} phase="produce" /> : null}
                        {/* AND A PRODUCE THIS PAGE DID NOT START --
                            another tab, or a reload while one was
                            going. `Working` cannot narrate it: it
                            reads the FLOW's stage, which is the last
                            flow run's, not this produce's. */}
                        {producing && !busy && !making && (
                          <div className="tiny" style={{
                            padding: "4px 6px", borderRadius: 4,
                            background: "var(--warn-bg, #fdf3d7)" }}>
                            <b>a draft is being produced…</b>
                            {/* NO CLAIM ABOUT WHERE IT CAME FROM.
                                This used to read "started somewhere
                                other than this page", which was a
                                guess from one fact -- that this tab
                                had no swing number for it -- and it
                                was usually wrong: a Save & close was
                                being read as a cancel, which threw
                                the swing away a moment before this
                                rendered. Say what is known. */}
                            <div className="muted">
                              {run?.produce_stage
                                || "it will appear here when it lands"}
                              {run?.produce_queued > 1
                                ? ` · ${run.produce_queued} queued on `
                                  + "this recording"
                                : ""}
                            </div>
                          </div>
                        )}
                      </>
                    )}
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
                    pulled while that runs, so the column never shows
                    the old draft and the new one together. */}
                {mine(shownDrafts)
                  .filter((c) => !(making && c.cut_idx != null
                    && c.cut_idx + 1 === making.swing))
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


      {/* NO "NEXT STAGE" BUTTONS DOWN HERE. They belonged to the
          stage view, where they changed what was on screen. All four
          columns are on screen at once and the same size, so a
          button labelled with the next stage would move a mark and
          nothing else -- a control that looks like it did something. */}

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
          // producing — without this the cut stays greyed out and
          // the drafts stage goes on announcing a swing that is not
          // being made, for as long as the page is open.
          claimForFlow
          onClose={() => {
            // A SAVE CLOSES THIS TOO. `onBackground` fires first and
            // sets the flag; without reading it here, Save & close
            // was handled as a cancel -- the producing state the save
            // had just set was wiped, the cut un-greyed, and the one
            // notice left was the generic "a draft is being produced"
            // meant for a run this page knows nothing about.
            const _saved = backgrounded.current;
            backgrounded.current = false;
            setEditing(null);
            if (!_saved) {
              // A REAL CANCEL: nothing was produced, so nothing is
              // producing. Without this the cut stays greyed and the
              // drafts column announces a swing nobody is making.
              setProducingCut(null);
            }
            load();
          }}
          onBackground={(msg) => {
            const _id = editing.row?.id;
            backgrounded.current = true;
            setEditing(null);
            // ── SAY IT NOW, NOT AFTER THE NEXT POLL ─────────────
            //
            // The wizard calls this the instant it hands the work
            // over -- before the first server call. Everything else
            // on this page waits for a status poll, which is a
            // second of silence at best and five while the queue is
            // answering, and silence after pressing Save is
            // indistinguishable from a save that did not register.
            const _p = producingRef.current;
            setNote(
              (_p?.swing != null
                ? `Swing ${_p.swing} of #${_id} queued — `
                  + `${_p.had ? "re-producing" : "producing"}. `
                : `#${_id} queued — ${_p?.had ? "re-" : ""}producing. `)
              + (msg ? `${msg} ` : "")
              + "One clip is built at a time; this page files it into "
              + "Draft produced when it lands. You can keep plotting "
              + "other swings meanwhile — they queue behind this one.");
            if (_id && !producingRef.current) {
              setProducingCut({ cutId: null, swing: null, slot: null,
                                uploadId: _id, had: false, from: "add" });
            }
            // ── NO WATCH YET ────────────────────────────────────
            //
            // This fires when the wizard hands the work over, which
            // is BEFORE the call that queues the produce: a tracer
            // re-render and a finalize come first, and they are the
            // slow part. A watch started here saw an idle server,
            // called the produce finished, claimed nothing and
            // reloaded the old draft as though it were the new one
            // -- and then the produce began and said so all over
            // again. `onDone` fires once the produce is queued, and
            // that is when there is something to watch.
            if (_id) adopting.current.add(_id);
            load();
          }}
          onDone={(ok, msg) => {
            const _id = editing.row?.id;
            backgrounded.current = false;
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
