/**
 * THE TRIGGER ZONES, drawn on the camera's own live picture.
 *
 * A par 3 is played from a back tee and a middle tee — separate patches
 * of turf, tens of feet apart in the frame. One rectangle covering both
 * also covers the path, the bench and the walkway between them, and a
 * person crossing any of those is a recording nobody asked for. So the
 * camera gets one box per tee and fires when a golfer is in ANY of them.
 *
 * Drawn against the live frame rather than a saved still, because the
 * question being answered is "is the back tee inside this box RIGHT NOW"
 * — after a zoom nudge, after the mount was bumped, with today's markers
 * where they actually are. Zoom with the lens buttons and the picture
 * underneath moves while you draw.
 *
 * Coordinates are the camera's NATIVE pixels, taken from the image's
 * natural size, and the frame size is saved alongside them. The agent
 * scales to whatever it is really capturing — a box drawn at 1080p is
 * the wrong third of the picture at 720p.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../api.js";

const COLOURS = ["#38bdf8", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#fb7185"];
const MIN_SIDE = 24;          // native px; smaller is a mis-click, not a zone
const HANDLE = 10;            // screen px for the resize corner

function clampBox(b, fw, fh) {
  const w = Math.max(MIN_SIDE, Math.min(b.w, fw));
  const h = Math.max(MIN_SIDE, Math.min(b.h, fh));
  return {
    ...b,
    w, h,
    x: Math.max(0, Math.min(Math.round(b.x), fw - w)),
    y: Math.max(0, Math.min(Math.round(b.y), fh - h)),
  };
}

export default function TriggerZones({
  cam, adminPassword, frameW, frameH, portalTarget, onSaved, onClose,
}) {
  const [boxes, setBoxes] = useState(() => (cam.tee_zones?.boxes || []).map(
    (b) => ({ ...b })));
  const [sel, setSel] = useState(null);
  const [drag, setDrag] = useState(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState(null);
  const [err, setErr] = useState(null);
  const svgRef = useRef(null);

  // The saved zones were drawn against some frame size; if the camera is
  // capturing at another one now, what is on screen is the wrong scale.
  // Rescale once, so the editor always works in the CURRENT frame.
  const savedFrame = cam.tee_zones?.frame;
  useEffect(() => {
    if (!frameW || !frameH || !savedFrame) return;
    if (savedFrame.w === frameW && savedFrame.h === frameH) return;
    const sx = frameW / savedFrame.w;
    const sy = frameH / savedFrame.h;
    setBoxes((bs) => bs.map((b) => ({
      ...b,
      x: Math.round(b.x * sx), y: Math.round(b.y * sy),
      w: Math.round(b.w * sx), h: Math.round(b.h * sy),
    })));
    setNote(`Rescaled from the ${savedFrame.w}×${savedFrame.h} frame they `
      + `were drawn on to this ${frameW}×${frameH} one — check them, then save.`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameW, frameH]);

  if (!frameW || !frameH) {
    return (
      <div className="tiny" style={{ color: "#bbb", padding: 8 }}>
        Waiting for a frame to draw on…
      </div>
    );
  }

  // Screen → native pixels. The overlay covers the picture exactly, so
  // its own box is the scale.
  function toNative(evt) {
    const r = svgRef.current.getBoundingClientRect();
    return {
      x: ((evt.clientX - r.left) / r.width) * frameW,
      y: ((evt.clientY - r.top) / r.height) * frameH,
      scale: frameW / r.width,
    };
  }

  function onDown(evt, index, corner) {
    evt.preventDefault();
    evt.stopPropagation();
    svgRef.current.setPointerCapture?.(evt.pointerId);
    const p = toNative(evt);
    if (index == null) {
      // Empty picture: start a new zone at this corner.
      if (boxes.length >= 6) {
        setErr("Six zones is the limit — delete one first.");
        return;
      }
      const i = boxes.length;
      setBoxes((bs) => [...bs, { x: Math.round(p.x), y: Math.round(p.y),
                                 w: MIN_SIDE, h: MIN_SIDE,
                                 label: i === 0 ? "Back" : i === 1 ? "Middle" : "" }]);
      setSel(i);
      setDrag({ mode: "size", i, ox: p.x, oy: p.y });
      return;
    }
    setSel(index);
    setDrag(corner
      ? { mode: "size", i: index, ox: boxes[index].x, oy: boxes[index].y }
      : { mode: "move", i: index, dx: p.x - boxes[index].x,
          dy: p.y - boxes[index].y });
  }

  function onMove(evt) {
    if (!drag) return;
    const p = toNative(evt);
    setErr(null);
    setBoxes((bs) => bs.map((b, i) => {
      if (i !== drag.i) return b;
      if (drag.mode === "move") {
        return clampBox({ ...b, x: p.x - drag.dx, y: p.y - drag.dy },
                        frameW, frameH);
      }
      return clampBox({
        ...b,
        x: Math.min(drag.ox, p.x), y: Math.min(drag.oy, p.y),
        w: Math.abs(p.x - drag.ox), h: Math.abs(p.y - drag.oy),
      }, frameW, frameH);
    }));
  }

  function onUp() { setDrag(null); }

  function removeSelected() {
    if (sel == null) return;
    setBoxes((bs) => bs.filter((_, i) => i !== sel));
    setSel(null);
  }

  async function save() {
    setSaving(true);
    setErr(null);
    setNote(null);
    try {
      const out = await api.setTeeZones(
        adminPassword, cam.id, boxes, frameW, frameH);
      setNote(out.note || "saved");
      onSaved?.(out);
    } catch (e) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  const dirty = JSON.stringify(boxes)
    !== JSON.stringify((cam.tee_zones?.boxes || []).map((b) => ({ ...b })));

  const surface = (
      <svg
        ref={svgRef}
        viewBox={`0 0 ${frameW} ${frameH}`}
        preserveAspectRatio="none"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                 cursor: drag ? "grabbing" : "crosshair", touchAction: "none" }}
        onPointerDown={(e) => onDown(e, null, false)}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      >
        {/* Everything outside the zones is dimmed, so what the camera
            ignores reads as ignored rather than as unmarked. */}
        <defs>
          <mask id={`zm-${cam.id}`}>
            <rect x="0" y="0" width={frameW} height={frameH} fill="#fff" />
            {boxes.map((b, i) => (
              <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill="#000" />
            ))}
          </mask>
        </defs>
        <rect x="0" y="0" width={frameW} height={frameH}
              fill="rgba(0,0,0,0.45)" mask={`url(#zm-${cam.id})`} />
        {boxes.map((b, i) => {
          const c = COLOURS[i % COLOURS.length];
          // EVERYTHING DRAWN HERE IS IN NATIVE PIXELS, because the
          // viewBox is. A 2px stroke is a hairline on a 1920-wide frame
          // and a fence post on a 300-wide one, so the furniture is
          // sized as a fraction of the frame instead of in absolutes.
          const u = Math.max(frameW, frameH) / 400;
          // The grab handle is the exception: it wants to stay a
          // finger's width on screen whatever the frame is, so it
          // converts from screen pixels through the current scale.
          const hs = HANDLE * (frameW / (svgRef.current?.clientWidth || frameW));
          return (
            <g key={i}>
              <rect
                x={b.x} y={b.y} width={b.w} height={b.h}
                fill={i === sel ? "rgba(255,255,255,0.08)" : "transparent"}
                stroke={c} strokeWidth={(i === sel ? 1.6 : 1.1) * u}
                style={{ cursor: "grab" }}
                onPointerDown={(e) => onDown(e, i, false)}
              />
              <text x={b.x + 4 * u} y={b.y + 15 * u} fill={c}
                    fontSize={12 * u} fontWeight="700"
                    style={{ pointerEvents: "none",
                             paintOrder: "stroke", stroke: "rgba(0,0,0,.7)",
                             strokeWidth: 2.5 * u }}>
                {b.label || `Zone ${i + 1}`}
              </text>
              {/* Bottom-right corner resizes. */}
              <rect
                x={b.x + b.w - hs} y={b.y + b.h - hs} width={hs} height={hs}
                fill={c} style={{ cursor: "nwse-resize" }}
                onPointerDown={(e) => onDown(e, i, true)}
              />
            </g>
          );
        })}
      </svg>
  );

  return (
    <>
      {portalTarget ? createPortal(surface, portalTarget) : surface}
      <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap",
                    gap: 6, alignItems: "center" }}>
        <span className="tiny" style={{ color: "#bbb", width: "100%" }}>
          Drag on the picture to add a zone — one per tee. Drag a box to
          move it, its corner to resize. A golfer in <b>any</b> zone
          triggers; the ground between them does not.
        </span>
        {boxes.map((b, i) => (
          <button
            key={i} type="button"
            className={i === sel ? "small" : "secondary small"}
            style={{ width: "auto",
                     borderColor: COLOURS[i % COLOURS.length] }}
            onClick={() => setSel(i)}
          >
            {b.label || `Zone ${i + 1}`}
          </button>
        ))}
        {sel != null && boxes[sel] && (
          <>
            <input
              type="text"
              value={boxes[sel].label || ""}
              placeholder="name this tee"
              onChange={(e) => setBoxes((bs) => bs.map((b, i) => (
                i === sel ? { ...b, label: e.target.value.slice(0, 24) } : b
              )))}
              style={{ width: 130, padding: "4px 8px", fontSize: 13 }}
            />
            <button type="button" className="ghost small err-text"
                    style={{ width: "auto" }} onClick={removeSelected}>
              Delete zone
            </button>
          </>
        )}
        <button type="button" className="small" style={{ width: "auto" }}
                disabled={saving || !dirty} onClick={save}>
          {saving ? "Saving…" : `Save ${boxes.length} zone${boxes.length === 1 ? "" : "s"}`}
        </button>
        <button type="button" className="ghost small" style={{ width: "auto" }}
                onClick={onClose}>
          Done
        </button>
        {note && <span className="tiny" style={{ color: "#8fd3a6" }}>{note}</span>}
        {err && <span className="tiny err-text">{err}</span>}
      </div>
    </>
  );
}
