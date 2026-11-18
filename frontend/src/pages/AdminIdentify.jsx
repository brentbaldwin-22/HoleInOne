import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import PlayerIdLink, { IDENTIFY_CHANGED } from "../components/PlayerIdLink.jsx";
import { api } from "../api.js";
import { Brand, Icon } from "../components/Brand.jsx";
import { fmtDateTime } from "../time.js";
import { visibleClips } from "./identifyWindow.js";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

/** Today on the OPERATOR's clock, as the value a date input wants. */
function todayLocal() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/**
 * One local calendar day as a pair of UTC instants.
 *
 * The server cannot work this out: every datetime it stores is naive UTC
 * and a Course has no timezone, so a UTC day at an Eastern course starts
 * at 8pm the evening before and a late tee time lands on the wrong page.
 * The browser is the only party that knows the operator's offset, so it
 * is the one that draws the boundary. setDate() rather than +24h, so the
 * day a clock changes is still one day.
 */
function dayWindow(localDate) {
  const start = new Date(`${localDate}T00:00`);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** How a tile's crop was aimed, for the tooltip. */
function aimNote(r) {
  if (!r) return "";
  if (r.focus_src === "scan") {
    return `tracer ${r.focus_x}${r.zoomed ? " (zoomed)" : ""}`;
  }
  if (r.focus_src === "vertical") {
    return `tracer ${r.focus_x} from the 9:16 — this clip's own scan found none`;
  }
  return `NO TRACER (${r.tracer_why || "?"}) — centred, so this tile is a guess`;
}

function clipLabel(c) {
  const where = c.camera_type === "tee" ? "tee" : "green";
  return `Hole ${c.hole_number} · ${where}`;
}

/* ONE TILE, TWO GRIDS. The hole grid and the whole-day grid show the
 * same thing and must keep showing the same thing -- two copies of
 * fifty lines of tile would drift the first time either was touched.
 */
function ClipTile({ c, playerId, still, busy, onPick, onWatch }) {
  const mine = c.participant_id === playerId;
  const taken = c.participant_id != null && !mine;
  return (
    <div className="pick-cell">
      <button
        type="button"
        className={`pick-tile${mine ? " on" : ""}`}
        onClick={() => onPick(c)}
        disabled={busy === c.id}
        style={taken ? { opacity: 0.55 } : undefined}
        title={[
          taken ? `Currently ${c.participant_name}'s` : clipLabel(c),
          still && aimNote(still),
        ].filter(Boolean).join("  ·  ")}
      >
        {/* NO VIDEO ON THIS GRID AT ALL. A <video> here decodes, seeks
            and repaints while the operator is trying to compare eleven
            outfits -- the tiles flickered. A still cannot flicker.
            Until one arrives the tile is simply empty. */}
        {still ? (
          <img src={still.still_url} alt={`Shot at ${clipLabel(c)}`} />
        ) : (
          <span className="pick-empty">…</span>
        )}
        <span className="pick-time">
          {fmtDateTime(c.captured_at, undefined, { timeStyle: "short" })}
          {" · "}
          {clipLabel(c)}
          {taken ? ` · ${c.participant_name}` : ""}
        </span>
        {mine && (
          <span className="pick-check"><Icon name="check" size={16} /></span>
        )}
      </button>
      {/* A SEPARATE BUTTON, not nested inside the tile: a button within
          a button is invalid, and a click meant for "watch" must never
          land on "give this shot to Alex". */}
      <button
        type="button"
        className="tile-watch"
        onClick={() => onWatch(c)}
        title="Watch the full clip"
        aria-label={`Watch the full clip from ${clipLabel(c)}`}
      >
        <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
          <path d="M4 2.5 L13 8 L4 13.5 Z" fill="currentColor" />
        </svg>
      </button>
    </div>
  );
}

/**
 * PLAYER IDENTIFICATION — putting a name on a shot by hand.
 *
 * Nothing does this automatically. services/appearance.py is a stub that
 * hashes the image bytes instead of looking at the picture, so matcher.py
 * round-robins and the outfit photo taken at registration is read by
 * nobody. This page is where a person does what the embedder cannot.
 *
 * Player-first, not clip-first. /flagged-clips already walks clips and
 * guesses at golfers underneath each one; that is the wrong way round for
 * someone at the course, who has the roster in front of them and can
 * answer "which of these shots is Alex's" but not "who is this".
 */
export default function AdminIdentify() {
  const adminPassword =
    localStorage.getItem(ADMIN_PW_STORAGE) ||
    localStorage.getItem(LEGACY_ADMIN_PW_STORAGE) ||
    "";

  const [courses, setCourses] = useState(null);
  const [courseId, setCourseId] = useState("");
  // The date field is UNCONTROLLED, read on change. Bound to state a
  // native date input cannot be typed into: it is a row of segments, and
  // the first digit makes the value briefly "", which React then writes
  // back over the segment being typed.
  const [day, setDay] = useState(todayLocal);
  const [data, setData] = useState(null);
  const [player, setPlayer] = useState(null);   // the golfer being worked on
  const [busy, setBusy] = useState(null);       // clip id mid-request
  const [stills, setStills] = useState({});     // {clip_id: url}
  const [holeIdx, setHoleIdx] = useState(0);    // where in the par-3s we are
  // Set by the operator to see past the tee-time window. Reset on
  // every hole and player change below, so revealing everything once
  // does not quietly stay on for the rest of the round.
  const [showAllClips, setShowAllClips] = useState(false);
  // The whole-day backstop below the hole grid. Deliberately NOT reset
  // on hole change: an operator who has opened it is hunting, and
  // closing it under them on every Next is the opposite of helpful.
  const [showDayAll, setShowDayAll] = useState(false);
  const [watch, setWatch] = useState(null);     // clip being played full-size
  // Bumped whenever the course or day changes, so stills still in
  // flight for the day you just left cannot land on the new grid.
  const run = useRef(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setCourses(await api.identifyCourses(adminPassword, dayWindow(day)));
      } catch (e) {
        setError(e.message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day]);

  async function load(cid = courseId, d = day) {
    run.current += 1;
    setStills({});
    if (!cid) { setData(null); return; }
    setError(null);
    try {
      const d2 = await api.identifyDay(adminPassword, { courseId: cid, ...dayWindow(d) });
      setData(d2);
      loadStills(d2.clips || []);
    } catch (e) {
      setData(null);
      setError(e.message);
    }
  }

  /**
   * Fill in the frozen frames, a few at a time.
   *
   * The first call for a clip scans its video for the tracer, which is
   * seconds of work, so eleven at once would hold the page up and
   * eleven in sequence would take a minute. Three at a time fills the
   * grid visibly, in roughly the order the operator reads it, and every
   * call after the first is a cache hit.
   */
  async function loadStills(clips) {
    const mine = run.current;
    const queue = clips.map((c) => c.id);
    async function worker() {
      while (queue.length) {
        const id = queue.shift();
        try {
          const r = await api.identifyStill(adminPassword, id);
          // A still for a day the operator has already navigated away
          // from must not be painted onto the grid now showing.
          if (r.still_url && run.current === mine) {
            setStills((prev) => ({ ...prev, [id]: r }));
          }
        } catch {
          // The tile stays empty. A missing frame is a worse grid, not
          // a broken page.
        }
      }
    }
    await Promise.all([worker(), worker(), worker()]);
  }

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [courseId, day]);

  // The roster row the page is currently working on, re-read from the
  // freshest payload so its tally moves as clips are paired.
  const current = player && data
    ? data.players.find((p) => p.id === player.id) || player
    : null;

  /**
   * The holes to walk, in play order.
   *
   * The course's own par 3s, because that is what they are -- Rivertowne
   * plays 2, 8, 14 and 17. Where a course has none recorded, fall back
   * to the holes the day's footage actually came from: an unconfigured
   * course should still be workable, and showing nothing at all would
   * look like the page was broken rather than the course unset.
   */
  const holes = (data?.course?.par3_holes?.length
    ? data.course.par3_holes
    : [...new Set((data?.clips || []).map((c) => c.hole_number))].sort((a, b) => a - b));
  const hole = holes[Math.min(holeIdx, Math.max(0, holes.length - 1))];
  const allHoleClips = (data?.clips || []).filter((c) => c.hole_number === hole);
  const onLastHole = holeIdx >= holes.length - 1;

  /* NARROW BY THE CLOCK. A group that tees off at noon reaches hole 2
   * about a quarter of an hour later, so the 9am and 3pm shots on that
   * camera are not theirs and are only there to be scrolled past. The
   * backend works the window out from the tee time (see
   * identify.shot_window) and widens it hole by hole, because pace
   * error accumulates: tight on hole 2, deliberately loose by hole 17.
   *
   * THREE RULES, ALL OF THEM ABOUT NOT LOSING A SHOT:
   *
   *   no window          -> show everything. A player with no tee time
   *                         is not a reason to show an empty grid.
   *   window hides all   -> show everything. If nothing at all falls in
   *                         it the model is wrong for this round -- a
   *                         shotgun start, a corrected clock -- and the
   *                         operator needs the footage, not our theory.
   *   anything hidden    -> say how many, one click to reveal.
   *
   * Narrowing is a convenience. Being complete is the job.
   */
  const win = current?.windows?.[String(hole)] || null;
  // The rule lives in identifyWindow.js, where it can be exercised
  // without a browser -- tools/identify_window_test.js asserts that
  // shown + hidden accounts for every clip in every mode.
  const { shown: holeClips, hidden: hiddenCount, windowed } =
    visibleClips(allHoleClips, win, current?.id, showAllClips);
  // Every clip of the day, no hole filter and no window. `day()`
  // already returns them in captured_at order.
  const dayClips = data?.clips || [];

  // A reveal belongs to one hole and one golfer. Leaving it on would
  // quietly undo the narrowing for the rest of the round, which is the
  // sort of thing nobody notices until they are scrolling again.
  useEffect(() => { setShowAllClips(false); }, [hole, current?.id]);

  async function pair(clip) {
    if (!current) return;
    setBusy(clip.id);
    setError(null);
    try {
      if (clip.participant_id === current.id) await api.identifyUnpair(adminPassword, clip.id);
      else await api.identifyPair(adminPassword, clip.id, current.id);
      await load();
      window.dispatchEvent(new Event(IDENTIFY_CHANGED));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
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
        <Link to="/admin/produced-clips">Produced Clips</Link>
        <Link to="/admin/broadcast-clips">Broadcast</Link>
        <Link to="/admin/cameras">Cameras</Link>
        <PlayerIdLink active />
      </div>

      <div className="card">
        <h3>Player identification</h3>
        <p className="small muted">
          Pick the course and the day, then a golfer, then the shots they hit.
          Nothing matches a swing to a person automatically — the outfit photo
          is collected but no embedder reads it yet, so this is the step that
          puts a clip in someone's gallery.
        </p>

        <div className="inline" style={{ gap: 16, flexWrap: "wrap", marginTop: 14 }}>
          <div className="field" style={{ margin: 0, minWidth: 280 }}>
            <label>Course</label>
            <select
              value={courseId}
              onChange={(e) => { setPlayer(null); setCourseId(e.target.value); }}
            >
              <option value="">Pick a course…</option>
              {(courses || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.unidentified > 0
                    ? ` — ${c.unidentified} golfer${c.unidentified === 1 ? "" : "s"} unidentified`
                    : " — clear"}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label>Day</label>
            <input
              type="date"
              defaultValue={todayLocal()}
              onChange={(e) => { if (e.target.value) { setPlayer(null); setDay(e.target.value); } }}
            />
          </div>
        </div>
      </div>

      {error && <p className="err-text small">{error}</p>}

      {data && !current && (
        <div className="card">
          <div className="inline" style={{ justifyContent: "space-between", width: "100%", marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Who played</h3>
            <span className="small muted">
              {data.players.length} golfer{data.players.length === 1 ? "" : "s"}
              {" · "}
              {data.unidentified} of {data.clips.length} shot
              {data.clips.length === 1 ? "" : "s"} unidentified
            </span>
          </div>

          {data.players.length === 0 ? (
            <p className="small muted">
              Nobody signed up at {data.course.name} on this day. If golfers
              registered, check the date — a tee time is stored against the
              instant it was booked for, not the day the clip landed.
            </p>
          ) : (
            <div className="pick-grid">
              {data.players.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="pick-tile"
                  onClick={() => { setHoleIdx(0); setPlayer(p); }}
                  title={`${p.name} — ${p.assigned} shot${p.assigned === 1 ? "" : "s"} so far`}
                >
                  {p.selfie_url ? (
                    <img src={p.selfie_url} alt={p.name} />
                  ) : (
                    <span className="pick-empty">No photo</span>
                  )}
                  <span className="pick-time">
                    {p.name}
                    {p.assigned > 0 ? ` · ${p.assigned}` : ""}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {data && current && (
        <div className="card">
          <div className="inline" style={{ justifyContent: "space-between", width: "100%", marginBottom: 12 }}>
            <div className="inline" style={{ gap: 12 }}>
              {current.selfie_url && (
                <img
                  src={current.selfie_url}
                  alt={current.name}
                  style={{ width: 54, height: 72, objectFit: "cover", borderRadius: 6 }}
                />
              )}
              <div>
                <h3 style={{ margin: 0 }}>{current.name}</h3>
                <span className="small muted">
                  {current.tee_time ? `Teed off ${fmtDateTime(current.tee_time)}` : "No tee time"}
                  {" · "}
                  {current.assigned} shot{current.assigned === 1 ? "" : "s"} paired
                </span>
              </div>
            </div>
            <button type="button" className="ghost small" onClick={() => setPlayer(null)}>
              ← All golfers
            </button>
          </div>

          <div className="inline" style={{ justifyContent: "space-between", width: "100%", marginTop: 14 }}>
            <h4 style={{ margin: 0 }}>Hole {hole ?? "—"}</h4>
            <span className="small muted">
              Par 3 {Math.min(holeIdx + 1, holes.length)} of {holes.length}
              {" · "}
              {holeClips.length} shot{holeClips.length === 1 ? "" : "s"}
              {/* WHAT IS BEING HELD BACK, AND THE WAY TO IT. A filter
                  the operator cannot see is a filter they will not
                  trust the moment a shot seems to be missing -- so the
                  count and the window are both on screen, and one tap
                  undoes it. */}
              {windowed && hiddenCount > 0 && (
                <>
                  {" · "}
                  <button
                    type="button"
                    className="ghost small"
                    onClick={() => setShowAllClips(true)}
                    title={`Showing ${fmtDateTime(win[0], undefined, { timeStyle: "short" })}`
                      + `–${fmtDateTime(win[1], undefined, { timeStyle: "short" })},`
                      + " worked out from this golfer's tee time. The window"
                      + " widens on later holes because pace drifts."}
                  >
                    {hiddenCount} outside this tee time — show all
                  </button>
                </>
              )}
              {showAllClips && win && (
                <>
                  {" · "}
                  <button type="button" className="ghost small"
                          onClick={() => setShowAllClips(false)}>
                    narrow to tee time
                  </button>
                </>
              )}
            </span>
          </div>
          <p className="small muted">
            {current.name}'s shot on hole {hole ?? "—"}. Tap it to give it to
            them; tap it again to take it back. Shots already belonging to
            someone else say so — tapping one moves it.
          </p>

          {holeClips.length === 0 ? (
            <p className="small muted">
              No shots on hole {hole ?? "—"} that day — move on.
            </p>
          ) : (
            <div className="pick-grid">
              {holeClips.map((c) => (
                <ClipTile
                  key={c.id} c={c} playerId={current.id}
                  still={stills[c.id]} busy={busy}
                  onPick={pair} onWatch={setWatch}
                />
              ))}
            </div>
          )}

          {/* NEXT IS ALWAYS LIVE, picked or not. Plenty of golfers do not
              play every par 3, and plenty of shots never got recorded;
              an operator made to pick something before moving on would
              have to give the hole to the wrong person to escape it. */}
          <div className="inline" style={{ justifyContent: "space-between", width: "100%", marginTop: 16 }}>
            <button
              type="button"
              className="ghost small"
              disabled={holeIdx === 0}
              onClick={() => setHoleIdx((i) => Math.max(0, i - 1))}
            >
              ← Hole {holes[holeIdx - 1] ?? ""}
            </button>
            {onLastHole ? (
              <button type="button" onClick={() => { setHoleIdx(0); setPlayer(null); }}>
                Done with {current.name}
              </button>
            ) : (
              <button type="button" onClick={() => setHoleIdx((i) => i + 1)}>
                Next · Hole {holes[holeIdx + 1]} →
              </button>
            )}
          </div>

          {/* EVERY SHOT OF THE DAY, UNDERNEATH. The grid above is
              narrowed twice over -- to one hole, and to the window the
              tee time accounts for -- and both of those are guesses
              about where a shot should be. This is the place where no
              guess is applied at all: every produced clip from the
              day, every hole, every hour, tappable exactly like the
              ones above.
              Collapsed, because it is the backstop and not the
              workflow -- but the count is on the button, so an
              operator who cannot find a shot can see at a glance that
              there is somewhere else to look. */}
          <div style={{ marginTop: 18, borderTop: "1px solid var(--rule)",
                        paddingTop: 14 }}>
            <button
              type="button"
              className="ghost small"
              onClick={() => setShowDayAll((v) => !v)}
            >
              {showDayAll ? "▾" : "▸"} All clips from this day
              {" "}({dayClips.length})
            </button>
            {showDayAll && (
              <>
                <p className="small muted" style={{ marginTop: 8 }}>
                  Every produced clip on {data.course?.name} that day, in
                  the order they were shot — no hole filter, no tee-time
                  window. Tap one to give it to {current.name}.
                </p>
                {dayClips.length === 0 ? (
                  <p className="small muted">No clips at all that day.</p>
                ) : (
                  <div className="pick-grid">
                    {dayClips.map((c) => (
                      <ClipTile
                        key={`all-${c.id}`} c={c} playerId={current.id}
                        still={stills[c.id]} busy={busy}
                        onPick={pair} onWatch={setWatch}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* The full clip, for when a frozen frame is not enough to tell
          two golfers in similar shirts apart. Through a portal, because
          the grid sits inside cards that would clip it. */}
      {watch && createPortal(
        <div className="clip-watch" onClick={() => setWatch(null)}>
          <video
            src={watch.preview_url}
            controls
            autoPlay
            playsInline
            onClick={(e) => e.stopPropagation()}
          />
          <div className="clip-watch-bar">
            <span className="small">
              {fmtDateTime(watch.captured_at, undefined, { timeStyle: "short" })}
              {" · "}{clipLabel(watch)}
              {watch.participant_name ? ` · ${watch.participant_name}` : ""}
            </span>
            <button type="button" onClick={() => setWatch(null)}>Close</button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
