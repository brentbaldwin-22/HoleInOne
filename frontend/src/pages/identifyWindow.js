// WHICH SHOTS TO SHOW WHILE PAIRING ONE GOLFER ON ONE HOLE.
//
// A group that tees off at noon reaches hole 2 about a quarter of an
// hour later, so the 9am and 3pm shots on that camera are not theirs
// and exist only to be scrolled past. The backend works the window out
// from the tee time (identify.shot_window) and widens it hole by hole,
// because pace error accumulates.
//
// Separated from the page because the rule that matters is not the
// narrowing, it is the three ways the narrowing must GIVE UP -- and a
// rule about not losing data should be testable without a browser.

// Tag a naive-UTC string so the browser stops reading it as local.
// Mirrors parseApiDate in time.js; kept here so this file is pure.
const HAS_TZ = /[zZ]$|[+-]\d{2}:?\d{2}$/;

export function asDate(iso) {
  if (!iso) return null;
  if (iso instanceof Date) return iso;
  const s = String(iso);
  const d = new Date(HAS_TZ.test(s) ? s : s + "Z");
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * @param clips     every shot on this hole
 * @param win       [startIso, endIso] for this golfer and hole, or null
 * @param playerId  the golfer being paired, so their own shots survive
 * @param showAll   the operator has asked to see past the window
 * @returns {shown, hidden, windowed}
 */
export function visibleClips(clips, win, playerId, showAll) {
  const all = clips || [];
  const lo = win ? asDate(win[0]) : null;
  const hi = win ? asDate(win[1]) : null;

  // NO WINDOW, NO NARROWING. A golfer with no tee time is not a reason
  // to show an empty grid.
  if (!lo || !hi || showAll) {
    return { shown: all, hidden: 0, windowed: false };
  }

  const kept = all.filter((c) => {
    // ALREADY PAIRED TO THIS GOLFER: never hidden, whatever the clock
    // says. A mis-tap has to stay visible to be undone.
    if (playerId != null && c.participant_id === playerId) return true;
    const t = asDate(c.captured_at);
    return t && t >= lo && t <= hi;
  });

  // THE WINDOW HID EVERYTHING. Then the model is wrong for this round
  // -- a shotgun start, a clock corrected mid-day -- and the operator
  // needs the footage, not our theory of where it should be.
  if (kept.length === 0) {
    return { shown: all, hidden: 0, windowed: false };
  }

  return { shown: kept, hidden: all.length - kept.length, windowed: true };
}
