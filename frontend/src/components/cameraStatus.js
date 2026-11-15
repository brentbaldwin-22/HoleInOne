/**
 * HOW ALIVE IS THIS CAMERA — the pure part, shared.
 *
 * Extracted so the cameras table and the old console cannot drift
 * about what "late" means. They are read side by side while one
 * replaces the other, and two pages disagreeing about whether a rig
 * is down is worse than either answer on its own.
 *
 * Pure functions only. Anything that renders, fetches or holds state
 * stays in the page that owns it.
 */

/** A timestamp as "3m ago", or "—".
 *
 * BACKEND TIMESTAMPS ARE NAIVE UTC, with no Z suffix. Without a
 * timezone marker the browser reads them as LOCAL time and the diff
 * against now() comes out wrong by the viewer's offset -- five hours
 * in Central, which is how a healthy camera reads "-17985s ago".
 */
export function tsRel(iso) {
  if (!iso) return "—";
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const d = new Date(utcIso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

/** Seconds since an ISO timestamp, or null. Same UTC correction. */
export function secsAgo(iso) {
  if (!iso) return null;
  const utcIso = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  return Math.round((Date.now() - new Date(utcIso).getTime()) / 1000);
}

// The agent heartbeats every 60s (heartbeat_seconds in the Pi config),
// so one missed beat is noise and two is a signal.
export const HEARTBEAT_LATE_SEC = 150;
export const HEARTBEAT_DOWN_SEC = 900;

/** {color, label} for a heartbeat age, so the operator is not doing
 *  the subtraction. The whole point of "last seen" is answering "is
 *  this thing alive right now". */
export function heartbeatTone(sec) {
  if (sec == null) return { color: "#b3261e", label: "never called in" };
  if (sec <= HEARTBEAT_LATE_SEC) return { color: "#2f6b45", label: "live" };
  if (sec <= HEARTBEAT_DOWN_SEC) return { color: "#8a6d1f", label: "late" };
  return { color: "#b3261e", label: "down" };
}

/** Is the agent far enough gone that anything needing the Pi will fail?
 *
 * WHAT A DEAD AGENT TAKES WITH IT is not "camera controls" vs "the
 * rest" -- it is whether the press has to reach the Pi RIGHT NOW.
 * Capture, watch live, trigger zones, focus and the green measure all
 * do. Everything the BACKEND owns stays live on purpose: enable,
 * pause triggering, pair, rename, delete. The Pi picks those up on
 * its next heartbeat, so setting one while it is away is the normal
 * way to queue a change for when it returns.
 */
export function agentIsDown(cam) {
  const sec = secsAgo(cam?.last_seen_at);
  return sec == null || sec > HEARTBEAT_DOWN_SEC;
}

/** Why a Pi-bound control is disabled, in words an operator can act on. */
export function offlineReason(cam) {
  return cam?.last_seen_at
    ? `The agent last called in ${tsRel(cam.last_seen_at)} and is down, `
      + "so nothing that has to reach the Pi can work. Get the link back "
      + "first — this is not about the camera."
    : "No agent has ever called in for this camera, so there is nothing "
      + "here to reach yet.";
}

/** The one-word state of a camera, for a table cell.
 *
 * ORDERED BY WHAT STOPS WHAT. A disabled camera is not recording
 * whatever its heartbeat says, and a live camera with triggering
 * paused is not recording either -- so both outrank the link, which
 * is only the top answer when the camera is otherwise meant to be
 * working.
 */
export function cameraState(cam) {
  if (!cam) return { key: "unknown", label: "unknown", color: "#6b7280" };
  if (cam.enabled === false) {
    return { key: "disabled", label: "Disabled", color: "#6b7280",
             why: "Switched off here. It will not record and the page "
                  + "will not ask it to." };
  }
  const sec = secsAgo(cam.last_seen_at);
  const tone = heartbeatTone(sec);
  if (tone.label !== "live") {
    return { key: tone.label, label: tone.label === "late" ? "Late" : "Down",
             color: tone.color,
             why: offlineReason(cam) };
  }
  if (cam.triggering_enabled === false) {
    return { key: "paused", label: "Paused", color: "#8a6d1f",
             why: "Online, but triggering is paused: it will not start a "
                  + "recording by itself." };
  }
  return { key: "live", label: "Live", color: "#2f6b45",
           why: `The agent called in ${tsRel(cam.last_seen_at)}.` };
}
