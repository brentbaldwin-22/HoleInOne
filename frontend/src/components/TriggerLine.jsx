/* WHY A TRIGGER FIRED, IN ONE PLACE.
 *
 * The events table has always said that an event happened; this says
 * which mechanism produced it, so a clip of somebody walking to their
 * cart and a clip of a struck ball no longer read identically.
 *
 * IT LIVED ON THE OLD CAMERAS PAGE, which is the reason this file
 * exists. The current page renders its own version of the same field
 * -- `trigger_detail.kind` and the zone, and nothing else -- so a
 * dwell that the rig had fully described, with how long somebody
 * stood there, the threshold it beat, the blinks forgiven and what
 * the microphone was doing at the time, arrived at the screen and was
 * printed as the single word "dwell". The detail was never missing.
 * Nothing on the page the operator actually opens was reading it.
 *
 * So both pages render this, and a field added here reaches both.
 */

export const TONE_CLASS = {
  ok: "ok",
  info: "live",
  warn: "warn",
  bad: "danger",
};

/** Wall-clock from the Pi's epoch seconds, to a tenth.
 *
 * A TENTH BECAUSE THE QUESTION IS SUB-SECOND. "The sound came a second
 * before the picture" was this project's hardest bug to see; a line
 * that rounds impact to the nearest second cannot be used to check
 * that kind of claim against the clip.
 */
function clockOf(epochSec) {
  if (epochSec == null || !isFinite(epochSec)) return null;
  const d = new Date(epochSec * 1000);
  if (isNaN(d.getTime())) return null;
  const p2 = (n) => String(n).padStart(2, "0");
  return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`
    + `.${Math.floor((epochSec % 1) * 10)}`;
}

/** A zone as a person refers to it: its label, falling back to index.
 *
 * The index is what the config speaks; the label is what the operator
 * drew. Neither alone is enough -- a label can be blank, and an index
 * on its own makes them count boxes -- so show the label and keep the
 * index beside it.
 */
function zoneOf(detail) {
  if (!detail) return null;
  const { zone, zone_label: label } = detail;
  if (label && zone != null) return `${label} (zone ${zone})`;
  if (label) return label;
  if (zone != null) return `zone ${zone}`;
  return null;
}

/** WHAT THE SWING JUDGE SAID, as the words beside the clip.
 *
 * THREE STATES, NOT TWO. The rig asks the judge before it spends the
 * link on an upload, and reporting only the answer collapses "nobody
 * asked" into "asked, and nothing came back" -- an event with no
 * verdict field then reads as a configuration the operator should
 * change when the real fault may be a key or a link on the server.
 * They call for opposite actions, so they get different words.
 *
 * A clip refused outright is never registered, so "NOT a swing" is
 * only reachable while verify_drops is off -- which is exactly when
 * somebody is checking the judge against real golf and most wants to
 * see it.
 */
function judgeOf(detail) {
  if (!detail || typeof detail !== "object") return [];
  if (detail.verdict != null) {
    const out = [`judge: ${detail.verdict ? "swing" : "NOT a swing"}${
      detail.verdict_confidence ? ` (${detail.verdict_confidence})` : ""}`];
    if (detail.verdict_reason) out.push(`"${detail.verdict_reason}"`);
    return out;
  }
  if (detail.verdict_asked) {
    return [`judge: could not tell${
      detail.verdict_why ? ` — ${detail.verdict_why}` : ""} (uploaded anyway)`];
  }
  return ["judge: not asked"];
}

/** WHAT MADE THIS EVENT. One line, in the order a person asks it in.
 *
 * The page has always said an event happened and never why, so a clip
 * of somebody walking to their cart and a clip of a struck ball read
 * identically -- and "why did this fire?" is the question an operator
 * actually has when a clip is wrong. It is also the only way to see,
 * from here, whether the microphone or the motion dwell is doing the
 * work on a given tee.
 *
 * Every field is optional. A Pi that predates this sends nothing, a Pi
 * that could not describe itself sends nothing, and both say so rather
 * than render an empty frame that looks like a value of zero.
 */
export function TriggerLine({ detail }) {
  if (!detail || typeof detail !== "object") {
    return (
      <div className="small muted" style={{ marginTop: 6 }}>
        Trigger: not recorded — this Pi is running an agent from before
        the trigger was described, or could not describe it.
      </div>
    );
  }
  const kind = detail.kind;
  const zone = zoneOf(detail);
  const at = clockOf(detail.at);
  const bits = [];
  let head = "Trigger";
  let tone = TONE_CLASS.info;

  if (kind === "audio") {
    head = "Audio — impact heard";
    tone = TONE_CLASS.ok;
    if (at) bits.push(`at ${at}`);
    if (detail.ratio != null) bits.push(`\u00d7${detail.ratio} over the noise floor`);
    if (detail.attack_ms != null) bits.push(`attack ${detail.attack_ms} ms`);
    bits.push(zone
      ? `movement in ${zone}`
      + (detail.confirm_tolerance_sec != null
          ? ` within ${detail.confirm_tolerance_sec}s`
          : "")
      : (detail.why || "confirmed"));
    if (Array.isArray(detail.window) && detail.window.length === 2) {
      const a = clockOf(detail.window[0]);
      const b = clockOf(detail.window[1]);
      const len = detail.window[1] - detail.window[0];
      if (a && b) bits.push(`cut ${a}–${b} (${len.toFixed(1)}s)`);
    }
    for (const b of judgeOf(detail)) bits.push(b);
  } else if (kind === "dwell") {
    head = "Motion dwell";
    if (zone) {
      bits.push(detail.stood_sec != null
        ? `somebody stood in ${zone} for ${detail.stood_sec}s`
        : `somebody stood in ${zone}`);
    } else if (detail.stood_sec != null) {
      bits.push(`somebody stood on the tee for ${detail.stood_sec}s`);
    }
    if (detail.dwell_sec != null) bits.push(`threshold ${detail.dwell_sec}s`);
    // A RESTART IS A BLINK FORGIVEN, not a fault -- but a trigger that
    // needed several of them is one the detector nearly missed, which
    // is worth seeing next to a clip that starts late.
    //
    // THIS TRIGGER'S, NOT THE AGENT'S LIFETIME TOTAL. `restarts` used
    // to be the latter -- zeroed when the agent booted and never
    // again -- so an event reading "70 restart(s)" beside "stood 2.2s"
    // said "the detector nearly missed this one 70 times" when it
    // meant "70 people have crossed a zone since breakfast". The two
    // want opposite responses, and the sentence above is the one a
    // reader takes from it. The rig sends both now; the lifetime
    // figure is still worth showing, just not as this one's.
    if (detail.restarts) bits.push(`${detail.restarts} restart(s)`);
    if (detail.restarts_total != null
        && detail.restarts_total !== detail.restarts) {
      bits.push(`${detail.restarts_total} since the agent started`);
    }
    if (at) bits.push(`at ${at}`);
  } else if (kind === "capture") {
    head = "Operator capture";
    if (detail.capture_sec != null) bits.push(`${detail.capture_sec}s requested`);
    if (zone) bits.push(`somebody in ${zone}`);
    if (at) bits.push(`at ${at}`);
  } else if (kind === "manual") {
    head = "Manual trigger";
    bits.push("fired by the /tmp/golfreelz-trigger sentinel on the Pi");
    if (at) bits.push(`at ${at}`);
  } else {
    bits.push(JSON.stringify(detail).slice(0, 200));
  }

  // THE EARS, ALONGSIDE THE DWELL THAT FIRED. "The dwell did it" is
  // half an answer while an audio trigger is also running: what the
  // operator needs is whether the microphone was listening and heard
  // nothing, or was not listening at all. Those need different fixes
  // and look identical from here without this.
  const a = detail.audio;
  let ears = null;
  if (a && kind !== "audio") {
    ears = !a.healthy
      ? "ears NOT hearing the course (mic off, or the ring has stalled)"
      : a.shadow
        ? `ears listening in shadow — ${a.heard} impact(s) heard so far`
        : `ears live — ${a.heard} heard, ${a.cuts} cut`;
  }

  return (
    <div className="small" style={{ marginTop: 6, lineHeight: 1.5 }}>
      <span className={`pill small ${tone}`}><b>{head}</b></span>{" "}
      <span className="muted">{bits.filter(Boolean).join(" · ")}</span>
      {ears && (
        <div className="muted" style={{ marginTop: 2 }}>
          {a.healthy ? "" : "⚠ "}{ears}
        </div>
      )}
    </div>
  );
}
