/**
 * Trigger zones for ONE test run, drawn on a frame of the clip itself.
 *
 * WHY NOT REUSE THE CAMERA'S EDITOR. The boxes on the Cameras page are
 * what the rig records against: narrowing one there to try an idea on
 * yesterday's footage would change when the tee triggers on a live
 * hole, and the operator finding that out is a lost shoot. So these
 * boxes are passed with the request and stored nowhere. Closing the
 * panel, or clearing them, goes straight back to the camera's own.
 *
 * Boxes are FRACTIONS of the frame, never pixels: this <img> is
 * whatever width the layout gives it, and the clip may be 1080p or
 * 720p, so a pixel here means nothing on the server.
 */
import { useEffect, useRef, useState } from "react";

import { api } from "../api.js";

export function PiTestZones({ uploadId, adminPassword, zones, onChange }) {
  const [frame, setFrame] = useState(null);     // {image_url, total_frames}
  const [at, setAt] = useState(0);
  const [err, setErr] = useState(null);
  const [dragFrom, setDragFrom] = useState(null);
  const [draft, setDraft] = useState(null);
  const imgRef = useRef(null);

  useEffect(() => {
    let dead = false;
    api.getLongUploadFrame(adminPassword, uploadId, at, "tee")
      .then((f) => { if (!dead) { setFrame(f); setErr(null); } })
      .catch((e) => { if (!dead) setErr(e.message); });
    return () => { dead = true; };
  }, [uploadId, at, adminPassword]);

  function frac(e) {
    const r = imgRef.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return null;
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  }
  function onDown(e) {
    const p = frac(e);
    if (!p) return;
    e.preventDefault();
    setDragFrom(p);
    setDraft({ x: p.x, y: p.y, w: 0, h: 0 });
  }
  function onMove(e) {
    if (!dragFrom) return;
    const p = frac(e);
    if (!p) return;
    setDraft({
      x: Math.min(dragFrom.x, p.x),
      y: Math.min(dragFrom.y, p.y),
      w: Math.abs(p.x - dragFrom.x),
      h: Math.abs(p.y - dragFrom.y),
    });
  }
  function onUp() {
    if (!dragFrom) return;
    setDragFrom(null);
    // A CLICK IS NOT A BOX. Without this, every stray click on the
    // image added a zero-area zone, and a zone list with an empty box
    // in it measures nothing while looking like it measures something.
    if (draft && draft.w > 0.01 && draft.h > 0.01) {
      onChange([...(zones || []), draft]);
    }
    setDraft(null);
  }

  const boxes = zones || [];
  const pct = (v) => `${(v * 100).toFixed(3)}%`;

  return (
    <div style={{ marginTop: 8 }}>
      <div className="small muted" style={{ marginBottom: 4 }}>
        {boxes.length === 0 ? (
          <>No test zones — the run will use the <b>camera&apos;s own trigger
            zones</b>, which is what the rig does. Drag on the frame to try
            a different shape.</>
        ) : (
          <><b>{boxes.length} test zone{boxes.length === 1 ? "" : "s"}</b> —
            used for this run only. Nothing is saved, and the camera keeps
            triggering on its own boxes.</>
        )}
      </div>
      {err && <div className="warn-text small">frame: {err}</div>}
      {frame?.image_url && (
        <div
          style={{ position: "relative", display: "inline-block",
                   maxWidth: "100%", cursor: "crosshair", userSelect: "none" }}
          onMouseDown={onDown}
          onMouseMove={onMove}
          onMouseUp={onUp}
          onMouseLeave={onUp}
        >
          {/* HEIGHT IS CAPPED ON PURPOSE. At full size this frame runs
              ~850px and pushes the Test cut results a screen and a half
              down the page, which reads exactly like the button doing
              nothing -- it did, to the first person who used it. The
              overlays are positioned in percentages, so scaling the
              image scales the boxes with it. */}
          <img
            ref={imgRef}
            src={frame.image_url}
            alt="frame to draw test zones on"
            draggable={false}
            style={{ maxWidth: "100%", maxHeight: "48vh",
                     display: "block", borderRadius: 4 }}
          />
          {[...boxes, ...(draft ? [draft] : [])].map((b, i) => (
            <div
              key={i}
              style={{
                position: "absolute",
                left: pct(b.x), top: pct(b.y),
                width: pct(b.w), height: pct(b.h),
                border: `2px solid ${i < boxes.length ? "#2ecc71" : "#f1c40f"}`,
                background: "rgba(46,204,113,0.12)",
                pointerEvents: "none",
              }}
            />
          ))}
        </div>
      )}
      <div className="inline small" style={{ gap: 6, marginTop: 4, flexWrap: "wrap" }}>
        <button className="small" onClick={() => setAt(Math.max(0, at - 50))}>
          ◀ frame
        </button>
        <span className="muted">
          frame {frame?.frame ?? "…"}{frame?.total_frames ? ` / ${frame.total_frames}` : ""}
        </span>
        <button
          className="small"
          onClick={() => setAt(Math.min((frame?.total_frames || 1) - 1, at + 50))}
        >
          frame ▶
        </button>
        <button
          className="small"
          onClick={() => onChange([])}
          disabled={boxes.length === 0}
          title="Back to the camera's own trigger zones"
        >
          Clear ({boxes.length})
        </button>
        {boxes.length > 0 && (
          <button className="small" onClick={() => onChange(boxes.slice(0, -1))}>
            Undo last
          </button>
        )}
      </div>
    </div>
  );
}
