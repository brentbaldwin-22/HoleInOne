/**
 * The putting surface, clicked on the green camera's own picture.
 *
 * WHY A POLYGON AND NOT A BOX. The thing this outline has to exclude is
 * the apron and the fairway short of it, and a bounding box around a
 * green contains exactly the ground a ball most often finishes on when
 * it misses. Greens are not rectangles and the mistake it has to
 * prevent is the shape, not the size.
 *
 * WHAT IT IS FOR. The rest-spot finder compares a median frame from
 * before the shot with one from after it, and reports what is newly on
 * the green. The median handles the brush and the people on its own —
 * swaying foliage medians to its mean position in both plates, and a
 * golfer crossing is a minority of frames at any one pixel. What a
 * median cannot do is tell a white dot on the putting surface from an
 * identical white dot ten yards short of it. Only position can, and
 * this is how the surface gets described.
 *
 * Stored in NATIVE pixels with the frame size beside it, like the
 * trigger zones and for the same reason: a polygon drawn at 1080p means
 * somewhere else entirely if the camera is recaptured at 720p.
 */
import { useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../api.js";

const EDGE = "#34d399";
const FOUND = "#f59e0b";

export default function GreenOutline({
  cam, adminPassword, frameW, frameH, portalTarget, onSaved, onClose,
}) {
  const saved = cam.green_roi?.points || [];
  const savedFrame = cam.green_roi?.frame;
  // Rescale once into the CURRENT frame, so the editor always works in
  // the pixels on screen rather than in whatever they were drawn at.
  const [pts, setPts] = useState(() => {
    if (!savedFrame || !frameW || !frameH) return saved.map((p) => [...p]);
    if (savedFrame.w === frameW && savedFrame.h === frameH) {
      return saved.map((p) => [...p]);
    }
    const sx = frameW / savedFrame.w;
    const sy = frameH / savedFrame.h;
    return saved.map(([x, y]) => [Math.round(x * sx), Math.round(y * sy)]);
  });
  const [drag, setDrag] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  // WHEN THE TWO PLATES ARE TAKEN, in seconds from the start of the
  // clip. Blank means each end, which is right until the group reaches
  // the green before the capture stops — then the after plate shows a
  // green with nothing new on it, which reads exactly like a miss.
  const [beforeSec, setBeforeSec] = useState("");
  const [afterSec, setAfterSec] = useState("");
  const [note, setNote] = useState(null);
  const [err, setErr] = useState(null);
  const svgRef = useRef(null);

  function at(e) {
    const r = svgRef.current.getBoundingClientRect();
    return [
      Math.round(((e.clientX - r.left) / r.width) * frameW),
      Math.round(((e.clientY - r.top) / r.height) * frameH),
    ];
  }

  function onDown(e, i) {
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    if (i == null) setPts((p) => [...p, at(e)]);
    else setDrag(i);
  }

  function onMove(e) {
    if (drag == null) return;
    const p = at(e);
    setPts((old) => old.map((q, i) => (i === drag ? p : q)));
  }

  async function save() {
    setSaving(true);
    setErr(null);
    setNote(null);
    try {
      await api.setGreenOutline(adminPassword, cam.id, pts, frameW, frameH);
      setNote(pts.length ? `Saved ${pts.length} points` : "Outline cleared");
      onSaved?.();
    } catch (ex) {
      setErr(ex.message || "could not save the outline");
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setErr(null);
    setResult(null);
    try {
      setResult(await api.greenRestTest(adminPassword, cam.id, {
        before_sec: beforeSec, after_sec: afterSec,
      }));
    } catch (ex) {
      setErr(ex.message || "could not run the finder");
    } finally {
      setTesting(false);
    }
  }

  const u = Math.max(frameW || 1, frameH || 1) / 400;
  const closed = pts.length >= 3;
  const dirty = JSON.stringify(pts) !== JSON.stringify(saved);

  const surface = (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${frameW} ${frameH}`}
      preserveAspectRatio="none"
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
               cursor: drag != null ? "grabbing" : "crosshair",
               touchAction: "none" }}
      onPointerDown={(e) => onDown(e, null)}
      onPointerMove={onMove}
      onPointerUp={() => setDrag(null)}
      onPointerCancel={() => setDrag(null)}
    >
      {/* Everything outside the green is dimmed, so the ground that can
          never hold an answer reads as excluded rather than unmarked. */}
      {closed && (
        <>
          <defs>
            <mask id={`gm-${cam.id}`}>
              <rect x="0" y="0" width={frameW} height={frameH} fill="#fff" />
              <polygon points={pts.map((p) => p.join(",")).join(" ")}
                       fill="#000" />
            </mask>
          </defs>
          <rect x="0" y="0" width={frameW} height={frameH}
                fill="rgba(0,0,0,0.45)" mask={`url(#gm-${cam.id})`} />
          <polygon points={pts.map((p) => p.join(",")).join(" ")}
                   fill="rgba(52,211,153,0.10)" stroke={EDGE}
                   strokeWidth={1.4 * u} />
        </>
      )}
      {!closed && pts.length > 1 && (
        <polyline points={pts.map((p) => p.join(",")).join(" ")}
                  fill="none" stroke={EDGE} strokeWidth={1.4 * u}
                  strokeDasharray={`${4 * u} ${3 * u}`} />
      )}
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={4 * u} fill={EDGE}
                stroke="rgba(0,0,0,.6)" strokeWidth={1.2 * u}
                style={{ cursor: "grab" }}
                onPointerDown={(e) => onDown(e, i)} />
      ))}
      {/* What the finder made of it, on the same picture — the only way
          to judge an outline is to see what it lets through. */}
      {(result?.arrivals || []).map((a, i) => (
        <g key={`a${i}`}>
          <circle cx={a.x} cy={a.y} r={7 * u} fill="none" stroke={FOUND}
                  strokeWidth={1.6 * u} />
          <text x={a.x + 10 * u} y={a.y - 8 * u} fill={FOUND}
                fontSize={11 * u} fontWeight="700"
                style={{ pointerEvents: "none", paintOrder: "stroke",
                         stroke: "rgba(0,0,0,.7)", strokeWidth: 2.5 * u }}>
            {i === 0 ? "at rest" : `also #${i + 1}`}
          </text>
        </g>
      ))}
      {(result?.departures || []).map((d, i) => (
        <circle key={`d${i}`} cx={d.x} cy={d.y} r={6 * u} fill="none"
                stroke="#94a3b8" strokeWidth={1.2 * u}
                strokeDasharray={`${3 * u} ${2 * u}`} />
      ))}
    </svg>
  );

  return (
    <>
      {portalTarget ? createPortal(surface, portalTarget) : surface}
      <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap",
                    gap: 6, alignItems: "center" }}>
        <span className="tiny" style={{ color: "#bbb", width: "100%" }}>
          Click round the <b>putting surface</b> to drop corners; drag one
          to move it. Follow the edge of the mown green, not a box around
          it — the apron and the fairway short of it are where a missed
          shot finishes, and a white dot there looks exactly like a white
          dot on the green. Three corners minimum. <b>Test</b> runs the
          finder on this camera&apos;s most recent clip and rings what it
          found, so you can see what the outline lets through before
          anything depends on it.
        </span>
        <button type="button" className="small" style={{ width: "auto" }}
                disabled={saving || !dirty || (pts.length > 0 && !closed)}
                onClick={save}>
          {saving ? "Saving…"
            : pts.length ? `Save ${pts.length} points` : "Clear outline"}
        </button>
        <button type="button" className="secondary small"
                style={{ width: "auto" }} disabled={testing} onClick={test}>
          {testing ? "Looking…" : "🔍 Test on last clip"}
        </button>
        <label className="tiny muted"
               style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
               title="Seconds from the start of the clip. Blank = near the start.">
          before
          <input type="number" min="0" step="0.5" value={beforeSec}
                 placeholder="auto"
                 onChange={(e) => setBeforeSec(e.target.value)}
                 style={{ width: 62, padding: "3px 6px", fontSize: 12 }} />
        </label>
        <label className="tiny muted"
               style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
               title="Seconds from the start of the clip. Blank = near the end — pull it back if the group reached the green before the capture stopped.">
          after
          <input type="number" min="0" step="0.5" value={afterSec}
                 placeholder="auto"
                 onChange={(e) => setAfterSec(e.target.value)}
                 style={{ width: 62, padding: "3px 6px", fontSize: 12 }} />
        </label>
        {pts.length > 0 && (
          <button type="button" className="ghost small"
                  style={{ width: "auto" }}
                  onClick={() => setPts((p) => p.slice(0, -1))}>
            Undo point
          </button>
        )}
        {pts.length > 0 && (
          <button type="button" className="ghost small err-text"
                  style={{ width: "auto" }} onClick={() => setPts([])}>
            Start over
          </button>
        )}
        <button type="button" className="ghost small" style={{ width: "auto" }}
                onClick={onClose}>
          Done
        </button>
        {pts.length > 0 && !closed && (
          <span className="tiny err-text">
            A polygon needs at least 3 corners.
          </span>
        )}
        {note && <span className="tiny" style={{ color: "#8fd3a6" }}>{note}</span>}
        {err && <span className="tiny err-text">{err}</span>}
        {result && (
          <div className="tiny" style={{ width: "100%", marginTop: 4,
                                         color: "#ddd" }}>
            <div>
              {result.best
                ? `Ball at rest near ${result.best.x}, ${result.best.y} `
                  + `(${result.best.area}px, seen in `
                  + `${Math.round((result.best.persist || 0) * 100)}% of the `
                  + "after frames)"
                : "Nothing new found on the green."}
              {" · "}plates at {(result.before_frame / (result.fps || 60))
                .toFixed(1)}s and {(result.after_frame / (result.fps || 60))
                .toFixed(1)}s
              {result.clip ? ` · ${result.clip}` : ""}
            </div>
            {(result.notes || []).map((n, i) => (
              <div key={i} className="muted" style={{ marginTop: 2 }}>{n}</div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
