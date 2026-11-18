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

// Shirt families the cameras genuinely confuse. Mirrors _ALSO in
// backend/app/services/shirt_colour.py -- navy reads blue in sun and
// dark in shade, white in shadow reads grey, and so on. Kept in step
// by hand, which is a cost; the alternative is a round trip to ask
// the server whether two words match.
const ALSO = [
  ["dark", "blue"], ["light", "grey"], ["red", "pink"],
  ["red", "orange"], ["blue", "cyan"], ["dark", "purple"],
];

// How sure both readings must be before a shirt is allowed to hide a
// clip. A torso sample half full of fairway splits its vote; acting on
// that is how the shot being hunted for disappears.
export const SHIRT_MIN_CONF = 0.55;

export function shirtsCompatible(a, b) {
  // NOT KNOWING IS NOT A MISMATCH. Every absent reading matches.
  if (!a || !b) return true;
  if (a === b) return true;
  return ALSO.some(([x, y]) => (a === x && b === y) || (a === y && b === x));
}

/**
 * @param clips     every shot on this hole
 * @param win       [startIso, endIso] for this golfer and hole, or null
 * @param playerId  the golfer being paired, so their own shots survive
 * @param showAll   the operator has asked to see past the window
 * @param shirt     {family, conf} from the golfer's registration photo
 * @returns {shown, hidden, windowed}
 */
export function visibleClips(clips, win, playerId, showAll, shirt) {
  const all = clips || [];
  const lo = win ? asDate(win[0]) : null;
  const hi = win ? asDate(win[1]) : null;

  // NO WINDOW, NO NARROWING. A golfer with no tee time is not a reason
  // to show an empty grid.
  if (!lo || !hi || showAll) {
    return { shown: all, hidden: 0, windowed: false };
  }

  // The shirt is only allowed an opinion when the registration photo
  // gave a confident one. A blurry or half-cropped selfie must not
  // start deleting clips from the grid.
  const sf = (shirt && shirt.family && shirt.conf >= SHIRT_MIN_CONF)
    ? shirt.family : null;

  const kept = all.filter((c) => {
    // ALREADY PAIRED TO THIS GOLFER: never hidden, whatever the clock
    // or the colour says. A mis-tap has to stay visible to be undone.
    if (playerId != null && c.participant_id === playerId) return true;
    const t = asDate(c.captured_at);
    if (!t || t < lo || t > hi) return false;
    // WRONG SHIRT. Only when BOTH sides read confidently: a clip whose
    // own torso sample was unsure keeps its place in the grid.
    if (sf && c.shirt && (c.shirt_conf ?? 0) >= SHIRT_MIN_CONF
        && !shirtsCompatible(sf, c.shirt)) {
      return false;
    }
    return true;
  });

  // THE FILTERS HID EVERYTHING. Then they are wrong for this round --
  // a shotgun start, a clock corrected mid-day, a golfer who changed
  // out of the jacket they registered in -- and the operator needs the
  // footage, not our theory of where it should be.
  if (kept.length === 0) {
    return { shown: all, hidden: 0, windowed: false };
  }

  return { shown: kept, hidden: all.length - kept.length, windowed: true };
}
