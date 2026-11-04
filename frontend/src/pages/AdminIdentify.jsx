import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import PlayerIdLink, { IDENTIFY_CHANGED } from "../components/PlayerIdLink.jsx";
import { api } from "../api.js";
import { Brand, Icon } from "../components/Brand.jsx";
import { fmtDateTime } from "../time.js";

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

function clipLabel(c) {
  const where = c.camera_type === "tee" ? "tee" : "green";
  return `Hole ${c.hole_number} · ${where}`;
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
            setStills((prev) => ({ ...prev, [id]: r.still_url }));
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
                  onClick={() => setPlayer(p)}
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

          <p className="small muted">
            Every shot recorded that day. Tap one to give it to {current.name};
            tap it again to take it back. Shots already belonging to someone
            else say so — tapping one moves it.
          </p>

          {data.clips.length === 0 ? (
            <p className="small muted">No shots were recorded at this course on this day.</p>
          ) : (
            <div className="pick-grid">
              {data.clips.map((c) => {
                const mine = c.participant_id === current.id;
                const taken = c.participant_id != null && !mine;
                return (
                  <button
                    key={c.id}
                    type="button"
                    className={`pick-tile${mine ? " on" : ""}`}
                    onClick={() => pair(c)}
                    disabled={busy === c.id}
                    style={taken ? { opacity: 0.55 } : undefined}
                    title={taken ? `Currently ${c.participant_name}'s` : clipLabel(c)}
                  >
                    {/* NO VIDEO ON THIS GRID AT ALL. A <video> here
                        decodes, seeks and repaints while the operator is
                        trying to compare eleven outfits -- the tiles
                        flickered. A still cannot flicker. Until one
                        arrives the tile is simply empty. */}
                    {stills[c.id] ? (
                      <img src={stills[c.id]} alt={`Shot at ${clipLabel(c)}`} />
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
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
