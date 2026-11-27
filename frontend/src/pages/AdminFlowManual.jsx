import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api.js";
import { readAdminPassword } from "../adminAuth.js";
import { Brand } from "../components/Brand.jsx";
import { zoneName } from "../zones.js";

/*
 * CUTTING ONE RECORDING BY HAND.
 *
 * The audio cutter listens for a strike and takes four seconds before
 * it and eight after. It is right most of the time and wrong in two
 * ways that cost a clip: a swing it never heard, and a noise it
 * mistook for one. This page is the answer to both -- somebody
 * watches the recording and says where each clip starts.
 *
 * ONE BUTTON. The operator is watching a video for a swing, which is
 * the whole job; every control that is not "here" is something to
 * read instead of watching. Space bar does it too, because the hand
 * that is scrubbing is already on the keyboard.
 *
 * THE CUT DOES NOT BLOCK THE WATCHING. A frame-accurate cut re-encodes
 * and takes a few seconds, and the swings in a three-minute recording
 * do not politely wait that long. So a press records the mark
 * immediately, the video never stops, and the cut lands behind it --
 * each mark showing its own state. An operator who had to wait for
 * ffmpeg between swings would miss the next one.
 *
 * WHERE THE CLIP BEGINS, NOT WHERE THE STRIKE IS. "Start clip" is
 * literal: thirteen seconds forward from the press. That is a
 * different statement from the cutter's peak, and the page says so
 * rather than quietly centring the mark the way the cutter would.
 */

const CLIP_SEC = 13;

/* How far back a press lands, because a human reacts after they see
 * it. Tuned to nothing -- it is zero -- and kept as a named constant
 * so that "my clips all start half a second late" has somewhere
 * obvious to be fixed rather than being hunted through the handler. */
const REACTION_LEAD_SEC = 0;

function fmtClock(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const s = Math.max(0, Number(sec));
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${String(Math.floor(r)).padStart(2, "0")}` +
         `.${String(Math.floor((r % 1) * 10))}`;
}

/* One press: where it was, and what became of it. */
function MarkRow({ mark, onRetry, onRemove }) {
  const tone = mark.state === "failed" ? "err-text"
    : mark.state === "cutting" ? "muted" : "";
  return (
    <div className="card tight" style={{ margin: 0 }}>
      <div className="small">
        <b>Clip {mark.n}</b>{" "}
        <span className="muted">
          {fmtClock(mark.start)} → {fmtClock(mark.end)}
        </span>
      </div>
      <div className={`tiny ${tone}`} style={{ marginTop: 2 }}>
        {mark.state === "cutting" && (
          <>
            <span className="pill small">cutting</span>{" "}
            both angles, frame-accurate…
          </>
        )}
        {mark.state === "done" && (
          <>
            <span className="pill small">cut</span>{" "}
            {mark.secs ? `${mark.secs.toFixed(1)}s of video` : "saved"}
            {mark.green === false && " · no green angle"}
            {/* THE UNDO. One button press at the speed of a playing
                video is going to land early, late or twice, and
                without this the only remedy was to leave the wrong
                clip in the stage and remember it was wrong. */}
            {mark.id != null && (
              <>
                {" · "}
                <button type="button" className="btn ghost small"
                        onClick={() => onRemove(mark)}>remove</button>
              </>
            )}
          </>
        )}
        {mark.state === "removing" && (
          <><span className="pill small">removing</span>{" "}…</>
        )}
        {mark.state === "removed" && (
          <span style={{ opacity: 0.6 }}>removed</span>
        )}
        {mark.state === "failed" && (
          <>
            {mark.error || "the cut failed"}{" "}
            <button type="button" className="btn ghost small"
                    onClick={() => onRetry(mark)}>try again</button>
          </>
        )}
      </div>
      {mark.state === "cutting" && (
        <div className="shimmer" style={{ height: 3, marginTop: 6 }} />
      )}
    </div>
  );
}

export default function AdminFlowManual() {
  const { uploadId } = useParams();
  const navigate = useNavigate();
  const adminPassword = readAdminPassword();

  const [row, setRow] = useState(null);
  const [err, setErr] = useState(null);
  const [marks, setMarks] = useState([]);
  const [now, setNow] = useState(0);
  const [dur, setDur] = useState(0);
  const [ended, setEnded] = useState(false);

  const video = useRef(null);
  /* THE COUNTER, NOT marks.length. Two presses in the same tick both
     read the same length and both call themselves clip 3. */
  const seq = useRef(0);

  useEffect(() => {
    let dead = false;
    api.flowRawOne(adminPassword, uploadId)
      .then((r) => { if (!dead) setRow(r); })
      .catch((e) => { if (!dead) setErr(e.message); });
    return () => { dead = true; };
  }, [adminPassword, uploadId]);

  /* ── THE PRESS ──────────────────────────────────────────────────
     Records the mark and fires the cut, in that order, and never
     waits for the second before returning. The mark is what the
     operator pressed; the cut is what the server makes of it, and
     only one of those two should be able to be lost. */
  const cutHere = useCallback(() => {
    const v = video.current;
    if (!v) return;
    const at = Math.max(0, (v.currentTime || 0) - REACTION_LEAD_SEC);
    const total = v.duration || dur || 0;
    if (total && at >= total - 0.05) return;   // nothing left to cut
    seq.current += 1;
    const n = seq.current;
    const mark = {
      n, start: at,
      end: total ? Math.min(at + CLIP_SEC, total) : at + CLIP_SEC,
      state: "cutting", error: null, green: null, secs: null,
    };
    setMarks((m) => [...m, mark]);
    api.flowManualCut(adminPassword, uploadId, at)
      .then((res) => setMarks((m) => m.map((x) => (
        x.n === n ? { ...x, state: "done", id: res.id,
                      green: !!res.green_url,
                      secs: res.duration_sec, start: res.start_sec,
                      end: res.end_sec } : x))))
      .catch((e) => setMarks((m) => m.map((x) => (
        x.n === n ? { ...x, state: "failed", error: e.message } : x))));
  }, [adminPassword, uploadId, dur]);

  const retry = useCallback((mark) => {
    setMarks((m) => m.map((x) => (
      x.n === mark.n ? { ...x, state: "cutting", error: null } : x)));
    api.flowManualCut(adminPassword, uploadId, mark.start)
      .then((res) => setMarks((m) => m.map((x) => (
        x.n === mark.n ? { ...x, state: "done", id: res.id,
                           green: !!res.green_url,
                           secs: res.duration_sec } : x))))
      .catch((e) => setMarks((m) => m.map((x) => (
        x.n === mark.n ? { ...x, state: "failed", error: e.message } : x))));
  }, [adminPassword, uploadId]);

  const remove = useCallback((mark) => {
    if (mark.id == null) return;
    setMarks((m) => m.map((x) => (
      x.n === mark.n ? { ...x, state: "removing" } : x)));
    api.flowDeleteCut(adminPassword, mark.id)
      .then(() => setMarks((m) => m.map((x) => (
        x.n === mark.n ? { ...x, state: "removed" } : x))))
      .catch((e) => setMarks((m) => m.map((x) => (
        x.n === mark.n
          ? { ...x, state: "failed", error: `could not remove: ${e.message}` }
          : x))));
  }, [adminPassword]);

  /* SPACE CUTS, and does not also scroll the page or play/pause the
     video, which is what it would do left alone. Ignored while the
     focus is in a control that has its own use for it. */
  useEffect(() => {
    const onKey = (e) => {
      if (e.code !== "Space" && e.key !== " ") return;
      const t = e.target;
      const tag = (t?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select"
          || tag === "button" || t?.isContentEditable) return;
      e.preventDefault();
      cutHere();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cutHere]);

  const working = marks.some((m) => m.state === "cutting");
  const made = marks.filter((m) => m.state === "done").length;
  const failed = marks.filter((m) => m.state === "failed").length;
  const left = dur ? Math.max(0, dur - now) : null;

  /* ── ON TO THE CUTS ─────────────────────────────────────────────
     "Once I go through the entire tee raw clip, take me to the next
     page." The video ending IS going through it, so that is the
     trigger -- but it does not navigate on its own. An operator who
     let it run to the end while deciding about the last swing would
     be moved off the page mid-thought, and the cuts they just made
     are two seconds of ffmpeg from being on it. */
  const goToCuts = useCallback(() => {
    navigate("/admin/production-flow?stage=cuts");
  }, [navigate]);

  const title = useMemo(() => {
    if (!row) return `#${uploadId}`;
    return `#${row.id}${row.hole_number ? ` · Hole ${row.hole_number}` : ""}`
      + ` · ${row.course_name || `course #${row.course_id}`}`;
  }, [row, uploadId]);

  return (
    <div className="wrap wide">
      <Brand subtitle="Operator Console" />
      <div className="nav">
        <Link to="/admin">Dashboard</Link>
        <Link to="/admin/production-flow">Production Flow</Link>
        <span className="active">Make clips by hand</span>
      </div>

      <div className="card tight">
        <div className="row" style={{ justifyContent: "space-between",
                                      flexWrap: "wrap", gap: 8 }}>
          <div className="small">
            <b>Make clips by hand</b>{" "}
            <span className="muted tiny">· {title}</span>
          </div>
          <div className="tiny muted">
            {row?.duration_sec ? `${fmtClock(row.duration_sec)} long · ` : ""}
            {row?.trigger_zone == null ? "no tee box recorded"
              : zoneName(row.trigger_zone, row.trigger_zone_label)}
          </div>
        </div>
      </div>

      {err && <div className="card err-text small">{err}</div>}

      <div style={{ display: "grid", gap: 12,
                    gridTemplateColumns:
                      "minmax(320px, 2fr) minmax(240px, 1fr)",
                    alignItems: "start" }}>
        {/* ── THE TEE ANGLE, AND THE ONE BUTTON ──────────────────── */}
        <div className="card tight">
          {row === null && !err && (
            <div className="shimmer" style={{ height: 260 }} />
          )}
          {row && !row.tee_url && (
            <div className="tiny muted" style={{ padding: 10 }}>
              This recording has no tee video — there is nothing to cut.
              {" "}Its source was most likely swept off the disk.
            </div>
          )}
          {row?.tee_url && (
            <>
              {/* NO `loop`. The end of the video is the signal that
                  the recording has been gone through, and a video
                  that quietly restarts destroys it. */}
              <video
                ref={video}
                src={row.tee_url}
                controls
                playsInline
                preload="auto"
                style={{ width: "100%", borderRadius: 6,
                         background: "#000", display: "block" }}
                onLoadedMetadata={(e) => setDur(e.target.duration || 0)}
                onTimeUpdate={(e) => setNow(e.target.currentTime || 0)}
                onEnded={() => setEnded(true)}
                onPlay={() => setEnded(false)}
              />
              <button type="button" className="btn"
                      style={{ width: "100%", marginTop: 8,
                               padding: "14px 0", fontSize: "1.05rem" }}
                      disabled={!row?.tee_url}
                      onClick={cutHere}>
                Start clip
              </button>
              <div className="tiny muted" style={{ marginTop: 6 }}>
                Cuts {CLIP_SEC} seconds forward from wherever the video is
                {" "}sitting — at {fmtClock(now)} that is
                {" "}{fmtClock(now)} → {fmtClock(
                  dur ? Math.min(now + CLIP_SEC, dur) : now + CLIP_SEC)}.
                {" "}The space bar does the same thing. The video keeps
                {" "}playing while each cut is made.
              </div>
            </>
          )}
        </div>

        {/* ── WHAT HAS BEEN MARKED ───────────────────────────────── */}
        <div>
          <div className="card tight">
            <div className="small">
              <b>Clips from this recording</b>{" "}
              <span className="pill small">{made} cut</span>
            </div>
            <div className="tiny muted" style={{ marginTop: 2 }}>
              {left !== null
                ? `${fmtClock(left)} of the recording left to watch`
                : "watch the recording and mark each swing"}
              {failed > 0 && ` · ${failed} failed`}
            </div>
          </div>

          <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
            {marks.length === 0 && (
              <div className="tiny muted" style={{ opacity: 0.6,
                                                   padding: "4px 2px" }}>
                Nothing marked yet.
              </div>
            )}
            {marks.map((m) => (
              <MarkRow key={m.n} mark={m} onRetry={retry}
                       onRemove={remove} />
            ))}
          </div>
        </div>
      </div>

      {/* ── THE END OF THE RECORDING ───────────────────────────────
          Shown once the video has run out, which is what "gone
          through the entire tee raw clip" means. It is an offer, not
          a redirect: see `goToCuts`. */}
      {ended && (
        <div className="card tight" style={{ marginTop: 10 }}>
          <div className="small">
            <b>That is the whole recording.</b>{" "}
            <span className="muted">
              {made} clip{made === 1 ? "" : "s"} cut from it
              {working && " · one still finishing"}
              {failed > 0 && ` · ${failed} failed`}
            </span>
          </div>
          <div className="row" style={{ gap: 8, marginTop: 8,
                                        flexWrap: "wrap" }}>
            <button type="button" className="btn" onClick={goToCuts}>
              Go to Audio cuts →
            </button>
            <button type="button" className="btn ghost small"
                    onClick={() => {
                      const v = video.current;
                      if (v) { v.currentTime = 0; v.play?.(); }
                      setEnded(false);
                    }}>
              watch it again
            </button>
            <Link to="/admin/production-flow" className="btn ghost small">
              back to Raw clips
            </Link>
          </div>
          {working && (
            <div className="tiny muted" style={{ marginTop: 6 }}>
              A cut is still being made. It will finish on the server
              {" "}whether you stay on this page or not.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
