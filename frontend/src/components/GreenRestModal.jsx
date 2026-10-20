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
                ? `At rest near ${out.best.x}, ${out.best.y} — ${out.best.area}px, `
                  + `present in ${Math.round((out.best.persist || 0) * 100)}% `
                  + "of the after frames"
                : "Nothing new on the green."}
            </div>
            <div className="muted" style={{ marginTop: 2 }}>
              plates at {(out.before_frame / (out.fps || 60)).toFixed(1)}s and{" "}
              {(out.after_frame / (out.fps || 60)).toFixed(1)}s
              {out.clip ? ` · ${out.clip}` : ""}
            </div>
            {(out.notes || []).map((n, i) => (
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
          </div>
        )}
      </div>
    </div>
  );
}
