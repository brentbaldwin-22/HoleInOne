/**
 * Camera management page — register / pair / rotate-token / disable
 * the on-course capture devices. Phase 1 of the always-on hardware
 * integration; the event-trigger + upload-event endpoints the Pis
 * actually talk to land in phase 2.
 */
import { Fragment, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Brand } from "../components/Brand.jsx";
import { ViewMapModal } from "../components/ViewMapModal.jsx";
import { DailyMarksModal } from "../components/DailyMarksModal.jsx";
import TriggerZones from "../components/TriggerZones.jsx";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

function tsRel(iso) {
  if (!iso) return "—";
  // Backend serializes timestamps as naive UTC (no Z suffix). Without
  // a timezone marker, the browser parses them as local time, and
  // the diff against Date.now() comes out off by the user's UTC
  // offset (e.g. -5 hours in Central time = "-17985s ago"). Force
  // UTC interpretation by appending Z when no offset is present.
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const d = new Date(utcIso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

// Seconds since an ISO timestamp, or null when there isn't one. Same
// naive-UTC correction as tsRel -- without it a healthy camera reads
// hours stale by the size of the viewer's UTC offset.
function secsAgo(iso) {
  if (!iso) return null;
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  return Math.round((Date.now() - new Date(utcIso).getTime()) / 1000);
}

// The agent heartbeats every 60s (heartbeat_seconds in the Pi config),
// so one missed beat is noise and two is a signal. Colour the line
// rather than making the operator do the subtraction: the whole point
// of "last seen" is answering "is this thing alive right now".
const HEARTBEAT_LATE_SEC = 150;
const HEARTBEAT_DOWN_SEC = 900;
function heartbeatTone(sec) {
  if (sec == null) return { color: "#b3261e", label: "never called in" };
  if (sec <= HEARTBEAT_LATE_SEC) return { color: "#2f6b45", label: "live" };
  if (sec <= HEARTBEAT_DOWN_SEC) return { color: "#8a6d1f", label: "late" };
  return { color: "#b3261e", label: "down" };
}

// Seconds after which an event that hasn't reached a terminal state is
// treated as stuck. The tee-only fallback fires at 180s, so 300 gives it
// a chance to work before we call anything wrong.
const STUCK_AFTER_SEC = 300;

// What each status means, and — the part that matters when you're
// standing on a course wondering why no clips appeared — what it means
// when an event SITS there. A camera event that never reaches 'processed'
// produces nothing, silently, and the Production page shows no row at all
// because no upload was ever created.
const EVENT_STATES = {
  triggered: {
    label: "Triggered",
    tone: "warn",
    ok: "Pi fired a trigger. Waiting for it to upload a clip.",
    stuck:
      "No clip ever arrived. The Pi triggered and recorded, but the upload " +
      "didn't complete — usually a weak uplink dropping a large file. " +
      "Check the Pi: journalctl -u golfreelz-agent -n 100",
  },
  // NB: the backend sets this status when EITHER half is missing, not
  // just when the green is (cameras.py: `elif has_green:` also lands
  // here). So the copy has to be chosen from which file is actually
  // absent — see stuckMessage() — or it tells you the opposite of what
  // happened, which it did on first ship.
  tee_uploaded: {
    label: "Partial",
    tone: "warn",
    ok: "One clip in. Waiting on the other half.",
    stuck: "Only one of the two clips arrived.",
  },
  paired_uploaded: {
    label: "Both clips in",
    tone: "info",
    ok: "Both clips uploaded. Producing.",
    stuck:
      "Produce never picked this up. The queue may be stalled behind a hung " +
      "job — check Production for a card frozen on one stage.",
  },
  processed: {
    label: "Processed",
    tone: "ok",
    ok:
      "Produce ran. NOTE: 'processed' does not mean a clip was made — a " +
      "capture with no detectable swing also lands here. Check Production.",
  },
  failed: { label: "Failed", tone: "bad", ok: "See the error below." },
};

const TONE_STYLE = {
  ok: { bg: "rgba(34,197,94,0.14)", br: "rgba(34,197,94,0.5)" },
  info: { bg: "rgba(56,132,255,0.14)", br: "rgba(56,132,255,0.5)" },
  warn: { bg: "rgba(234,179,8,0.16)", br: "rgba(234,179,8,0.55)" },
  bad: { bg: "rgba(239,68,68,0.14)", br: "rgba(239,68,68,0.5)" },
};

/** The stuck copy, chosen from WHICH clip is missing rather than from the
 *  status alone. A 'tee_uploaded' event with no tee file is a different —
 *  and worse — problem than one with no green file: the tee-only fallback
 *  filters on `tee_clip_filename.isnot(None)`, so a green-only event is
 *  never swept, never failed, and sits forever. */
// WHAT THE CAMERA SAYS, not what we asked for. The agent reads the
// camera after every change, and this renders that answer: the keys it
// found, the values behind them, and the raw dump folded away for the
// firmware we have not met yet. A refusal is shown as a refusal -- a
// shutter we believe we set but did not is worse than one we never
// touched.
// WHAT THIS CAMERA IS REALLY SENDING. Three numbers that should agree
// and sometimes do not: the size/rate the stream OPENED at, the rate
// frames are actually arriving at, and the rate the config believes —
// which is what clips get stamped with. A camera delivering 30 while
// the config says 50 is what made a 35-second capture play in 21.
//
// Nothing here is a setting. On an RTSP camera the agent never asks for
// a frame rate (it cannot — the sensor belongs to the camera), so the
// rate is changed in the camera's own profile, and this is how you find
// out what it is.
function StreamReadout({ stream, onRead, busy }) {
  const s = stream || {};
  const rows = [
    ["Opened as", s.open_w && s.open_h
      ? `${s.open_w}×${s.open_h}${s.open_fps ? ` @ ${s.open_fps.toFixed(1)}` : ""}`
      : null],
    ["Delivering", s.delivered_fps ? `${s.delivered_fps.toFixed(1)} fps` : null],
    ["Clips stamped", s.config_fps ? `${s.config_fps.toFixed(0)} fps` : null],
  ].filter(([, v]) => v);
  const p = s.profile;
  return (
    <div style={{ width: "100%" }}>
      {/* NO PARAGRAPH. What this panel is for lives in the labels and
          in the tooltips; a card an operator reads every day does not
          need the rationale printed on it every time. */}
      <div className="tiny upper muted" style={{ marginBottom: 3 }}
           title="What the camera is really sending, measured rather than assumed. The rate is set in the camera's own profile; our config only says what we believe it to be.">
        Stream
      </div>
      {rows.length > 0 ? (
        <div className="tiny" style={{ display: "flex", flexWrap: "wrap",
                                       gap: "2px 14px" }}>
          {rows.map(([label, v]) => (
            <span key={label}>
              <span className="muted">{label}:</span> <b>{v}</b>
            </span>
          ))}
        </div>
      ) : (
        <div className="tiny muted">nothing reported yet</div>
      )}
      {/* THE WARNING STAYS. It is a finding, not an explanation: this
          camera's clips really do play fast. The why is in the tooltip
          so the card carries the fault and not the lecture. */}
      {s.mismatch && (
        <div className="tiny" style={{ color: "var(--warn)", marginTop: 3 }}
             title="Clips are stamped at the configured rate, so they play fast until the two are brought together — change the camera's profile, or the agent's config.">
          ⚠ {s.mismatch}
        </div>
      )}
      {p?.ok && p.values && (
        <div className="tiny" style={{ marginTop: 3 }}>
          <span className="muted">Camera says:</span>{" "}
          {/* Units on the numbers: "30" and "2048" side by side say
              nothing on their own. */}
          {[["resolution", ""], ["fps", " fps"], ["codec", ""],
            ["bitrate", " kbps"]]
            .filter(([k]) => p.values[k])
            .map(([k, unit], i) => (
              <Fragment key={k}>
                {i > 0 && <span className="muted"> · </span>}
                <b>{p.values[k]}{unit}</b>
              </Fragment>
            ))}
          {Object.keys(p.values).length === 0 && (
            <span className="muted">
              nothing we have a name for — open the raw list
            </span>
          )}
        </div>
      )}
      {p && p.ok === false && (
        <div className="tiny muted" style={{ marginTop: 3 }}>
          profile read refused: {p.error}
        </div>
      )}
      {/* Not .row — that stretches its children, and a Read button the
          width of the card looks like the primary action here. */}
      <div style={{ display: "flex", gap: 8, marginTop: 5,
                    alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="ghost small"
                style={{ width: "auto" }} disabled={busy}
                title="Ask the camera what its video profile is set to — resolution, frame rate, codec"
                onClick={onRead}>
          Read profile
        </button>
        {s.updated_at && (
          <span className="tiny muted">reported {tsRel(s.updated_at)}</span>
        )}
      </div>
      {p?.raw && Object.keys(p.raw).length > 0 && (
        <details className="tiny" style={{ marginTop: 4 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>
            Everything the profile listed ({Object.keys(p.raw).length})
            {p.source ? ` · ${p.source}` : ""}
          </summary>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr",
                        gap: "0 10px", marginTop: 4,
                        maxHeight: 200, overflowY: "auto" }}>
            {Object.entries(p.raw).map(([k, v]) => (
              <Fragment key={k}>
                <span className="muted">{k}</span>
                <span>{String(v)}</span>
              </Fragment>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function ExposureReadout({ exp }) {
  if (!exp) return null;
  const rows = [
    ["Mode", exp.mode],
    ["Shutter", exp.speed],
    ["Slowest allowed", exp.slow_limit],
    ["WDR", exp.wdr],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <div style={{ width: "100%" }}>
      {exp.ok === false && (
        <div className="tiny" style={{ color: "var(--danger)" }}>
          The camera refused: {exp.error || "no reason given"}
        </div>
      )}
      {rows.length > 0 ? (
        <div className="tiny" style={{ display: "flex", flexWrap: "wrap",
                                       gap: "2px 14px" }}>
          {rows.map(([label, v]) => (
            <span key={label}>
              <span className="muted">{label}:</span> <b>{v}</b>
            </span>
          ))}
        </div>
      ) : (
        <div className="tiny muted">no shutter key we have a name for</div>
      )}
      <div className="tiny muted" style={{ marginTop: 2 }}>
        read {tsRel(exp.updated_at)}
        {exp.sent && (
          <> · last sent {Object.entries(exp.sent)
            .map(([k, v]) => `${k}=${v}`).join(", ")}</>
        )}
      </div>
      {Object.keys(exp.raw || {}).length > 0 && (
        <details className="tiny" style={{ marginTop: 4 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>
            Everything the camera listed ({Object.keys(exp.raw).length})
          </summary>
          <div style={{ display: "grid",
                        gridTemplateColumns: "auto 1fr",
                        gap: "0 10px", marginTop: 4,
                        maxHeight: 200, overflowY: "auto" }}>
            {Object.entries(exp.raw).map(([k, v]) => (
              <Fragment key={k}>
                <span className="muted">{k}</span>
                <span>{String(v)}</span>
              </Fragment>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function stuckMessage(ev, teeInFlight) {
  const meta = EVENT_STATES[ev.status];
  // A clip the server is receiving right now is not a stuck one. Saying
  // "the tee Pi is failing to upload" while it is 82% through, on a link
  // that takes ten minutes a clip, sends the operator to SSH for a
  // problem that is resolving itself.
  if (teeInFlight && !ev.tee_clip_filename) {
    return (
      "The tee clip is still coming in — "
      + (teeInFlight.percent != null ? `${teeInFlight.percent}% ` : "")
      + (teeInFlight.stale_seconds < 30
        ? "and still moving. Nothing to do; it will produce when it lands."
        : `and it has not moved for ${
            teeInFlight.stale_seconds < 120
              ? `${teeInFlight.stale_seconds}s`
              : `${Math.round(teeInFlight.stale_seconds / 60)} min`
          }. The progress is banked, so it resumes from there when the `
          + "link comes back.")
    );
  }
  if (ev.status !== "tee_uploaded") return meta?.stuck || "";
  const haveTee = !!ev.tee_clip_filename;
  const haveGreen = !!ev.green_clip_filename;
  if (!haveTee && haveGreen) {
    return (
      "The TEE clip never arrived — only the green half uploaded. This event " +
      "can never produce: the tracer needs the tee view, and the tee-only " +
      "fallback skips events with no tee file, so nothing will sweep it. " +
      "The tee Pi is triggering and staying online but failing to upload. " +
      "Check it: vcgencmd get_throttled (0x0 = healthy power) and " +
      "journalctl -u golfreelz-agent -n 200"
    );
  }
  if (haveTee && !haveGreen) {
    return (
      "The green clip never arrived. The tee-only fallback should have " +
      "produced from the tee alone after 3 min — if it hasn't, that sweeper " +
      "isn't running."
    );
  }
  return "Neither clip arrived.";
}

function ageSeconds(iso) {
  if (!iso) return null;
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  return Math.round((Date.now() - new Date(utcIso).getTime()) / 1000);
}

/** One clip's arrival state. This is the single most useful thing on the
 *  page: it separates "the Pi never sent it" from "it arrived and produce
 *  did nothing with it". */
function ClipChip({ label, filename, sizeMb, durationSec, missing, url,
                   inFlight }) {
  const arrived = !!filename;
  // A clip the server is part-way through receiving is not "never
  // arrived" -- that reads as a dead Pi when it is a working one on a
  // slow link. Show how far it has got, and whether it is still moving.
  const coming = !arrived && !!inFlight;
  const moving = coming && inFlight.stale_seconds < 30;
  const tone = coming ? (moving ? "ok" : "warn")
    : !arrived ? "bad" : missing ? "warn" : "ok";
  const s = TONE_STYLE[tone];
  const mb = (b) => (b / (1024 * 1024)).toFixed(1);
  const detail = coming
    ? [
        inFlight.percent != null ? `${inFlight.percent}%` : "sending",
        `${mb(inFlight.received_bytes)}`
        + (inFlight.total_bytes ? `/${mb(inFlight.total_bytes)}` : "")
        + " MB",
        moving
          ? "uploading"
          : `stalled ${inFlight.stale_seconds < 120
              ? `${inFlight.stale_seconds}s`
              : `${Math.round(inFlight.stale_seconds / 60)}m`}`,
      ].join(" · ")
    : !arrived
    ? "never arrived"
    : missing
      ? "uploaded, file gone"
      : [
          sizeMb != null ? `${sizeMb} MB` : null,
          durationSec != null ? `${Number(durationSec).toFixed(1)}s` : null,
        ].filter(Boolean).join(" · ") || "arrived";
  const body = (
    <span
      className="small"
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "3px 10px", borderRadius: 999,
        background: s.bg, border: `1px solid ${s.br}`,
      }}
    >
      <b>{label}</b>
      <span className="muted">
        {coming ? "⟳" : arrived ? "✓" : "✗"} {detail}
      </span>
      {coming && inFlight.percent != null && (
        <span style={{
          width: 46, height: 4, borderRadius: 2, marginLeft: 2,
          background: "rgba(0,0,0,0.15)", overflow: "hidden",
          display: "inline-block",
        }}>
          <span style={{
            display: "block",
            width: `${Math.max(2, Math.min(100, inFlight.percent))}%`,
            height: "100%",
            background: moving ? "#1a9d55" : "#d69e2e",
          }} />
        </span>
      )}
    </span>
  );
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
      {body}
    </a>
  ) : body;
}

// ---------------------------------------------------------------------
// Green-camera calibration
// ---------------------------------------------------------------------
// Pixels are not yards, and the scale changes across the frame: a ball
// 30 ft from the pin but further from the camera covers fewer pixels
// than one 30 ft away and near. So there is no "pixels per foot" that is
// right anywhere except at a single distance. Four marked points define
// a homography onto the plane of the green, which is right everywhere.
//
// This screen is the blocking piece for closest-to-the-pin and for the
// distance stamped on a clip. It is NOT what finishes the tracer any
// more -- that is the green→tee map, which goes straight between the two
// pictures and needs no feet at all.

const DEFAULT_MARKS = [
  { label: "Front edge — centre", hint: "nearest point of the putting surface" },
  { label: "Back edge — centre", hint: "furthest point of the putting surface" },
  { label: "Left extreme", hint: "widest point on the left" },
  { label: "Right extreme", hint: "widest point on the right" },
];

function GreenCalibrationModal({ adminPassword, cam, onClose }) {
  const [frameUrl, setFrameUrl] = useState(null);
  const [marks, setMarks] = useState([]);     // {x,y,X,Y,label}
  const [pinIdx, setPinIdx] = useState(null); // index of the pin mark
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [existing, setExisting] = useState(null);
  const [probe, setProbe] = useState(null);
  const imgRef = useRef(null);

  // Grab a still. The live-frame path renews the watch TTL, so opening
  // this screen is enough to make the Pi start pushing frames.
  useEffect(() => {
    let cancelled = false;
    let url = null;
    async function grab() {
      try {
        // Marking the camera watched is what makes the Pi start pushing
        // frames; the first poll usually 404s until one lands, so retry
        // briefly rather than declaring the camera dead.
        await api.startWatchingCamera(adminPassword, cam.id);
        const src = api.cameraLiveFrameUrl(cam.id);
        let blob = null;
        for (let i = 0; i < 12 && !cancelled && !blob; i++) {
          const res = await fetch(src, {
            headers: { "X-Admin-Password": adminPassword },
            cache: "no-store",
          });
          if (res.status === 200) blob = await res.blob();
          else await new Promise((r) => setTimeout(r, 1000));
        }
        if (cancelled) return;
        if (!blob) throw new Error("no frame");
        url = URL.createObjectURL(blob);
        setFrameUrl(url);
      } catch (e) {
        if (!cancelled) setErr(
          "Could not get a frame from this camera. It has to be online and " +
          "delivering video — check it on the Cameras page first.",
        );
      }
    }
    grab();
    api.getGreenCalibration(adminPassword, cam.id)
      .then((r) => !cancelled && setExisting(r?.calibration || null))
      .catch(() => {});
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      api.stopWatchingCamera(adminPassword, cam.id).catch(() => {});
    };
  }, [adminPassword, cam.id]);

  // Click position in NATIVE image pixels, not displayed pixels — the
  // homography is fitted in the camera's own coordinate space, so a
  // browser-scaled click would bake the scale factor into the matrix.
  function addMark(e) {
    const img = imgRef.current;
    if (!img) return;
    const r = img.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * img.naturalWidth;
    const y = ((e.clientY - r.top) / r.height) * img.naturalHeight;
    const preset = DEFAULT_MARKS[marks.length];
    setMarks((m) => [...m, {
      x: Math.round(x), y: Math.round(y), X: "", Y: "",
      label: preset ? preset.label : `Point ${m.length + 1}`,
    }]);
    setResult(null);
  }

  function setField(i, key, v) {
    setMarks((m) => m.map((k, j) => (j === i ? { ...k, [key]: v } : k)));
  }

  const usable = marks.filter(
    (m) => m.X !== "" && m.Y !== "" && Number.isFinite(+m.X) && Number.isFinite(+m.Y),
  );
  const canSave = usable.length >= 4 && usable.length === marks.length;

  async function save() {
    setSaving(true); setErr(null); setResult(null);
    try {
      const pin = pinIdx != null && marks[pinIdx]
        ? { image: [marks[pinIdx].x, marks[pinIdx].y],
            world: [+marks[pinIdx].X, +marks[pinIdx].Y] }
        : null;
      const r = await api.calibrateGreenCamera(adminPassword, cam.id, {
        imagePoints: marks.map((m) => [m.x, m.y]),
        worldPoints: marks.map((m) => [+m.X, +m.Y]),
        pin,
      });
      setResult(r);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  }

  // Verification: click anywhere and see where the saved calibration
  // thinks it is. Pace it out and you know whether to trust it.
  async function probeAt(e) {
    const img = imgRef.current;
    if (!img) return;
    const r = img.getBoundingClientRect();
    const x = Math.round(((e.clientX - r.left) / r.width) * img.naturalWidth);
    const y = Math.round(((e.clientY - r.top) / r.height) * img.naturalHeight);
    try {
      setProbe(await api.measureGreenPoint(adminPassword, cam.id, x, y));
    } catch (e2) {
      setProbe({ error: e2.message });
    }
  }

  const calibrated = !!(existing || result);

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        zIndex: 200, overflow: "auto", padding: 20,
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="card" style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div className="row" style={{ alignItems: "center", gap: 10 }}>
          <b>Calibrate green camera #{cam.id}</b>
          <span className="small muted">
            {cam.course_name} · hole {cam.assigned_hole}
          </span>
          <button className="small ghost" style={{ marginLeft: "auto" }}
                  onClick={onClose}>Close</button>
        </div>

        <p className="small muted">
          Click <b>4 or more</b> points you can also locate on the yardage
          book, then type where each one is in <b>feet</b>. X runs across
          the green, Y from front to back. Origin is yours to pick — the
          front-left of the putting surface is a reasonable one.
          {" "}Mark the <b>pin</b> too and distances read out directly.
        </p>

        {err && <div className="card err-text small">{err}</div>}

        {!frameUrl && <div className="card muted small">Getting a frame…</div>}
        {frameUrl && (
          <div style={{ position: "relative", display: "inline-block", maxWidth: "100%" }}>
            <img
              ref={imgRef} src={frameUrl} alt="green camera still"
              onClick={calibrated && marks.length === 0 ? probeAt : addMark}
              style={{ maxWidth: "100%", cursor: "crosshair", display: "block" }}
            />
            {marks.map((m, i) => (
              <span key={i} style={{
                position: "absolute", left: `${(m.x / (imgRef.current?.naturalWidth || 1)) * 100}%`,
                top: `${(m.y / (imgRef.current?.naturalHeight || 1)) * 100}%`,
                transform: "translate(-50%,-50%)",
                width: 20, height: 20, borderRadius: "50%",
                background: i === pinIdx ? "rgba(239,68,68,0.85)" : "rgba(34,197,94,0.85)",
                color: "#fff", fontSize: 12, fontWeight: 700,
                display: "flex", alignItems: "center", justifyContent: "center",
                border: "2px solid #fff", pointerEvents: "none",
              }}>{i + 1}</span>
            ))}
          </div>
        )}

        {calibrated && marks.length === 0 && (
          <div className="small" style={{ marginTop: 8 }}>
            <b>Already calibrated.</b>{" "}
            {existing?.rms_error_ft != null
              ? `Fit residual ${existing.rms_error_ft} ft.`
              : "Fitted from 4 points (exact fit — no self-check available)."}
            {" "}Click the image to check a spot, or start clicking to re-do it.
            {probe && (
              <div className="card" style={{ marginTop: 6, padding: 8 }}>
                {probe.error ? <span className="err-text">{probe.error}</span> : (
                  <>x {probe.x_ft} ft · y {probe.y_ft} ft
                  {probe.distance_from_pin_display
                    ? ` · ${probe.distance_from_pin_display} from the pin` : ""}</>
                )}
              </div>
            )}
          </div>
        )}

        {marks.length > 0 && (
          <table className="small" style={{ width: "100%", marginTop: 12 }}>
            <thead><tr>
              <th>#</th><th>What you clicked</th><th>px</th>
              <th>X ft</th><th>Y ft</th><th>Pin</th>
            </tr></thead>
            <tbody>
              {marks.map((m, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td>
                    <input value={m.label}
                           onChange={(e) => setField(i, "label", e.target.value)}
                           style={{ width: "100%" }} />
                    {DEFAULT_MARKS[i] && (
                      <div className="tiny muted">{DEFAULT_MARKS[i].hint}</div>
                    )}
                  </td>
                  <td className="muted">{m.x},{m.y}</td>
                  <td><input type="number" value={m.X} style={{ width: 80 }}
                             onChange={(e) => setField(i, "X", e.target.value)} /></td>
                  <td><input type="number" value={m.Y} style={{ width: 80 }}
                             onChange={(e) => setField(i, "Y", e.target.value)} /></td>
                  <td>
                    <input type="radio" name="pin" checked={pinIdx === i}
                           onChange={() => setPinIdx(i)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="row" style={{ gap: 8, marginTop: 12, alignItems: "center" }}>
          <button onClick={save} disabled={!canSave || saving}>
            {saving ? "Saving…" : `Save calibration (${marks.length} points)`}
          </button>
          <button className="ghost small" onClick={() => { setMarks([]); setPinIdx(null); setResult(null); }}>
            Clear points
          </button>
          {marks.length > 0 && marks.length < 4 && (
            <span className="small muted">Need at least 4.</span>
          )}
          {marks.length === 4 && (
            <span className="small muted">
              A 5th point would let this measure its own accuracy.
            </span>
          )}
        </div>

        {result && (
          <div className="card" style={{ marginTop: 10,
               background: "rgba(34,197,94,0.08)" }}>
            <b>Saved.</b>{" "}
            <span className="small">{result.accuracy_note}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function CameraEventsPanel({ adminPassword }) {
  const [events, setEvents] = useState(null);
  const [err, setErr] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  // Partials the server is holding, keyed cam{id}-{session} so a chip
  // can find its own. On a slow link a clip is in flight for minutes,
  // and "never arrived" is the wrong thing to say about it the whole
  // time.
  const [inFlight, setInFlight] = useState({});

  useEffect(() => {
    if (!adminPassword) return undefined;
    let cancelled = false;
    async function load() {
      try {
        const rows = await api.listCameraEvents(adminPassword, 50, 0);
        if (!cancelled) { setEvents(rows || []); setErr(null); }
      } catch (e) {
        if (!cancelled) setErr(e.message || "could not load camera events");
      }
    }
    load();
    const id = setInterval(load, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, [adminPassword]);

  useEffect(() => {
    if (!adminPassword) return undefined;
    let cancelled = false;
    const load = () =>
      api
        .uploadsInFlight(adminPassword)
        .then((r) => {
          if (cancelled) return;
          const by = {};
          for (const p of r?.in_flight || []) {
            by[`${p.camera}-${p.session_id}`] = p;
          }
          setInFlight(by);
        })
        // Progress is a nicety; the events list must not go blank
        // because this one failed.
        .catch(() => {});
    load();
    // Faster than the events poll: a percentage has to be seen climbing
    // to mean anything.
    const id = setInterval(load, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [adminPassword]);

  async function act(fn, ev, confirmMsg) {
    if (confirmMsg && !confirm(confirmMsg)) return;
    setBusyId(ev.id);
    try {
      await fn(adminPassword, ev.id);
      const rows = await api.listCameraEvents(adminPassword, 50, 0);
      setEvents(rows || []);
    } catch (e) {
      setErr(e.message || "action failed");
    } finally {
      setBusyId(null);
    }
  }

  const shown = (events || []).filter((ev) => {
    if (!onlyProblems) return true;
    const age = ageSeconds(ev.triggered_at) ?? 0;
    const terminal = ev.status === "processed" || ev.status === "failed";
    return ev.status === "failed" || (!terminal && age > STUCK_AFTER_SEC);
  });

  return (
    <div style={{ marginTop: 28 }}>
      <div className="row" style={{ alignItems: "center", gap: 12, marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Camera events</h3>
        <span className="small muted">
          Every trigger the Pis sent, and how far it got.
        </span>
        <label className="small" style={{ marginLeft: "auto", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={onlyProblems}
            onChange={(e) => setOnlyProblems(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          Only stuck / failed
        </label>
      </div>

      <p className="small muted" style={{ marginTop: 0 }}>
        An event has to reach <b>Processed</b> before anything appears on{" "}
        <Link to="/admin/production">Production</Link>. If clips aren&apos;t
        showing up there, the status here tells you which stage stopped.
      </p>

      {err && <div className="card err-text small">{err}</div>}
      {events === null && <div className="card muted small">Loading…</div>}
      {events !== null && shown.length === 0 && (
        <div className="card muted center small" style={{ padding: 24 }}>
          {onlyProblems
            ? "No stuck or failed events."
            : "No camera events yet — no Pi has sent a trigger."}
        </div>
      )}

      {shown.map((ev) => {
        const age = ageSeconds(ev.triggered_at);
        const terminal = ev.status === "processed" || ev.status === "failed";
        const isStuck = !terminal && (age ?? 0) > STUCK_AFTER_SEC;
        const meta = EVENT_STATES[ev.status] || {
          label: ev.status || "unknown", tone: "info", ok: "",
        };
        const s = TONE_STYLE[isStuck ? "bad" : meta.tone];
        const busy = busyId === ev.id;
        return (
          <div key={ev.id} className="card" style={{ marginBottom: 10 }}>
            <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <b>#{ev.id}</b>
              <span
                className="small"
                style={{
                  padding: "3px 10px", borderRadius: 999,
                  background: s.bg, border: `1px solid ${s.br}`, fontWeight: 600,
                }}
              >
                {meta.label}{isStuck ? " · STUCK" : ""}
              </span>
              <span className="small muted">
                {ev.course_name || `course ${ev.course_id}`}
                {ev.hole_number != null ? ` · hole ${ev.hole_number}` : ""}
              </span>
              <span className="small muted">· {tsRel(ev.triggered_at)}</span>
              <span className="small muted" style={{ marginLeft: "auto" }}>
                {ev.tee_camera_name || `cam ${ev.tee_camera_id}`}
                {ev.dual_camera
                  ? ` + ${ev.green_camera_name || `cam ${ev.green_camera_id}`}`
                  : " (tee only)"}
              </span>
            </div>

            <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              <ClipChip
                label="TEE" filename={ev.tee_clip_filename} sizeMb={ev.tee_size_mb}
                durationSec={ev.tee_duration_sec} missing={ev.tee_missing}
                url={ev.tee_url}
                inFlight={inFlight[`cam${ev.tee_camera_id}-${ev.session_id}`]}
              />
              {ev.dual_camera && (
                <ClipChip
                  label="GREEN" filename={ev.green_clip_filename}
                  sizeMb={ev.green_size_mb} durationSec={ev.green_duration_sec}
                  missing={ev.green_missing} url={ev.green_url}
                  inFlight={
                    inFlight[`cam${ev.green_camera_id}-${ev.session_id}`]
                  }
                />
              )}
            </div>

            {(isStuck ? stuckMessage(ev, inFlight[`cam${ev.tee_camera_id}-${ev.session_id}`]) : meta.ok) && (
              <div
                className="small"
                style={{
                  marginTop: 8, padding: "8px 10px", borderRadius: 8,
                  background: isStuck ? "rgba(239,68,68,0.08)" : "rgba(127,127,127,0.08)",
                }}
              >
                {isStuck ? stuckMessage(ev, inFlight[`cam${ev.tee_camera_id}-${ev.session_id}`]) : meta.ok}
              </div>
            )}

            {ev.last_error && (
              <div className="err-text small" style={{ marginTop: 8 }}>
                {ev.last_error}
              </div>
            )}

            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <button
                className="small"
                disabled={busy || !ev.tee_clip_filename}
                onClick={() => act(api.reprocessCameraEvent, ev)}
                title={ev.tee_clip_filename
                  ? "Re-run production from the raw clips"
                  : "No tee clip was ever uploaded"}
              >
                {busy ? "Working…" : "Re-process"}
              </button>
              <button
                className="small danger"
                disabled={busy}
                onClick={() => act(
                  api.deleteCameraEvent, ev,
                  `Delete camera event #${ev.id}? This removes the raw clips.`,
                )}
              >
                Delete
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * THE CAMERA'S VIEW, FOR FREE.
 *
 * The picture box used to be empty until somebody pressed Watch, which
 * put a Pi into 10 fps JPEG streaming over a cellular modem to answer a
 * question that is almost always "is it still pointed at the tee" — a
 * question one frame answers. So the agent leaves a snapshot with the
 * backend every ten minutes whether anyone is looking or not, and this
 * is what the card shows: the view, immediately, on every camera, with
 * no traffic on the device at all.
 *
 * Live is still a click away, from the button on the picture itself.
 *
 * THE TIME IS PRINTED ON IT because a still with no timestamp is a lie
 * waiting to happen: the picture of a sunlit tee is indistinguishable
 * from the picture of a sunlit tee taken before the camera died. The
 * clock time and the age both show, and the age goes amber once the
 * snapshot is older than the camera should have let it get.
 */
const STILL_STALE_SEC = 30 * 60;   // 3x the backend's snapshot interval

function stillClock(iso) {
  if (!iso) return null;
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const d = new Date(utcIso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function CameraStill({ cam, adminPassword, onWatch, disabled }) {
  const [src, setSrc] = useState(null);
  const [takenAt, setTakenAt] = useState(null);
  const [missing, setMissing] = useState(false);
  const urlRef = useRef(null);

  // Refetched when the snapshot CHANGES, not on a clock: still_at comes
  // down with the camera list (which refreshes anyway), so a page left
  // open costs one image per camera per snapshot rather than one a
  // minute per camera. cam.still_at in the dependency list is the whole
  // mechanism.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(api.cameraStillUrl(cam.id), {
          headers: { "X-Admin-Password": adminPassword },
          cache: "no-store",
        });
        if (cancelled) return;
        if (res.status !== 200) { setMissing(true); return; }
        // The capture time travels with the bytes so the two cannot
        // disagree. Cross-origin dev servers can hide the header even
        // when the backend sends it, so the column is the fallback.
        const hdr = res.headers.get("X-Captured-At");
        const blob = await res.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setTakenAt(hdr || cam.still_at || null);
        setSrc(url);
        setMissing(false);
      } catch {
        if (!cancelled) setMissing(true);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam.id, cam.still_at, adminPassword]);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  const age = secsAgo(takenAt);
  const stale = age != null && age > STILL_STALE_SEC;
  const clock = stillClock(takenAt);

  return (
    <div style={{ position: "relative", background: "#000", minHeight: 160 }}>
      {src ? (
        <img src={src} alt="" style={{ display: "block", width: "100%",
                                       height: "auto" }} />
      ) : (
        <div style={{ minHeight: 160, display: "flex", alignItems: "center",
                      justifyContent: "center", color: "#bbb", fontSize: 13,
                      textAlign: "center", padding: 12 }}>
          {missing
            ? "No picture yet — the camera sends one as soon as it calls in."
            : "Loading the camera's last picture…"}
        </div>
      )}
      {/* The bar sits ON the picture rather than under it: the time
          belongs to the image, and Watch live wants to read as the play
          control of the thing above it. */}
      <div
        className="inline"
        style={{
          position: "absolute", left: 0, right: 0, bottom: 0,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 8, padding: "6px 8px", width: "100%", boxSizing: "border-box",
          background: "linear-gradient(to top, rgba(0,0,0,.72), rgba(0,0,0,0))",
        }}
      >
        <span
          className="tiny"
          style={{
            fontFamily: "monospace", minWidth: 0,
            color: stale ? "#f0c05a" : "#e8e8e8",
            textShadow: "0 1px 2px rgba(0,0,0,.9)",
          }}
          title={takenAt ? `Snapshot taken ${takenAt}` : "No snapshot stored"}
        >
          {clock ? <>{clock} · {tsRel(takenAt)}</> : "no snapshot"}
          {stale && " · not refreshing"}
        </span>
        <button
          type="button" className="small"
          style={{ width: "auto", flexShrink: 0 }}
          onClick={onWatch}
          disabled={disabled}
          title="Open the live picture from this camera (~10 fps). The camera only streams while this is open."
        >
          ▶ Watch live
        </button>
      </div>
    </div>
  );
}

/**
 * THE LENS, UNDER THE PICTURE IT MOVES.
 *
 * These controls used to live in the right-hand column, a screen away
 * from the only feedback they have. The lens cannot be asked where it
 * is — the camera declares no absolute position — so driving it is a
 * matter of watching the view change, and a control you operate by
 * watching something else belongs next to that something else.
 *
 * THE SLIDER IS A COUNT, NOT A READING. The backend adds up every step
 * it has sent and the ends of the travel are marked by hand, once, by
 * whoever drove the lens into them. That is enough to put a position on
 * a scale and to label it in the magnification the lens is built for —
 * and it drifts, which is why marking an end is also the repair.
 *
 * Shown only while the live view is open, because every one of these is
 * a nudge judged by eye and nudging a picture you cannot see is how a
 * camera ends up pointed at a tree.
 */
// THE SHUTTER, AS A LADDER. Mirrors _SHUTTER_LADDER in routers/admin.py
// -- keep the two together. Every rung but the first caps auto-exposure
// rather than pinning it, so the camera still exposes for the light and
// is only forbidden the long smear. Slowest first, which is also
// dimmest-but-brightest to sharpest-but-darkest.
const SHUTTER_LADDER = ["auto", "1/125", "1/250", "1/500",
                        "1/1000", "1/2000", "1/4000"];
const SHUTTER_WHY = {
  "auto": "No cap — the camera decides, and on a dull day it will pick a "
    + "long exposure that smears the ball",
  "1/125": "Dusk, last tee times",
  "1/250": "Low light",
  "1/500": "Flat light — the setting that matters most for the tracer",
  "1/1000": "Full sun",
  "1/2000": "Hard sun on white sky",
  "1/4000": "As short as this sensor goes; dark unless it is very bright",
};

// Which rung the camera is actually on, as an index, or null when it is
// not saying anything we recognise. The CAP is what these set, so that
// is what is matched -- the live shutter floats below it.
function shutterIndex(exp) {
  const v = exp?.slow_limit || exp?.speed;
  if (!v) return null;
  const i = SHUTTER_LADDER.indexOf(String(v).trim());
  if (i >= 0) return i;
  return /auto/i.test(exp?.mode || "") ? 0 : null;
}

function LensBar({ cam, onLens, onZoom, onMarkEnd, onShutter, note, busy }) {
  const z = cam.zoom || {};
  const [local, setLocal] = useState(z.fraction ?? 0);
  const [marking, setMarking] = useState(false);
  const dragging = useRef(false);
  // The shutter rung, held locally while a thumb is on it so the label
  // moves with the drag rather than waiting on a camera two polls away.
  const camShutter = shutterIndex(cam.exposure);
  const [shut, setShut] = useState(camShutter ?? 3);
  useEffect(() => {
    if (camShutter != null) setShut(camShutter);
  }, [camShutter]);

  // Follow the server's count when it changes under us (a nudge, a
  // mark, another operator) — but never while a thumb is on the slider.
  useEffect(() => {
    if (!dragging.current && z.fraction != null) setLocal(z.fraction);
  }, [z.fraction]);

  // If the camera ever reports its own position, that beats the count.
  // It is not expected to on this model — which is the whole reason the
  // count exists — but the agent asks after every move, so the day a
  // firmware grows the endpoint this starts showing the truth with
  // nobody having to change anything.
  const said = z.reported?.values?.zoom;
  const label = said != null
    ? `camera says ${said}`
    : !z.calibrated
      ? "no scale yet"
      : z.x != null
        ? `about ${z.x}x`
        : `${Math.round((z.fraction ?? 0) * 100)}% of travel`;

  function commit() {
    dragging.current = false;
    if (!z.calibrated) return;
    if (Math.abs(local - (z.fraction ?? 0)) < 0.001) return;
    onZoom(local);
  }

  const stepBtn = (op, amt, text, why) => (
    <button
      key={`${op}${amt}`} type="button" className="secondary small"
      style={{ width: "auto", minWidth: 40, fontFamily: "monospace" }}
      disabled={busy} title={why}
      onClick={() => onLens(op, amt)}
    >{text}</button>
  );

  return (
    <div style={{ padding: "8px 2px 2px", display: "flex",
                  flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center",
                    flexWrap: "wrap" }}>
        <span className="tiny" style={{ width: 40, textAlign: "right",
                                        opacity: 0.8, color: "#ddd" }}>
          Zoom
        </span>
        {stepBtn("zoom", -100, "−··", "wider, coarse")}
        {stepBtn("zoom", -10, "−·", "wider, fine")}
        <input
          type="range" min={0} max={1} step={0.005}
          value={local}
          disabled={busy || !z.calibrated}
          onPointerDown={() => { dragging.current = true; }}
          onChange={(e) => setLocal(parseFloat(e.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
          onBlur={commit}
          title={z.calibrated
            ? "Drag to a position along the lens's travel. The move is "
              + "worked out from the count, not read from the camera."
            : "Mark the two ends first — without them there is no scale "
              + "to slide along."}
          style={{ flex: 1, minWidth: 90, accentColor: "#38bdf8" }}
        />
        {stepBtn("zoom", 10, "+·", "tighter, fine")}
        {stepBtn("zoom", 100, "+··", "tighter, coarse")}
        <span className="tiny" style={{ minWidth: 62, color: "#e8e8e8",
                                        fontFamily: "monospace" }}>
          {label}
        </span>
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "center",
                    flexWrap: "wrap" }}>
        <span className="tiny" style={{ width: 40, textAlign: "right",
                                        opacity: 0.8, color: "#ddd" }}>
          Focus
        </span>
        {stepBtn("focus", -100, "−··", "nearer, coarse")}
        {stepBtn("focus", -10, "−·", "nearer, fine")}
        {stepBtn("focus", 10, "+·", "further, fine")}
        {stepBtn("focus", 100, "+··", "further, coarse")}
        <button type="button" className="small" style={{ width: "auto" }}
          disabled={busy}
          title="One-shot autofocus. Set the zoom FIRST, then press this — the camera focuses once and holds."
          onClick={() => onLens("simple_focus", 0)}>
          Auto
        </button>
        <button type="button" className="ghost small" style={{ width: "auto" }}
          disabled={busy} title="Return focus to its default position"
          onClick={() => onLens("reset_focus", 0)}>
          Reset
        </button>
        {cam.focus?.score != null && (
          <span className="tiny" style={{ marginLeft: "auto",
                                          fontFamily: "monospace" }}>
            <span style={{ color: "#e8e8e8" }}>{cam.focus.score}</span>
            {cam.focus.best != null && (
              <span style={{ color: "#999" }}> / best {cam.focus.best}</span>
            )}
            {cam.focus.focus_seconds ? (
              <span style={{ color: "#8fd3a6" }}> ●</span>
            ) : null}
          </span>
        )}
      </div>

      {/* THE SHUTTER, with the lens, because it is the third thing you
          set standing at a camera and the picture above is how you
          judge it: too short and the frame goes dark, too long and the
          ball smears. The old three weather buttons were three rungs of
          this ladder with no way to ask for the step between. */}
      <div style={{ display: "flex", gap: 6, alignItems: "center",
                    flexWrap: "wrap" }}>
        <span className="tiny" style={{ width: 40, textAlign: "right",
                                        opacity: 0.8, color: "#ddd" }}>
          Shutter
        </span>
        <input
          type="range" min={0} max={SHUTTER_LADDER.length - 1} step={1}
          value={shut}
          disabled={busy}
          list={`shutter-${cam.id}`}
          onChange={(e) => setShut(parseInt(e.target.value, 10))}
          onPointerUp={() => onShutter(SHUTTER_LADDER[shut])}
          onKeyUp={() => onShutter(SHUTTER_LADDER[shut])}
          title={SHUTTER_WHY[SHUTTER_LADDER[shut]]}
          style={{ flex: 1, minWidth: 120, accentColor: "#38bdf8" }}
        />
        <datalist id={`shutter-${cam.id}`}>
          {SHUTTER_LADDER.map((_, i) => <option key={i} value={i} />)}
        </datalist>
        <span className="tiny" style={{ minWidth: 62, color: "#e8e8e8",
                                        fontFamily: "monospace" }}>
          {SHUTTER_LADDER[shut] === "auto" ? "auto" : `≤ ${SHUTTER_LADDER[shut]}`}
        </span>
        {/* WHAT THE CAMERA SAYS, not what was sent -- which firmware
            exposes which key varies, and a set that landed on the wrong
            one has to be visible rather than assumed. */}
        <span className="tiny" style={{ color: camShutter == null
                                          ? "#999" : "#8fd3a6" }}>
          {cam.exposure
            ? (camShutter == null
                ? "camera: not saying"
                : `camera: ${cam.exposure.mode || "?"}`
                  + (cam.exposure.slow_limit ? ` ≤ ${cam.exposure.slow_limit}` : ""))
            : ""}
        </span>
        <button type="button" className="ghost small"
          style={{ width: "auto", padding: "0 6px" }} disabled={busy}
          title="Ask the camera what it currently has, and change nothing"
          onClick={() => onShutter("read")}>
          read
        </button>
      </div>

      {/* ONE LINE. Everything these controls need explaining lives in
          their tooltips now; the card is used standing at a camera, and
          a paragraph read for the fiftieth time is in the way. */}
      <div className="tiny" style={{ color: "#999" }}>
        {z.calibrated ? (
          <span title={`Counted from the ${z.travel} steps of travel `
            + `between the marked ends, not read from the camera, so it `
            + `drifts — re-mark an end to put it right.`}>
            Zoom is counted, not read — re-mark an end if it drifts.
          </span>
        ) : (
          <b title="Drive to the widest the lens goes and press Wide; drive to the tightest and press Tele. The stops are the only position this lens can be sure of.">
            The zoom slider needs its two ends marked.
          </b>
        )}
        {" "}
        <button type="button" className="ghost small"
          style={{ width: "auto", padding: "0 4px" }}
          onClick={() => setMarking((m) => !m)}>
          {marking ? "hide" : "mark ends"}
        </button>
      </div>

      {(marking || !z.calibrated) && (
        <div style={{ display: "flex", gap: 6, alignItems: "center",
                      flexWrap: "wrap" }}>
          <button type="button" className="secondary small"
            style={{ width: "auto" }} disabled={busy}
            title="The lens is against its wide stop right now. Starts a fresh scale."
            onClick={() => onMarkEnd("wide")}>
            Wide end is here
          </button>
          <button type="button" className="secondary small"
            style={{ width: "auto" }} disabled={busy || !z.wide_at}
            title={z.wide_at
              ? "The lens is against its tele stop right now. Closes the scale."
              : "Mark the wide end first — the travel is measured from it."}
            onClick={() => onMarkEnd("tele")}>
            Tele end is here
          </button>
          {z.calibrated && (
            <span className="tiny" style={{ color: "#999" }}>
              marked {tsRel(z.tele_at)}
            </span>
          )}
        </div>
      )}

      {note && (
        <span className="tiny" style={{ color: "#8fd3a6" }}>{note}</span>
      )}
    </div>
  );
}

export default function AdminCameras() {
  const adminPassword =
    localStorage.getItem(ADMIN_PW_STORAGE) ||
    localStorage.getItem(LEGACY_ADMIN_PW_STORAGE) ||
    "";

  const [cameras, setCameras] = useState(null);
  const [courses, setCourses] = useState([]);
  const [error, setError] = useState(null);
  const [lensNote, setLensNote] = useState({});
  const [busy, setBusy] = useState({}); // {camera_id: true}
  const [revealedToken, setRevealedToken] = useState({}); // {camera_id: true}
  const [cal, setCal] = useState(null);  // green→tee calibrator
  const [calibratingCam, setCalibratingCam] = useState(null);
  // The pin and the tee box: set daily, from the camera, by whoever is
  // standing there — unlike the calibration above, which is done once.
  const [dailyCam, setDailyCam] = useState(null);
  const [movingCam, setMovingCam] = useState(null); // camera_id whose move form is open
  const [moveDraft, setMoveDraft] = useState({
    courseId: "", hole: "", role: "", name: "", ballSide: "",
    kind: "pi", streamHost: "", streamPort: "554", streamPath: "",
    streamSubstreamPath: "", streamUsername: "", streamModel: "",
  });

  // Live-watch state: one camera at a time. We poll /live-frame via
  // fetch (rather than letting <img> do it) because the admin endpoint
  // requires the X-Admin-Password header, which <img src> can't send.
  // Each successful 200 turns into an object URL that we hand to <img>;
  // 204 (Pi hasn't uploaded a frame yet) just keeps the placeholder up.
  const [watchingCamId, setWatchingCamId] = useState(null);
  const [liveFrameSrc, setLiveFrameSrc] = useState(null);
  // The camera's NATIVE frame size, read off the live JPEG the Pi
  // pushes (it encodes the full frame, no resize) — which is the only
  // honest source for it, and the unit the trigger zones are stored in.
  const [liveNatural, setLiveNatural] = useState(null);
  const [zoningCamId, setZoningCamId] = useState(null);
  // The element the picture is painted in. The zone editor portals its
  // drawing surface into it, so the surface covers the PICTURE and not
  // the controls underneath — only one camera is ever watched, so one
  // element is enough.
  const [pictureEl, setPictureEl] = useState(null);
  const watchingCamIdRef = useRef(null);
  // AS BIG AS THE SCREEN ALLOWS. Judging focus, or where a trigger zone
  // actually falls, on a picture a third of a column wide is guesswork
  // — so the panel can take the whole viewport, and asks the browser
  // for true fullscreen on top of that. Two mechanisms because they
  // fail differently: the fixed overlay always works and stops at the
  // browser's chrome, native fullscreen gets the last inch of screen
  // and can be refused outright (an iframe without the permission, a
  // call the browser does not accept as user-initiated). Whichever
  // lands, the picture grows.
  const [expanded, setExpanded] = useState(false);
  const panelRef = useRef(null);

  // Leaving fullscreen by any route the button does not own — Escape,
  // the browser's own control, a tab switch — has to put the panel back
  // too, or the page is left in a state nothing on screen explains.
  useEffect(() => {
    function onFsChange() {
      if (!document.fullscreenElement) setExpanded(false);
    }
    function onKey(e) {
      // Escape collapses it whatever happened underneath: the browser
      // may have taken fullscreen, may have refused it, or may not
      // treat Escape as its own exit at all. Doing both is harmless --
      // the second one is a no-op -- and leaving the overlay up because
      // we assumed the browser would handle it is not.
      if (e.key !== "Escape") return;
      setExpanded(false);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    }
    document.addEventListener("fullscreenchange", onFsChange);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onFsChange);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  function toggleExpanded() {
    if (expanded) {
      setExpanded(false);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
      return;
    }
    setExpanded(true);
    // Best effort: a refusal leaves the overlay, which is already most
    // of the win, so there is nothing to tell the operator about.
    panelRef.current?.requestFullscreen?.().catch(() => {});
  }

  // Closing the live view while expanded must not leave the page in a
  // fullscreen panel of nothing.
  useEffect(() => {
    if (watchingCamId == null && expanded) {
      setExpanded(false);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchingCamId]);

  // New-camera form state
  const [newCourseId, setNewCourseId] = useState("");
  const [newHole, setNewHole] = useState("");
  const [newRole, setNewRole] = useState("tee");
  const [newName, setNewName] = useState("");
  // A Pi calls in and is discovered; an IP camera has to be written
  // down. Wisenet's defaults are prefilled because that is what is
  // going up on the poles.
  const [newKind, setNewKind] = useState("pi");
  const [newHost, setNewHost] = useState("");
  const [newPort, setNewPort] = useState("554");
  const [newPath, setNewPath] = useState("/profile1/media.smp");
  const [newSubPath, setNewSubPath] = useState("/profile2/media.smp");
  const [newUser, setNewUser] = useState("admin");
  const [newModel, setNewModel] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const list = await api.listCameras(adminPassword);
      setCameras(list);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    if (!adminPassword) return;
    api.listCourses(adminPassword).then(setCourses).catch((e) => setError(e.message));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminPassword]);

  // Keep the list fresh. It used to load once, which was fine when the
  // only live things on it were "last seen" and battery, and wrong the
  // moment a focus score arrived: someone at the mount turning a ring
  // was reading a number frozen at page load.
  //
  // Two rates. Idle, this is a dashboard and 15s is plenty. While any
  // camera is in focus mode it is an instrument being watched by
  // someone with a screwdriver, so it goes to 2s -- which is roughly
  // how fast the agent reports while armed, and no faster.
  const anyFocusing = (cameras || []).some((c) => c?.focus?.focus_seconds);
  useEffect(() => {
    if (!adminPassword) return undefined;
    const id = setInterval(load, anyFocusing ? 2000 : 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminPassword, anyFocusing]);

  // Poll the live frame ~10 fps while watching. Uses fetch (not <img>
  // src) so we can send the X-Admin-Password header and so 204 (no
  // frame yet) doesn't trigger a broken-image render.
  useEffect(() => {
    if (watchingCamId == null) return;
    let cancelled = false;
    let timer = null;
    let prevUrl = null;
    const url = api.cameraLiveFrameUrl(watchingCamId);

    async function pollOnce() {
      if (cancelled) return;
      try {
        const res = await fetch(url, {
          headers: { "X-Admin-Password": adminPassword },
          cache: "no-store",
        });
        if (cancelled) return;
        if (res.status === 200) {
          const blob = await res.blob();
          if (cancelled) {
            return;
          }
          const objectUrl = URL.createObjectURL(blob);
          setLiveFrameSrc((old) => {
            if (old) URL.revokeObjectURL(old);
            return objectUrl;
          });
          prevUrl = objectUrl;
        }
        // 204 (no frame yet) and other non-200s: keep polling, keep
        // showing whatever was last visible (placeholder or prior frame).
      } catch {
        // network error — keep polling
      }
      if (!cancelled) timer = setTimeout(pollOnce, 100);
    }
    pollOnce();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (prevUrl) URL.revokeObjectURL(prevUrl);
      setLiveFrameSrc(null);
    };
  }, [watchingCamId, adminPassword]);

  // Best-effort: tell the backend to stop watching when the page is
  // unmounted (otherwise it'd hold the 10s TTL until it naturally
  // expires, which is fine but wastes Pi cycles).
  useEffect(() => {
    watchingCamIdRef.current = watchingCamId;
  }, [watchingCamId]);
  useEffect(() => {
    return () => {
      const id = watchingCamIdRef.current;
      if (id != null) api.stopWatchingCamera(adminPassword, id).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startWatch(cam) {
    if (watchingCamId === cam.id) return;
    // If we were watching another camera, ask the backend to release
    // it before claiming a new one (fire-and-forget — don't block).
    if (watchingCamId != null) {
      api.stopWatchingCamera(adminPassword, watchingCamId).catch(() => {});
    }
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.startWatchingCamera(adminPassword, cam.id);
      setWatchingCamId(cam.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function stopWatch() {
    const id = watchingCamId;
    setWatchingCamId(null);
    if (id != null) {
      try {
        await api.stopWatchingCamera(adminPassword, id);
      } catch (e) {
        setError(e.message);
      }
    }
  }

  async function createCamera(e) {
    e.preventDefault();
    setError(null);
    if (!newCourseId || !newHole) {
      setError("Course and hole are required.");
      return;
    }
    setCreating(true);
    try {
      if (newKind === "ip" && !newHost.trim()) {
        setError("An IP camera needs a stream host (its IP address).");
        setCreating(false);
        return;
      }
      await api.createCamera(adminPassword, {
        courseId: parseInt(newCourseId, 10),
        assignedHole: parseInt(newHole, 10),
        assignedRole: newRole,
        name: newName.trim(),
        kind: newKind,
        streamHost: newHost.trim(),
        streamPort: parseInt(newPort, 10) || 554,
        streamPath: newPath.trim(),
        streamSubstreamPath: newSubPath.trim(),
        streamUsername: newUser.trim(),
        streamModel: newModel.trim(),
      });
      setNewHole("");
      setNewName("");
      setNewHost("");
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  }

  // A TOGGLE, because ten minutes is a long time to have pressed the
  // wrong button. Focus mode makes the agent report its sharpness every
  // few seconds instead of every minute; it expires on its own, but an
  // operator who armed it by accident had no way to say so and the
  // /focus-mode/stop endpoint sat here unused.
  async function focusMode(cam) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    setError(null);
    try {
      if (cam.focus?.focus_seconds) {
        await api.stopFocusMode(adminPassword, cam.id);
      } else {
        await api.focusMode(adminPassword, cam.id, 600);
      }
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function captureNow(cam) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    setError(null);
    try {
      const r = await api.captureCamera(adminPassword, cam.id, 30);
      window.alert(
        `Capture queued for camera #${cam.id}.\n\n` +
        `It records 30s the next time it polls (a few seconds), its ` +
        `paired green records alongside it, and the clip goes through ` +
        `the produce queue.\n\nWatch for it on Production.` +
        (r?.paired_green_camera_id
          ? ""
          : "\n\nNOTE: this camera has no paired green, so the clip " +
            "will be tee-only."),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function pairWith(cam, partnerId) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.pairCameras(adminPassword, cam.id, partnerId);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  // THE CALIBRATOR, SOURCED FROM THE PAIR'S OWN FOOTAGE. The two frames
  // are only backdrops to click ground features on, so the server picks
  // the pair's most recent dual-camera capture -- most likely to still
  // look like the course does today -- and the fit is filed against the
  // cameras, which is what it describes.
  async function openCalibrator(cam) {
    setCal({ loading: true, camId: cam.id });
    try {
      const src = await api.cameraCalibrationSource(adminPassword, cam.id);
      const teeF = 0;
      const greenF = 0;
      const [t, g] = await Promise.all([
        api.getLongUploadFrame(adminPassword, src.upload_id, teeF, "tee"),
        api.getLongUploadFrame(adminPassword, src.upload_id, greenF, "green"),
      ]);
      setCal({
        uploadId: src.upload_id,
        tee: { ...t, frame: teeF },
        green: { ...g, frame: greenF },
        existing: src.view_map || null,
        mismatch: src.mismatch || null,
        scope: (src.course_name
          ? `${src.course_name} · hole ${src.hole}`
          : `hole ${src.hole}`)
          + (src.key_reason ? ` · filed by ${src.key_reason}` : "")
          + (src.captured_at
            ? ` · frames from ${src.captured_at.slice(0, 16).replace("T", " ")}`
            : ""),
      });
    } catch (e) {
      setCal({ error: e?.message || String(e), camId: cam.id });
    }
  }

  async function unpair(cam) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.unpairCamera(adminPassword, cam.id);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  // ZOOM AND FOCUS ARE NUDGES, NOT POSITIONS. The camera reports
  // Absolute.Zoom and Query.Zoom as false, so there is no value to show
  // and no slider to build -- the operator steers by the live view,
  // which is why these controls sit directly under it.
  async function lens(cam, op, amount) {
    setError(null);
    try {
      const out = await api.cameraLens(adminPassword, cam.id, op, amount);
      setLensNote((m) => ({ ...m, [cam.id]: "sent — applies on the next poll" }));
      setTimeout(
        () => setLensNote((m) => ({ ...m, [cam.id]: null })), 4000,
      );
      // A zoom step moves the counted position, so the slider has to be
      // told. (The lens itself still cannot be asked — see lens_zoom.py.)
      if (out?.op === "zoom") load();
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  // THE SLIDER. Not "go to 0.4" -- the lens has no absolute mode -- but
  // "from where you are counted to be, move this far", planned by the
  // backend into the three step sizes the lens accepts.
  async function zoomTo(cam, fraction) {
    setError(null);
    try {
      const out = await api.cameraZoom(adminPassword, cam.id, fraction);
      setLensNote((m) => ({ ...m, [cam.id]: out.note || "sent" }));
      setTimeout(
        () => setLensNote((m) => ({ ...m, [cam.id]: null })), 5000,
      );
      load();
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  // "The lens is against that stop right now." The origin for the count
  // above, and the repair when it has drifted.
  async function markZoomEnd(cam, end) {
    setError(null);
    try {
      const out = await api.cameraZoomEnd(adminPassword, cam.id, end);
      setLensNote((m) => ({ ...m, [cam.id]: out.note || "marked" }));
      setTimeout(
        () => setLensNote((m) => ({ ...m, [cam.id]: null })), 6000,
      );
      load();
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  // EXPOSURE IS NOT A NUDGE. Unlike zoom and focus, the camera will say
  // what it currently has -- so every press here is followed by a read,
  // and the card shows the camera's answer rather than the preset that
  // was clicked. Which arrives on the agent's next heartbeat, so the
  // list is reloaded a beat later.
  async function exposure(cam, preset) {
    setError(null);
    try {
      const out = await api.cameraExposure(adminPassword, cam.id, preset);
      setLensNote((m) => ({ ...m, [cam.id]: out.note || "sent" }));
      // The agent nudges its heartbeat as soon as it has applied this,
      // so a few seconds is usually enough to have the answer.
      setTimeout(() => { load(); }, 6000);
      setTimeout(
        () => setLensNote((m) => ({ ...m, [cam.id]: null })), 8000,
      );
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  async function readStreamProfile(cam) {
    setError(null);
    try {
      const out = await api.readStreamProfile(adminPassword, cam.id);
      setLensNote((m) => ({ ...m, [cam.id]: out.note || "asked" }));
      // The agent pokes its heartbeat as soon as it has an answer.
      setTimeout(() => { load(); }, 6000);
      setTimeout(
        () => setLensNote((m) => ({ ...m, [cam.id]: null })), 8000,
      );
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  async function rotateToken(cam) {
    if (!window.confirm(
      "Rotate this camera's auth token? The Pi will stop authenticating with the old token immediately — you'll need to re-provision the SD card.",
    )) return;
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.rotateCameraToken(adminPassword, cam.id);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function toggleEnabled(cam) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.updateCamera(adminPassword, cam.id, { enabled: !cam.enabled });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function toggleTriggering(cam) {
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.updateCamera(adminPassword, cam.id, {
        triggeringEnabled: cam.triggering_enabled === false,
      });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  function openMove(cam) {
    setMoveDraft({
      courseId: String(cam.course_id),
      hole: String(cam.assigned_hole),
      role: cam.assigned_role,
      name: cam.name || "",
      ballSide: cam.ball_side || "",
      kind: cam.kind || "pi",
      streamHost: cam.stream_host || "",
      streamPort: String(cam.stream_port || 554),
      streamPath: cam.stream_path || "",
      streamSubstreamPath: cam.stream_substream_path || "",
      streamUsername: cam.stream_username || "",
      streamModel: cam.stream_model || "",
    });
    setMovingCam(cam.id);
  }

  function closeMove() {
    setMovingCam(null);
  }

  async function submitMove(cam) {
    const hole = parseInt(moveDraft.hole, 10);
    const courseId = parseInt(moveDraft.courseId, 10);
    if (!Number.isFinite(courseId) || !Number.isFinite(hole)) {
      setError("Course and hole are required.");
      return;
    }
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      const updated = await api.updateCamera(adminPassword, cam.id, {
        courseId,
        assignedHole: hole,
        assignedRole: moveDraft.role,
        name: moveDraft.name,
        ballSide: moveDraft.ballSide,
        kind: moveDraft.kind,
        // Sent only for an IP camera. Blanking a Pi's stream fields on
        // every rename would be a silent write to columns the operator
        // never opened.
        ...(moveDraft.kind === "ip"
          ? {
              streamHost: moveDraft.streamHost.trim(),
              streamPort: parseInt(moveDraft.streamPort, 10) || 554,
              streamPath: moveDraft.streamPath.trim(),
              streamSubstreamPath: moveDraft.streamSubstreamPath.trim(),
              streamUsername: moveDraft.streamUsername.trim(),
              streamModel: moveDraft.streamModel.trim(),
            }
          : {}),
      });
      if (updated && updated.auto_unpaired) {
        window.alert(
          "Move applied. This camera was auto-unpaired because the new placement no longer matched its previous partner's course / hole / role.",
        );
      }
      closeMove();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function deleteCamera(cam) {
    if (!window.confirm(
      `Delete camera #${cam.id} (${cam.name || `hole ${cam.assigned_hole} ${cam.assigned_role}`})? This can't be undone.`,
    )) return;
    setBusy((b) => ({ ...b, [cam.id]: true }));
    try {
      await api.deleteCamera(adminPassword, cam.id);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [cam.id]: false }));
    }
  }

  async function copyToken(token) {
    try {
      await navigator.clipboard.writeText(token);
    } catch (e) {
      window.prompt("Copy this token:", token);
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

  // Pair candidates per camera: same course + same hole + opposite
  // role + currently unpaired (or paired with this camera).
  function pairCandidates(cam) {
    if (!cameras) return [];
    const oppositeRole = cam.assigned_role === "tee" ? "green" : "tee";
    return cameras.filter((c) =>
      c.id !== cam.id
      && c.course_id === cam.course_id
      && c.assigned_hole === cam.assigned_hole
      && c.assigned_role === oppositeRole
      && (!c.paired_with_camera_id || c.paired_with_camera_id === cam.id),
    );
  }

  function findById(id) {
    return cameras?.find((c) => c.id === id);
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
        <Link to="/admin/cameras" className="active">Cameras</Link>
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 6 }}>Cameras</h3>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Register each on-course capture device (one tee + one green per
          par-3 hole). Each row's <code>auth_token</code> is the secret the
          Pi uses to call <code>/api/cameras/&#123;token&#125;/...</code> — keep
          it private and rotate it if a device is lost.
        </p>
      </div>

      {error && <div className="card err-text small">{error}</div>}

      <div className="card">
        <h4 style={{ marginBottom: 8 }}>Register new camera</h4>
        <form onSubmit={createCamera}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div className="field" style={{ flex: 1, minWidth: 150 }}>
              <label className="small">Type</label>
              <select value={newKind}
                      onChange={(e) => setNewKind(e.target.value)}
                      disabled={creating}>
                <option value="pi">Pi agent</option>
                <option value="ip">IP camera (RTSP)</option>
              </select>
            </div>
            <div className="field" style={{ flex: 2, minWidth: 200 }}>
              <label className="small">Course</label>
              <select
                value={newCourseId}
                onChange={(e) => setNewCourseId(e.target.value)}
                disabled={creating}
              >
                <option value="">Pick a course…</option>
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: 1, minWidth: 80 }}>
              <label className="small">Hole</label>
              <input
                type="number" min="1" max="18"
                value={newHole}
                onChange={(e) => setNewHole(e.target.value)}
                disabled={creating}
              />
            </div>
            <div className="field" style={{ flex: 1, minWidth: 120 }}>
              <label className="small">Role</label>
              <select value={newRole} onChange={(e) => setNewRole(e.target.value)} disabled={creating}>
                <option value="tee">tee</option>
                <option value="green">green</option>
              </select>
            </div>
            <div className="field" style={{ flex: 2, minWidth: 200 }}>
              <label className="small">Name (optional)</label>
              <input
                type="text" placeholder="Hole 3 tee — east tree"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                disabled={creating}
              />
            </div>
            <div>
              <button type="submit" disabled={creating}>
                {creating ? "Creating…" : "Register"}
              </button>
            </div>
          </div>

          {/* AN IP CAMERA CANNOT INTRODUCE ITSELF. A Pi arrives holding
              its token; this one answers RTSP and waits, so everything
              needed to find it has to be typed here. */}
          {newKind === "ip" && (
            <div style={{ marginTop: 10, paddingTop: 10,
                          borderTop: "1px solid rgba(120,120,120,0.25)" }}>
              <div className="tiny muted" style={{ marginBottom: 8 }}>
                Where to reach it. The <b>password is not stored here</b> —
                it lives in the recorder's own config, because a camera
                credential in a database its network cannot reach is risk
                with nothing bought for it.
              </div>
              <div className="row" style={{ gap: 8, flexWrap: "wrap",
                                            alignItems: "flex-end" }}>
                <div className="field" style={{ flex: 2, minWidth: 160 }}>
                  <label className="small">Host / IP</label>
                  <input type="text" placeholder="10.0.0.249"
                         value={newHost} disabled={creating}
                         onChange={(e) => setNewHost(e.target.value)} />
                </div>
                <div className="field" style={{ flex: 1, minWidth: 90 }}>
                  <label className="small">RTSP port</label>
                  <input type="number" value={newPort} disabled={creating}
                         onChange={(e) => setNewPort(e.target.value)} />
                </div>
                <div className="field" style={{ flex: 2, minWidth: 190 }}>
                  <label className="small">Main stream path</label>
                  <input type="text" value={newPath} disabled={creating}
                         onChange={(e) => setNewPath(e.target.value)} />
                </div>
                <div className="field" style={{ flex: 2, minWidth: 190 }}>
                  <label className="small">Substream path</label>
                  <input type="text" value={newSubPath} disabled={creating}
                         onChange={(e) => setNewSubPath(e.target.value)} />
                </div>
                <div className="field" style={{ flex: 1, minWidth: 110 }}>
                  <label className="small">Username</label>
                  <input type="text" value={newUser} disabled={creating}
                         onChange={(e) => setNewUser(e.target.value)} />
                </div>
                <div className="field" style={{ flex: 2, minWidth: 170 }}>
                  <label className="small">Model (optional)</label>
                  <input type="text" placeholder="Hanwha XNV-L6080"
                         value={newModel} disabled={creating}
                         onChange={(e) => setNewModel(e.target.value)} />
                </div>
              </div>
            </div>
          )}
        </form>
      </div>

      {cameras === null && (
        <div className="card"><div className="shimmer" style={{ height: 80 }} /></div>
      )}

      {cameras?.length === 0 && (
        <div className="card muted center" style={{ padding: 40 }}>
          No cameras registered yet. Use the form above to register the first one.
        </div>
      )}

      <div className="stack" style={{ gap: 10 }}>
        {cameras?.map((cam) => {
          const partner = cam.paired_with_camera_id
            ? findById(cam.paired_with_camera_id) : null;
          const candidates = pairCandidates(cam);
          const isBusy = !!busy[cam.id];
          const tokenVisible = !!revealedToken[cam.id];
          return (
            <div key={cam.id} className="card tight" style={{ margin: 0, padding: 12 }}>
              <div style={{ display: "flex", alignItems: "flex-start",
                            gap: 12, flexWrap: "wrap" }}>
                {/* minWidth earns its keep now that the live view sits in
                    this column: below ~300px the picture is not worth
                    looking at, so the controls wrap under it instead. */}
                <div className="small" style={{ flex: 1, minWidth: 300 }}>
                  <b>#{cam.id}</b>{" "}
                  <span className={`pill small ${cam.enabled ? "ok" : "warn"}`}>
                    {cam.enabled ? "enabled" : "disabled"}
                  </span>{" "}
                  <span className={`pill small ${cam.assigned_role === "tee" ? "" : "ok"}`}>
                    {cam.assigned_role}
                  </span>{" "}
                  {cam.assigned_role === "tee" && cam.triggering_enabled === false && (
                    <span className="pill small warn">triggering paused</span>
                  )}{" "}
                  {cam.battery && (
                    <span
                      className={`pill small ${
                        cam.battery.level === "critical" || cam.battery.low
                          ? "warn"
                          : cam.battery.level === "low"
                            ? ""
                            : "ok"
                      }`}
                      style={
                        cam.battery.level === "critical" || cam.battery.low
                          ? { background: "#dc2626", color: "#fff" }
                          : cam.battery.level === "low"
                            ? { background: "#f59e0b", color: "#1a1a1a" }
                            : undefined
                      }
                      title={[
                        `Battery ${cam.battery.voltage}V`,
                        cam.battery.watts != null
                          ? `drawing ${cam.battery.watts}W`
                          : null,
                        cam.battery.est_days != null
                          ? `~${cam.battery.est_days} days left at current use`
                          : null,
                        cam.battery.updated_at
                          ? `updated ${tsRel(cam.battery.updated_at)}`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      🔋 {cam.battery.voltage}V · ~{cam.battery.percent}%
                      {cam.battery.est_days != null && (
                        <> · ~{cam.battery.est_days}d</>
                      )}
                      {cam.battery.low && <> · LOW</>}
                    </span>
                  )}{" "}
                  {cam.focus && (
                    <span
                      className={`pill small ${cam.focus.unreliable ? "" : "ok"}`}
                      style={
                        cam.focus.unreliable
                          ? { background: "#f59e0b", color: "#1a1a1a" }
                          : undefined
                      }
                      title={[
                        `Focus score ${cam.focus.score}`,
                        "higher is sharper; compare against THIS camera over time, not against another one — the number depends on what the camera is pointed at",
                        cam.focus.brightness != null
                          ? `brightness ${cam.focus.brightness}`
                          : null,
                        cam.focus.exposure === "dark"
                          ? "too dark to trust — fix exposure before reading the score"
                          : cam.focus.exposure === "bright"
                            ? "blown out — fix exposure before reading the score"
                            : null,
                        cam.focus.updated_at
                          ? `updated ${tsRel(cam.focus.updated_at)}`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      🔎 {cam.focus.score}
                      {cam.focus.best != null && (
                        <> · best {cam.focus.best}</>
                      )}
                      {cam.focus.unreliable && <> · {cam.focus.exposure}</>}
                    </span>
                  )}{" "}
                  {/* Course FIRST, and always — it is the camera's real
                      placement. `name` is free text and goes stale the
                      moment a camera moves, so it renders after, dimmer,
                      and is never a substitute for the course. */}
                  <span style={{ fontWeight: 600 }}>
                    {" · "}{cam.course_name || `course ${cam.course_id}`}
                    {" · hole "}{cam.assigned_hole}
                  </span>
                  {cam.name && (
                    <span className="tiny muted"> · “{cam.name}”</span>
                  )}
                  {cam.kind === "ip" && (
                    <span className="tiny" style={{
                      marginLeft: 6, padding: "1px 7px", borderRadius: 999,
                      border: "1px solid rgba(70,130,200,0.55)",
                      background: "rgba(70,130,200,0.14)",
                    }}>IP camera</span>
                  )}
                  <div className="tiny muted" style={{ marginTop: 2 }}>
                    {/* KEY OFF THE HEARTBEAT, NOT THE KIND. An IP camera
                        driven by a Pi still has an agent calling in with
                        the token -- the Hanwha is only the lens -- so it
                        has a real "last seen" and hiding it threw away
                        the fastest way to tell whether the site is up.
                        Only a camera nothing has EVER called in for gets
                        the explanation instead of a time. */}
                    {cam.kind === "ip" && cam.stream_model && (
                      <>{cam.stream_model} · </>
                    )}
                    {cam.last_seen_at ? (
                      <>
                        last seen:{" "}
                        <span style={{
                          color: heartbeatTone(secsAgo(cam.last_seen_at)).color,
                          fontWeight: 600,
                        }}
                          title={`The agent heartbeats every 60s. Under ${HEARTBEAT_LATE_SEC}s is healthy; beyond ${HEARTBEAT_DOWN_SEC}s it is down.`}
                        >
                          {tsRel(cam.last_seen_at)}
                          {" · "}
                          {heartbeatTone(secsAgo(cam.last_seen_at)).label}
                        </span>
                        {cam.last_event_at && <> · last event: {tsRel(cam.last_event_at)} ({cam.last_event_status})</>}
                        {cam.firmware_version && <> · fw {cam.firmware_version}</>}
                      </>
                    ) : cam.kind === "ip" ? (
                      <>
                        nothing has ever called in for this camera — an RTSP
                        camera has no heartbeat of its own, so this needs a
                        Pi or a bridge running against its token
                      </>
                    ) : (
                      <>last seen: never</>
                    )}
                  </div>
                  {cam.kind === "ip" && cam.rtsp_url && (
                    <div className="tiny" style={{ marginTop: 6,
                                                   fontFamily: "monospace" }}>
                      <div>main: <code>{cam.rtsp_url}</code></div>
                      {cam.rtsp_substream_url && (
                        <div>sub:{" "}<code>{cam.rtsp_substream_url}</code></div>
                      )}
                      <div className="muted" style={{ fontFamily: "inherit",
                                                      marginTop: 3 }}>
                        Swap <code>PASSWORD</code> for the camera's own — it is
                        deliberately not stored here.
                      </div>
                    </div>
                  )}
                  <div className="tiny" style={{ marginTop: 6, fontFamily: "monospace" }}>
                    auth_token:{" "}
                    {tokenVisible ? (
                      <>
                        <code>{cam.auth_token}</code>
                        <button
                          type="button" className="ghost small"
                          onClick={() => copyToken(cam.auth_token)}
                          style={{ marginLeft: 8 }}
                        >
                          Copy
                        </button>
                      </>
                    ) : (
                      <button
                        type="button" className="ghost small"
                        onClick={() => setRevealedToken((r) => ({ ...r, [cam.id]: true }))}
                      >
                        Show
                      </button>
                    )}
                  </div>

                  {/* THE PICTURE LIVES IN THE CARD, not under it. The
                      right-hand column is tall — pairing, lens, shutter —
                      and the left column runs out of content well above
                      its foot, so a full-width panel below the card was
                      pushing the picture off screen past empty space.
                      Here it fills that space and sits beside the very
                      controls it exists to be watched against: nudge the
                      zoom, see the zoom.

                      AND IT IS ALWAYS THERE. It used to be an empty gap
                      until somebody pressed Watch, which asked a Pi on a
                      cellular modem for ten frames a second to answer
                      "is it still pointed at the tee" — one frame's
                      question. The snapshot answers it on page load, for
                      every camera, with no traffic on the device; live
                      is a click away on the picture itself. */}
                  <div
                    ref={watchingCamId === cam.id ? panelRef : undefined}
                    className="card tight"
                    style={{
                      margin: "10px 0 0", padding: 8, background: "#000",
                      // EXPANDED IS A LAYOUT, not just a bigger box. The
                      // panel covers the viewport and becomes a column:
                      // the header and the lens keep their natural
                      // height and the picture takes everything left,
                      // which is the whole point of pressing it.
                      ...(expanded && watchingCamId === cam.id ? {
                        position: "fixed", inset: 0, zIndex: 9000,
                        margin: 0, borderRadius: 0, overflow: "auto",
                        display: "flex", flexDirection: "column", gap: 6,
                      } : null),
                    }}
                  >
                    {watchingCamId === cam.id ? (
                      <>
                        <div
                          className="inline"
                          style={{ justifyContent: "space-between",
                                   marginBottom: 6, gap: 8, width: "100%" }}
                        >
                          {/* The title wraps in this narrower column, so it
                              takes the slack and Close keeps its corner. */}
                          <div className="small" style={{ color: "#bbb",
                                                          flex: 1, minWidth: 0 }}>
                            Live · #{cam.id}
                            {cam.name && <> — {cam.name}</>}
                            {" · "}hole {cam.assigned_hole} {cam.assigned_role}
                          </div>
                          <button type="button" className="ghost small"
                                  style={{ width: "auto", flexShrink: 0 }}
                                  onClick={toggleExpanded}
                                  title={expanded
                                    ? "Back to the card (Escape does this too)"
                                    : "Fill the screen — the picture is how "
                                      + "focus and trigger zones are judged, "
                                      + "and a third of a column is not "
                                      + "enough of it"}>
                            {expanded ? "⤡ Shrink" : "⤢ Expand"}
                          </button>
                          <button type="button" className="ghost small"
                                  style={{ width: "auto", flexShrink: 0 }}
                                  onClick={stopWatch}>
                            Close
                          </button>
                        </div>
                        {/* THE BOX IS THE PICTURE'S BOUNDS, exactly, in
                            both layouts — the zone editor portals its
                            drawing surface into this element and pins it
                            to the edges, so a box even slightly larger
                            than the image puts every rectangle somewhere
                            the camera is not looking. Normally that is
                            free: a block image at width 100% makes its
                            own parent's height. Expanded it has to be
                            asked for, with the frame's own ratio and a
                            cap on both axes, so the box letterboxes
                            along with the picture inside it. */}
                        <div
                          ref={setPictureEl}
                          style={{
                            position: "relative",
                            background: "#000",
                            ...(expanded ? {
                              // Width first, height derived. A box given
                              // only a ratio and two maximums has no
                              // basis to size from in a flex column and
                              // collapses or overflows depending on the
                              // browser; a definite width with the
                              // ratio is arithmetic.
                              //
                              // What is subtracted is what else is in
                              // the column, counted rather than guessed
                              // at once: the title row always, the lens
                              // rows on an IP camera, the zone editor's
                              // strip while it is open. Guessing it was
                              // how the help line under the sliders got
                              // cut off the bottom of the screen.
                              width: `min(100%, calc((100vh - ${
                                74 + (cam.kind === "ip" ? 152 : 0)
                                   + (zoningCamId === cam.id ? 150 : 0)
                              }px) * ${
                                (liveNatural?.w || 16) / (liveNatural?.h || 9)
                              }))`,
                              aspectRatio: `${liveNatural?.w || 16} / ${
                                liveNatural?.h || 9}`,
                              margin: "auto",
                            } : { minHeight: 240 }),
                          }}
                        >
                          {liveFrameSrc && (
                            <img
                              src={liveFrameSrc}
                              alt=""
                              onLoad={(e) => {
                                const { naturalWidth: w, naturalHeight: h } = e.target;
                                if (w && h && (liveNatural?.w !== w
                                               || liveNatural?.h !== h)) {
                                  setLiveNatural({ w, h });
                                }
                              }}
                              style={{
                                display: "block",
                                width: "100%",
                                height: "auto",
                              }}
                            />
                          )}

                          {!liveFrameSrc && (
                            <div
                              style={{
                                position: "absolute",
                                inset: 0,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                color: "#bbb",
                                fontSize: 13,
                              }}
                            >
                              Waiting for live frame from Pi…
                            </div>
                          )}
                        </div>

                        {/* THE LENS, at the bottom of the watch area,
                            because the picture above is the only thing
                            that can tell you what a nudge did. */}
                        {cam.kind === "ip" && (
                          <LensBar
                            cam={cam}
                            busy={isBusy}
                            note={lensNote[cam.id]}
                            onLens={(op, amt) => lens(cam, op, amt)}
                            onZoom={(f) => zoomTo(cam, f)}
                            onMarkEnd={(end) => markZoomEnd(cam, end)}
                            onShutter={(v) => exposure(cam, v)}
                          />
                        )}

                        {zoningCamId === cam.id && (
                          <TriggerZones
                            cam={cam}
                            adminPassword={adminPassword}
                            frameW={liveNatural?.w}
                            frameH={liveNatural?.h}
                            portalTarget={pictureEl}
                            onSaved={() => load()}
                            onClose={() => setZoningCamId(null)}
                          />
                        )}
                      </>
                    ) : (
                      <CameraStill
                        cam={cam}
                        adminPassword={adminPassword}
                        disabled={isBusy}
                        onWatch={() => startWatch(cam)}
                      />
                    )}
                  </div>
                </div>
                <div style={{
                  width: 230, flexShrink: 0,
                  display: "flex", flexDirection: "column",
                  gap: 6, alignItems: "stretch",
                }}>
                  {partner ? (
                    <span className="small muted" style={{ textAlign: "center" }}>
                      paired with{" "}
                      <code>#{partner.id} ({partner.assigned_role})</code>
                      {" "}
                      <button
                        type="button" className="ghost small"
                        onClick={() => unpair(cam)} disabled={isBusy}
                      >
                        Unpair
                      </button>
                    </span>
                  ) : candidates.length > 0 ? (
                    <select
                      defaultValue=""
                      disabled={isBusy}
                      onChange={(e) => {
                        const v = parseInt(e.target.value, 10);
                        if (Number.isFinite(v)) pairWith(cam, v);
                      }}
                      className="small"
                      style={{ fontSize: 12 }}
                    >
                      <option value="">Pair with…</option>
                      {candidates.map((p) => (
                        <option key={p.id} value={p.id}>
                          #{p.id} ({p.assigned_role}) {p.name && `— ${p.name}`}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="tiny muted">no eligible partner</span>
                  )}
                  {/* THREE GROUPS, because twelve controls in one stack
                      is a list to read rather than a thing to use. They
                      are sorted by WHEN you reach for them: Live while
                      standing at the camera, Setup when something has
                      been bolted or moved, Device when you are not
                      thinking about pictures at all. */}
                  {/* No Watch button here any more: it is on the
                      picture, where what it does is visible. A control
                      for "show me that" belongs on the that.

                      The heading only appears when something is under
                      it: an IP green camera has neither Capture (a green
                      records when its tee says so) nor Focus mode (its
                      lens arms that itself), and a heading over nothing
                      reads as a control that failed to load. */}
                  {(cam.assigned_role === "tee" || cam.kind !== "ip") && (
                    <div className="tiny upper muted" style={{ marginTop: 2 }}>Live</div>
                  )}
                  {cam.assigned_role === "tee" && (
                    <button
                      type="button" className="small"
                      onClick={() => captureNow(cam)}
                      disabled={isBusy || !cam.enabled}
                      title="Record 30s now on this camera and its paired green, and send it through produce"
                    >
                      Capture
                    </button>
                  )}
                  {/* FOCUS MODE IS A BUTTON ONLY WHERE NOTHING ELSE CAN
                      ARM IT. All it does is raise the rate the sharpness
                      score is reported at, from the heartbeat's once a
                      minute to every few seconds — it does not touch the
                      lens. On an IP camera the focus nudges below arm it
                      themselves, because pressing one is the entire
                      reason to want the fast number; asking for it
                      separately first was a step that only ever got
                      skipped. A Pi's lens is a ring somebody turns by
                      hand, with no command to ride along on, so there it
                      stays a button. */}
                  {cam.kind !== "ip" && (
                    <button
                      type="button"
                      className={cam.focus?.focus_seconds ? "small" : "secondary small"}
                      onClick={() => focusMode(cam)}
                      disabled={isBusy || !cam.enabled}
                      title={
                        cam.focus?.focus_seconds
                          ? `Focus mode on — ${cam.focus.focus_seconds}s left. The score updates every few seconds; turn the ring until it peaks. Press again to stop it now.`
                          : "Report the focus score every few seconds for 10 minutes, so you can turn the lens ring against a live number. Resets the session best."
                      }
                    >
                      {cam.focus?.focus_seconds
                        ? `Stop focusing (${cam.focus.focus_seconds}s)`
                        : "Focus mode"}
                    </button>
                  )}

                  {/* SETUP: everything that describes where this camera
                      is pointed. Two of these need a partner, because
                      they describe a PAIR of viewpoints rather than one
                      camera. */}
                  <div className="tiny upper muted" style={{ marginTop: 8 }}>Setup</div>
                  {cam.assigned_role === "tee" && (
                    <button
                      type="button"
                      className={zoningCamId === cam.id ? "small" : "secondary small"}
                      disabled={isBusy}
                      title={
                        watchingCamId === cam.id
                          ? "Draw the boxes a golfer has to stand in for this camera to trigger — one per tee, so the path between them does not fire it"
                          : "Watch this camera first: the zones are drawn on its live picture, so you can see where the tees actually are"
                      }
                      onClick={() => {
                        if (watchingCamId !== cam.id) startWatch(cam);
                        setZoningCamId(zoningCamId === cam.id ? null : cam.id);
                      }}
                    >
                      {zoningCamId === cam.id
                        ? "Done with zones"
                        : `Trigger zones${
                            (cam.tee_zones?.boxes || []).length
                              ? ` (${cam.tee_zones.boxes.length})` : " — none"}`}
                    </button>
                  )}
                  {/* GREEN PIXELS TO TEE PIXELS — the only thing that
                      aims the end of the tracer. A homography between
                      two bolted-down viewpoints, so it belongs to the
                      pair and is fitted once; from a Production card it
                      looked like a property of the clip, which is how a
                      hole ends up calibrated a dozen times from a dozen
                      clips, each fit overwriting the last. */}
                  {partner && (
                    <button
                      type="button" className="secondary small"
                      onClick={() => openCalibrator(cam)}
                      disabled={isBusy}
                      title="Map the green camera's view onto the tee camera's by clicking the same ground features in both. Done once for this pair — every swing they record afterwards is aimed by it."
                    >
                      ⊹ Aim: calibrate green→tee
                    </button>
                  )}
                  {/* GREEN PIXELS TO FEET — the only thing that can
                      measure a yardage. Closest-to-the-pin and the
                      distance plate come from it, and nothing else does.
                      It was offered on tee cameras too until it turned
                      out nothing read that fit: aiming goes through the
                      green→tee map above, and green_to_image, the
                      feet-to-tee-pixels half of the old route, has no
                      callers left. */}
                  {cam.assigned_role === "green" && (
                    <button
                      type="button" className="secondary small"
                      onClick={() => setCalibratingCam(cam)}
                      disabled={isBusy}
                      title="Map this camera's pixels onto the green in feet, by marking four edges of the putting surface. This is what measures closest-to-the-pin and stamps the distance on a clip. Aiming the tracer is the separate green→tee button."
                    >
                      {cam.green_homography
                        ? "Measure: distances ✓"
                        : "Measure: calibrate distances"}
                    </button>
                  )}
                  {/* THE TWO MARKS THAT CHANGE EVERY MORNING. The
                      calibrations above describe where the cameras are
                      bolted; the pin is cut to a new spot each day and
                      the tee markers are walked forward or back. Same
                      pair of pictures, opposite lifetime.

                      Named for what each mark DOES, because the tee one
                      is a rectangle drawn on the tee view and so is the
                      trigger zone above it — and they feed completely
                      different things. This one bounds the BALL SEARCH;
                      the zones decide whether a person is on the tee. */}
                  {partner && (
                    <button
                      type="button" className="secondary small"
                      onClick={() => setDailyCam(cam)}
                      disabled={isBusy}
                      title="Today, on this hole: the flagstick on the green view, and the patch of turf the ball search is confined to on the tee view. Both move overnight; the calibrations do not. Not the same as trigger zones, which decide when to record."
                    >
                      ⛳ Today&apos;s pin &amp; ball area
                    </button>
                  )}
                  {cam.kind === "ip" && (
                    <div
                      style={{
                        display: "flex", flexWrap: "wrap", gap: 6,
                        alignItems: "center", width: "100%",
                        padding: "6px 8px", borderRadius: 8,
                        border: "1px solid rgba(120,120,120,0.35)",
                        background: "rgba(120,120,120,0.06)",
                      }}
                    >
                      <StreamReadout
                        stream={cam.stream}
                        busy={isBusy}
                        onRead={() => readStreamProfile(cam)}
                      />
                      {/* EVERY CONTROL IS ON THE PICTURE NOW — zoom,
                          focus and shutter alike. What is left here is
                          the diagnostics: what the camera says it is
                          sending and what it says its exposure is,
                          which are readings to check rather than
                          controls to reach for. No prose: the labels
                          and the tooltips carry it. */}
                      <div style={{ width: "100%", borderTop:
                                    "1px solid rgba(120,120,120,0.25)",
                                    paddingTop: 6, marginTop: 2 }} />
                      {/* The heading only when there is a reading
                          under it — a label over nothing reads as a
                          panel that failed to load. */}
                      {cam.exposure && (
                        <div className="tiny upper muted"
                             title="What the camera reports its exposure to be — its own words, not the preset that was sent. Set it from the slider on the live picture.">
                          Shutter
                        </div>
                      )}
                      <ExposureReadout exp={cam.exposure} />
                    </div>
                  )}
                  {/* DEVICE: nothing here is about the picture. Kept
                      last and together so Delete is nowhere near the
                      buttons used every day. */}
                  <div className="tiny upper muted" style={{ marginTop: 8 }}>Device</div>
                  {cam.assigned_role === "tee" && (
                    <button
                      type="button" className="secondary small"
                      onClick={() => toggleTriggering(cam)} disabled={isBusy}
                      title="Pause/resume motion triggering. Paused = camera stays online but won't record events (use when it's powered on indoors)."
                    >
                      {cam.triggering_enabled === false
                        ? "Resume triggering"
                        : "Pause triggering"}
                    </button>
                  )}
                  <button
                    type="button" className="secondary small"
                    onClick={() => toggleEnabled(cam)} disabled={isBusy}
                  >
                    {cam.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    type="button" className="secondary small"
                    onClick={() => openMove(cam)} disabled={isBusy}
                    title="Rename, or move to a different course / hole / role"
                  >
                    Edit
                  </button>
                  <button
                    type="button" className="secondary small"
                    onClick={() => rotateToken(cam)} disabled={isBusy}
                    title="Mint a new auth_token for this camera"
                  >
                    Rotate token
                  </button>
                  <button
                    type="button" className="ghost small err-text"
                    onClick={() => deleteCamera(cam)} disabled={isBusy}
                  >
                    Delete
                  </button>
                </div>
              </div>

              {movingCam === cam.id && (
                <div
                  className="card tight"
                  style={{ margin: "10px 0 0", padding: 10, background: "var(--surface-alt)" }}
                >
                  <div className="small" style={{ marginBottom: 6 }}>
                    <b>Edit camera #{cam.id}</b>{" "}
                    <span className="tiny muted">
                      · changing course / hole / role will auto-unpair if the
                      existing partner no longer fits
                    </span>
                  </div>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
                    <div className="field" style={{ flex: 2, minWidth: 180 }}>
                      <label className="small">Name</label>
                      <input
                        type="text"
                        placeholder="e.g. Tee cam #1"
                        value={moveDraft.name}
                        onChange={(e) => setMoveDraft((d) => ({ ...d, name: e.target.value }))}
                        disabled={isBusy}
                      />
                    </div>
                    <div className="field" style={{ flex: 2, minWidth: 180 }}>
                      <label className="small">Course</label>
                      <select
                        value={moveDraft.courseId}
                        onChange={(e) => setMoveDraft((d) => ({ ...d, courseId: e.target.value }))}
                        disabled={isBusy}
                      >
                        {courses.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 80 }}>
                      <label className="small">Hole</label>
                      <input
                        type="number" min="1" max="18"
                        value={moveDraft.hole}
                        onChange={(e) => setMoveDraft((d) => ({ ...d, hole: e.target.value }))}
                        disabled={isBusy}
                      />
                    </div>
                    {moveDraft.role === "tee" && (
                      <div className="field" style={{ flex: 1, minWidth: 150 }}>
                        <label className="small">Ball side</label>
                        <select
                          value={moveDraft.ballSide}
                          onChange={(e) => setMoveDraft((d) => ({ ...d, ballSide: e.target.value }))}
                          disabled={isBusy}
                          title="Which side of the golfer's feet the ball sits on, in this camera's view. Fixed per installation — it stops a white shoe being picked as the ball."
                        >
                          <option value="">auto (both sides)</option>
                          <option value="left">left of the feet</option>
                          <option value="right">right of the feet</option>
                        </select>
                      </div>
                    )}
                    <div className="field" style={{ flex: 1, minWidth: 100 }}>
                      <label className="small">Role</label>
                      <select
                        value={moveDraft.role}
                        onChange={(e) => setMoveDraft((d) => ({ ...d, role: e.target.value }))}
                        disabled={isBusy}
                      >
                        <option value="tee">tee</option>
                        <option value="green">green</option>
                      </select>
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 130 }}>
                      <label className="small">Kind</label>
                      <select
                        value={moveDraft.kind}
                        onChange={(e) => setMoveDraft((d) => ({ ...d, kind: e.target.value }))}
                        disabled={isBusy}
                        title="A Pi calls in with its token and carries a fixed-lens module. An IP camera is reached at an address and has a motorised lens the app can drive."
                      >
                        <option value="pi">Pi (fixed lens)</option>
                        <option value="ip">IP camera (RTSP)</option>
                      </select>
                    </div>
                    <div className="inline" style={{ gap: 6 }}>
                      <button
                        type="button"
                        onClick={() => submitMove(cam)}
                        disabled={isBusy}
                      >
                        {isBusy ? "Saving…" : "Apply"}
                      </button>
                      <button
                        type="button" className="ghost"
                        onClick={closeMove} disabled={isBusy}
                      >
                        Cancel
                      </button>
                    </div>
                    {moveDraft.kind === "ip" && (
                      <div style={{
                        width: "100%", marginTop: 4, paddingTop: 8,
                        borderTop: "1px solid rgba(120,120,120,0.3)",
                        display: "flex", gap: 8, flexWrap: "wrap",
                        alignItems: "flex-end",
                      }}>
                        <div className="tiny muted" style={{ width: "100%" }}>
                          Where the recorder finds this camera. The password
                          is deliberately not stored here — it lives in the
                          Pi's own config, because a credential in a cloud
                          database the camera's network cannot reach is risk
                          with no benefit.
                        </div>
                        <div className="field" style={{ flex: 2, minWidth: 150 }}>
                          <label className="small">Host</label>
                          <input
                            type="text" placeholder="192.168.50.11"
                            value={moveDraft.streamHost}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamHost: e.target.value }))}
                            disabled={isBusy}
                          />
                        </div>
                        <div className="field" style={{ flex: 1, minWidth: 80 }}>
                          <label className="small">Port</label>
                          <input
                            type="number" placeholder="554"
                            value={moveDraft.streamPort}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamPort: e.target.value }))}
                            disabled={isBusy}
                          />
                        </div>
                        <div className="field" style={{ flex: 2, minWidth: 160 }}>
                          <label className="small">Main path</label>
                          <input
                            type="text" placeholder="/profile2/media.smp"
                            value={moveDraft.streamPath}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamPath: e.target.value }))}
                            disabled={isBusy}
                            title="Profile numbering differs per model — ffprobe it rather than assuming profile1 is the main stream."
                          />
                        </div>
                        <div className="field" style={{ flex: 2, minWidth: 160 }}>
                          <label className="small">Sub path</label>
                          <input
                            type="text" placeholder="/profile3/media.smp"
                            value={moveDraft.streamSubstreamPath}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamSubstreamPath: e.target.value }))}
                            disabled={isBusy}
                          />
                        </div>
                        <div className="field" style={{ flex: 1, minWidth: 110 }}>
                          <label className="small">Username</label>
                          <input
                            type="text" placeholder="admin"
                            value={moveDraft.streamUsername}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamUsername: e.target.value }))}
                            disabled={isBusy}
                          />
                        </div>
                        <div className="field" style={{ flex: 1, minWidth: 130 }}>
                          <label className="small">Model</label>
                          <input
                            type="text" placeholder="XNV-6080R"
                            value={moveDraft.streamModel}
                            onChange={(e) => setMoveDraft((d) => ({ ...d, streamModel: e.target.value }))}
                            disabled={isBusy}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {cal && (cal.loading || cal.error ? (
        <div role="dialog" onClick={() => setCal(null)}
             style={{ position: "fixed", inset: 0, zIndex: 1200,
                      background: "rgba(0,0,0,0.75)", display: "flex",
                      alignItems: "center", justifyContent: "center",
                      padding: 16 }}>
          <div className="card" onClick={(e) => e.stopPropagation()}
               style={{ margin: 0, padding: 18, maxWidth: 460 }}>
            <div className="row" style={{ justifyContent: "space-between",
                                          gap: 12 }}>
              <b>⊹ Calibrate green→tee</b>
              <button className="btn ghost" style={{ width: "auto" }}
                      onClick={() => setCal(null)}>Close ✕</button>
            </div>
            <div style={{ marginTop: 10 }}>
              {cal.error
                ? <div className="err-text small">{cal.error}</div>
                : <div className="small">
                    Finding a capture from this pair to calibrate on…
                  </div>}
            </div>
          </div>
        </div>
      ) : (
        <ViewMapModal
          uploadId={cal.uploadId}
          adminPassword={adminPassword}
          teeFrame={cal.tee}
          greenFrame={cal.green}
          existing={cal.existing}
          mismatch={cal.mismatch}
          scope={cal.scope}
          onClose={() => setCal(null)}
          onSaved={() => { setCal(null); load(); }}
        />
      ))}

      {dailyCam && (
        <DailyMarksModal
          adminPassword={adminPassword}
          cam={dailyCam}
          onClose={() => { setDailyCam(null); load(); }}
        />
      )}

      {calibratingCam && (
        <GreenCalibrationModal
          adminPassword={adminPassword}
          cam={calibratingCam}
          onClose={() => { setCalibratingCam(null); load(); }}
        />
      )}

      <CameraEventsPanel adminPassword={adminPassword} />
    </div>
  );
}
