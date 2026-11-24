/*
 * The operator console's video viewer.
 *
 * EXTRACTED FROM AdminProduction, not copied. It was defined inside
 * that page and not exported, so a second screen wanting "open the
 * tee angle the way Production does" had the choice of a near-copy
 * that drifts or an import that could not be written. The overlay --
 * wall clock and frame number over the picture -- is the whole reason
 * the operator opens a video here rather than in a new tab, and a
 * second implementation of it would be a second answer to "what frame
 * is this", which is the question the console exists to settle.
 */
import { useEffect, useRef, useState } from "react";

import { parseApiDate } from "../time.js";

function fmtClock(ms) {
  // HH:MM:SS.mmm in US Central time (auto CST/CDT). Both the tee and
  // green overlays use the same zone, so at the same real instant they
  // read identically — that's what lets the operator eyeball sync.
  const d = new Date(ms);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    hourCycle: "h23",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(d);
  const get = (t) => parts.find((p) => p.type === t)?.value || "00";
  const millis = String(d.getMilliseconds()).padStart(3, "0");
  return `${get("hour")}:${get("minute")}:${get("second")}.${millis}`;
}

export function VideoLightbox({ url, title, startedAt, startedApprox, fps, onClose }) {
  const videoRef = useRef(null);
  const [curTime, setCurTime] = useState(0);
  const startMs = startedAt ? parseApiDate(startedAt)?.getTime() ?? null : null;
  const hasFps = !!(fps && fps > 0);
  const showOverlay = startMs != null || hasFps;

  // Track the video's currentTime via rAF so the clock + frame
  // readouts stay smooth during playback and update on seek/scrub.
  useEffect(() => {
    if (!url || !showOverlay) return undefined;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setCurTime(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [url, showOverlay]);

  if (!url) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title || "Video viewer"}
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.85)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000, padding: 24, cursor: "zoom-out",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: "min(1280px, 95vw)", width: "100%",
          cursor: "default",
        }}
      >
        <div
          className="row"
          style={{
            color: "#fff", marginBottom: 8, alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <b style={{ fontSize: "0.9rem" }}>{title || "Video"}</b>
          <button
            type="button"
            className="ghost"
            onClick={onClose}
            style={{ width: "auto" }}
          >
            Close ✕
          </button>
        </div>
        <div style={{ position: "relative" }}>
          <video
            ref={videoRef}
            src={url}
            controls
            autoPlay
            style={{
              width: "100%", maxHeight: "80vh",
              background: "#000", borderRadius: 6, display: "block",
            }}
          />
          {showOverlay && (
            <div
              style={{
                position: "absolute", top: 8, left: 8,
                background: "rgba(0,0,0,0.7)", color: "#3ee37a",
                fontFamily: "monospace", fontSize: "1rem",
                padding: "4px 8px", borderRadius: 4,
                pointerEvents: "none", letterSpacing: "0.5px",
                display: "flex", flexDirection: "column", gap: 2,
              }}
            >
              {startMs != null && (
                <span
                  title={startedApprox
                    ? "Reckoned from the clip's start time (the trigger), "
                      + "not the camera's own first-frame stamp — right to "
                      + "a fraction of a second, but do not read a "
                      + "tee-to-green offset off it"
                    : "The camera's own reported first-frame time plus the "
                      + "position in the clip. The tee and green overlays "
                      + "are in the same zone, so at the same real instant "
                      + "they read identically"}
                >
                  {startedApprox ? "≈" : ""}
                  {fmtClock(startMs + curTime * 1000)} CT
                </span>
              )}
              {hasFps && <span>Frame {Math.floor(curTime * fps)}</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
