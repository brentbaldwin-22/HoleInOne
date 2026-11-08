/**
 * Where the ball finished, for one capture's green clip.
 *
 * A PICTURE, NOT COORDINATES. "1247, 638" says nothing about whether
 * the thing found is a ball, so this shows the two plates the detector
 * compared — before on the left, after on the right — with what it
 * found ringed on them. The comparison it made is the comparison the
 * eye makes, which is the only way to judge a run of it.
 *
 * The two time boxes matter more than they look. A green capture runs
 * well past the shot, and on a short hole the group can be up and
 * marking the ball before the clip stops; the after plate then holds a
 * green with nothing new on it, which reads exactly like a miss. When
 * a run comes back empty, moving the after plate back is the first
 * thing to try, not redrawing the outline.
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";

/**
 * Every picture the run actually looked at, in the order it looked.
 *
 * ONE SIDE-BY-SIDE AT THE END IS A VERDICT, NOT A METHOD. It answers
 * "is the thing it found a ball" and nothing else, and the four
 * questions it cannot answer are the four places this goes wrong: is
 * the before plate clean, is the after plate from before the group
 * walked on, did the exposure match take, and what crossed the
 * threshold. Each of those is a picture, and each one is here.
 */
function Stages({ stages }) {
  const [open, setOpen] = useState(null);
  if (!stages?.length) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div className="small"><b>What it looked at, in order</b></div>
      <div style={{ display: "grid", gap: 10, marginTop: 6,
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(240px, 1fr))" }}>
        {stages.map((st) => (
          <figure key={st.name} style={{ margin: 0 }}>
            <a href={st.url} target="_blank" rel="noreferrer"
               onClick={(e) => { e.preventDefault(); setOpen(st); }}>
              <img src={st.url} alt={st.title}
                   style={{ width: "100%", borderRadius: 4,
                            background: "#111", cursor: "zoom-in",
                            border: "1px solid var(--border, #333)" }} />
            </a>
            <figcaption className="tiny" style={{ marginTop: 3 }}>
              <b>{st.title}</b>
              <div className="muted" style={{ marginTop: 2 }}>{st.why}</div>
            </figcaption>
          </figure>
        ))}
      </div>
      {/* FULL SIZE, BECAUSE A BALL IS FOUR PIXELS. A thumbnail of a
          green is a picture in which the thing being judged is not
          visible, which makes the gallery decorative. */}
      {open && (
        <div
          onClick={() => setOpen(null)}
          style={{ position: "fixed", inset: 0, zIndex: 95,
                   background: "rgba(0,0,0,0.9)", display: "flex",
                   flexDirection: "column", alignItems: "center",
                   justifyContent: "center", padding: 12, cursor: "zoom-out" }}
        >
          <img src={open.url} alt={open.title}
               style={{ maxWidth: "100%", maxHeight: "86vh",
                        objectFit: "contain" }} />
          <div className="small" style={{ color: "#eee", marginTop: 8,
                                          maxWidth: 900 }}>
            <b>{open.title}</b> — {open.why}
          </div>
        </div>
      )}
    </div>
  );
}

/** One row of the funnel: what went in, what came out, and the gate. */
function Step({ n, title, count, units, children, bad }) {
  return (
    <div style={{ marginTop: 8, paddingLeft: 10,
                  borderLeft: `2px solid ${bad ? "#fbbf24" : "#2f6feb"}` }}>
      <div className="small">
        <b>{n} · {title}</b>
        {count != null && (
          <span className="muted">
            {" "}— {count}{units ? ` ${units}` : ""}
          </span>
        )}
      </div>
      <div className="tiny muted" style={{ marginTop: 2 }}>{children}</div>
    </div>
  );
}

/**
 * THE WORK, NOT ONLY THE ANSWER.
 *
 * "1 arrival(s), 0 departure(s)" is a result, and a result is not
 * something anybody can make more accurate. Every number below comes
 * from the run that just happened: how many pixels were searched, how
 * many were bright enough to be a candidate, how many of those were
 * ball-shaped, and which gate each of the rest died on. A run that
 * finds nothing now says WHICH of the four quite different failures
 * it was, and they need opposite fixes.
 */
function HowItFound({ out }) {
  const st = out.steps || {};
  const dl = out.dials || {};
  const pl = out.plates || {};
  const ai = out.ai || null;
  const rej = out.rejected || [];
  const ai_used = out.source === "ai";
  return (
    <details style={{ marginTop: 12 }}>
      <summary className="small" style={{ cursor: "pointer" }}>
        <b>How it found it</b>
        <span className="muted">
          {" "}— the six steps, with this run&apos;s numbers
        </span>
      </summary>

      <div className="tiny muted" style={{ marginTop: 8 }}>
        A ball at rest is not a motion problem. Every other detector in
        this app is a frame difference, and a stationary ball produces
        no frame-to-frame difference at all — it is not merely hard for
        them to find, it is invisible to them. So this asks a different
        question: not <i>what moved</i>, but <i>what is on the green now
        that was not on it before</i>.
      </div>

      <Step n="1" title="Ask the model first"
            count={ai_used ? "answered" : "not used"}
            bad={!ai_used}>
        Two colour frames, a yellow grid drawn over both so a position
        can be read off a cell rather than guessed from the frame edge,
        and one question: where is the golf ball in the AFTER frame. It
        needs no outline and no plates. Its answer is snapped onto the
        nearest ball-sized blob when there is one within reach, so a
        position that is close becomes a position that is exact.
        {ai?.crop && (
          <div style={{ marginTop: 3 }}>
            {ai.crop.applied
              ? <>cropped to the green at {ai.crop.origin?.join(",")}, sent{" "}
                  {ai.crop.size?.join("x")} at {ai.crop.scale}x — the
                  model&apos;s fixed budget of attention is spent on the
                  putting surface instead of on sky and treeline, and every
                  white thing outside the outline is simply not in the
                  picture to be mistaken for a ball</>
              : <>sent the whole frame — {ai.crop.why}</>}
          </div>
        )}
        {ai && (
          <div style={{ marginTop: 3 }}>
            {ai.ran
              ? <>asked {ai.model}
                {ai.confidence != null
                  ? ` · ${Math.round(ai.confidence * 100)}% confident`
                  : ""}
                {ai.min_confidence != null
                  ? ` (needs ${Math.round(ai.min_confidence * 100)}%)`
                  : ""}
                {ai.error ? ` · NOT USED: ${ai.error}` : " · used"}
                {ai.why ? <div style={{ marginTop: 2 }}>“{ai.why}”</div>
                  : null}
              </>
              : <>not asked — {ai.error || "no model configured"}</>}
          </div>
        )}
      </Step>

      {!ai_used && (
        <>
          <Step n="2" title="Build two plates"
                count={out.timing?.frames_read} units="frames read">
            A plate is the <b>median</b> of {dl.plate_frames || 31} frames
            spread over {dl.plate_span_sec || 4} seconds — not consecutive
            frames, which at 60fps is half a second over which a bush has
            barely moved. The median is what makes this work in clutter:
            brush swaying in the wind medians to its mean position in BOTH
            plates and cancels, and a golfer walking across is in a
            minority of frames at any one pixel so the median throws them
            out. The ball survives because it is genuinely static in the
            second plate and genuinely absent from the first.
            {pl.before_sec != null && (
              <div style={{ marginTop: 3 }}>
                before plate centred {pl.before_sec}s, after plate{" "}
                {pl.after_sec}s — {pl.gap_sec}s apart,{" "}
                {pl.frames_each} frames each
              </div>
            )}
          </Step>

          <Step n="3" title="Match the exposure, then subtract"
                count={out.exposure_shift != null
                  ? `${out.exposure_shift > 0 ? "+" : ""}`
                    + `${out.exposure_shift} levels, gain `
                    + `${out.exposure_gain}`
                  : null}>
            The camera&apos;s auto-exposure moves between two plates taken
            a minute apart. Without correcting for it first, every
            textured patch on the green reads as something that arrived
            or left. The before plate is scaled and shifted onto the
            after plate inside the outline, and only then subtracted.
            Pixels at least <b>{dl.diff_threshold || 18} grey levels</b>
            {" "}brighter than before are arrivals; the same amount
            dimmer are departures.
            {st.searched_px != null && (
              <div style={{ marginTop: 3 }}>
                {st.searched_px.toLocaleString()} px searched inside the
                outline · {(st.bright_px || 0).toLocaleString()} brighter ·
                {" "}{(st.dim_px || 0).toLocaleString()} dimmer
              </div>
            )}
          </Step>

          <Step n="4" title="Keep only the ball-shaped ones"
                count={st.ball_shaped != null
                  ? `${st.ball_shaped} of ${st.components || 0}`
                  : null}
                bad={st.components > 0 && !st.ball_shaped}>
            The bright pixels are grouped into connected components, and
            each is held to two tests: an area between{" "}
            <b>{dl.min_area}</b> and <b>{dl.max_area}</b> px — wide,
            because a green camera sees the front of the green at a very
            different scale from the back — and a bounding box no longer
            than <b>{dl.max_aspect}:1</b>. A ball is round; a mowing
            stripe, a shadow edge and a sprinkler line are not, and they
            are exactly what survives the area test.
            {st.components > 0 && (
              <div style={{ marginTop: 3 }}>
                {st.too_small ? `${st.too_small} too small · ` : ""}
                {st.too_big ? `${st.too_big} too big · ` : ""}
                {st.not_round ? `${st.not_round} not round · ` : ""}
                {st.ball_shaped || 0} ball-shaped
              </div>
            )}
          </Step>

          <Step n="5" title="Is it there in every frame?"
                count={st.kept != null
                  ? `${st.kept} of ${st.ball_shaped || 0}`
                  : null}
                bad={st.not_persistent > 0 && !st.kept}>
            The median&apos;s blind spot: something that sat still for
            half the window shows up in it. A ball that has come to rest
            is in EVERY frame, so{" "}
            {dl.persist_samples || 7} of the after frames are checked
            individually and at least{" "}
            <b>{Math.round((dl.persist_frac || 0.7) * 100)}%</b> of them
            have to still show it — at{" "}
            {Math.round((dl.persist_rel || 0.6) * 100)}% of the full
            threshold, because one frame carries its own noise where a
            median does not.
            {st.not_persistent
              ? <div style={{ marginTop: 3, color: "#fbbf24" }}>
                  {st.not_persistent} ball-shaped candidate(s) failed
                  here — either a person who stood still for part of the
                  window, or the ball itself in shade for a few frames.
                </div>
              : null}
          </Step>

          <Step n="6" title="Rank, and say what left too"
                count={(out.arrivals || []).length} units="arrival(s)">
            Steadiest first, then brightest — <i>not</i> biggest: at the
            back of a green the ball is a handful of pixels and the
            biggest thing that survives the filters is more likely to be
            a shadow. A green very often already has a ball on it, and
            if it is marked between the two plates it appears as a{" "}
            <b>departure</b> — reported rather than ignored, because an
            operator reading a confusing answer deserves to know a ball
            left.
          </Step>
        </>
      )}

      {rej.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="small">
            <b>Turned down</b>
            <span className="muted">
              {" "}— {out.n_rejected} candidate(s), the{" "}
              {rej.length} biggest shown, and the grey squares on the
              picture are these
            </span>
          </div>
          <div style={{ maxHeight: 190, overflow: "auto", marginTop: 4 }}>
            <table className="tiny" style={{ width: "100%",
                                             borderCollapse: "collapse" }}>
              <tbody>
                {rej.map((r, i) => (
                  <tr key={i}>
                    <td className="muted" style={{ padding: "1px 6px 1px 0",
                                                   whiteSpace: "nowrap" }}>
                      {r.x}, {r.y}
                    </td>
                    <td className="muted" style={{ padding: "1px 6px 1px 0",
                                                   whiteSpace: "nowrap" }}>
                      {r.area}px · {r.strength} lv
                    </td>
                    <td className="muted" style={{ padding: "1px 0" }}>
                      {r.why}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </details>
  );
}

export default function GreenRestModal({ row, adminPassword, onClose }) {
  const [beforeSec, setBeforeSec] = useState("");
  const [afterSec, setAfterSec] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);
  const [err, setErr] = useState(null);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      setOut(await api.greenRestForUpload(adminPassword, row.id, {
        before_sec: beforeSec, after_sec: afterSec,
      }));
    } catch (e) {
      setErr(e.message || "could not run the finder");
    } finally {
      setBusy(false);
    }
  }

  // Run once on open, with the default plates, so the dialog opens on
  // an answer rather than on a form.
  useEffect(() => { run(); /* eslint-disable-next-line */ }, []);

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 90,
               background: "rgba(0,0,0,0.65)", display: "flex",
               alignItems: "center", justifyContent: "center", padding: 16 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card"
        style={{ maxWidth: 1100, width: "100%", maxHeight: "90vh",
                 overflow: "auto" }}
      >
        <div className="row" style={{ alignItems: "center", gap: 10 }}>
          <b style={{ flex: 1 }}>
            Ball on the green — #{row.id}
          </b>
          <button type="button" className="ghost small"
                  style={{ width: "auto" }} onClick={onClose}>
            Close
          </button>
        </div>

        {err && <div className="err-text small" style={{ marginTop: 8 }}>{err}</div>}

        {out && !out.has_outline && (
          <div className="small" style={{ marginTop: 8, color: "#fbbf24" }}>
            No green outline on this camera, so the whole frame was
            searched. Draw one on the Cameras page — a white dot short of
            the green is identical to one on it except for where it is.
          </div>
        )}

        {out?.image_url && (
          <img
            src={out.image_url}
            alt="before and after plates"
            style={{ width: "100%", marginTop: 10, borderRadius: 6,
                     background: "#111" }}
          />
        )}

        <Stages stages={out?.stages} />

        <div className="row" style={{ gap: 8, marginTop: 10,
                                      alignItems: "center", flexWrap: "wrap" }}>
          <label className="tiny muted"
                 style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            before (s)
            <input type="number" min="0" step="0.5" value={beforeSec}
                   placeholder="auto"
                   onChange={(e) => setBeforeSec(e.target.value)}
                   style={{ width: 70, padding: "3px 6px", fontSize: 12 }} />
          </label>
          <label className="tiny muted"
                 style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            after (s)
            <input type="number" min="0" step="0.5" value={afterSec}
                   placeholder="auto"
                   onChange={(e) => setAfterSec(e.target.value)}
                   style={{ width: 70, padding: "3px 6px", fontSize: 12 }} />
          </label>
          <button type="button" className="small" style={{ width: "auto" }}
                  disabled={busy} onClick={run}>
            {busy ? "Looking…" : "Run again"}
          </button>
        </div>

        {out && (
          <div className="small" style={{ marginTop: 10 }}>
            <div>
              {out.best
                ? (out.source === "ai"
                  ? `Ball at ${out.best.x}, ${out.best.y} — `
                    + `${Math.round((out.confidence || 0) * 100)}% confident, `
                    + (out.on_green ? "on the green" : "NOT on the green")
                  : `At rest near ${out.best.x}, ${out.best.y} — `
                    + `${out.best.area}px, present in `
                    + `${Math.round((out.best.persist || 0) * 100)}% of the `
                    + "after frames")
                : "No ball found on the green."}
              {out.source && (
                <span className="muted">
                  {" "}· {out.source === "ai" ? "found by the model"
                    : "plate comparison"}
                </span>
              )}
            </div>
            <div className="muted" style={{ marginTop: 2 }}>
              {out.source === "ai" ? (out.clip || "") : (
                <>plates at {(out.before_frame / (out.fps || 60)).toFixed(1)}s
                  {" "}and {(out.after_frame / (out.fps || 60)).toFixed(1)}s
                  {out.clip ? ` · ${out.clip}` : ""}</>
              )}
            </div>
            {out.timing && (
              <div className="muted" style={{ marginTop: 2 }}>
                took {(
                  (out.timing.fetch_sec || 0) + (out.timing.read_sec || 0)
                  + (out.timing.detect_sec || 0) + (out.timing.render_sec || 0)
                ).toFixed(1)}s — {out.timing.fetch_sec || 0}s fetching the
                clip, {out.timing.read_sec || 0}s reading{" "}
                {out.timing.frames_read} frames of {out.timing.clip_frames},{" "}
                {out.timing.detect_sec || 0}s comparing
              </div>
            )}
            {(out.notes || []).filter(Boolean).map((n, i) => (
              <div key={i} className="muted" style={{ marginTop: 2 }}>{n}</div>
            ))}
            {!out.best && (
              <div style={{ marginTop: 6, color: "#fbbf24" }}>
                Try moving the <b>after</b> plate earlier — 20 or 30 seconds.
                If the group reached the green before the clip stopped, the
                ball is in somebody&apos;s pocket by the end of it, and that
                looks identical to a miss from here.
              </div>
            )}

            <HowItFound out={out} />
          </div>
        )}
      </div>
    </div>
  );
}
