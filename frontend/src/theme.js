/**
 * THE SITE'S COLOURS, chosen once in /admin and worn by every page.
 *
 * Two attributes on <html> carry the whole thing:
 *
 *     data-mode="light"   (pinned; see clean())
 *     data-direction="linen"
 *
 * styles.css defines a palette for each combination, so nothing here
 * knows a hex value except the small swatches the admin picker draws.
 * Changing the site's look is therefore two string writes, and every
 * component that already spends var(--primary) or var(--surface)
 * follows without being touched.
 *
 * The chosen theme lives in the database, which means one fetch before
 * we can be sure of it. Rather than render the site in the wrong
 * colours for that half second, the last answer is cached in this
 * browser and applied synchronously at boot; the fetch then corrects
 * it. A browser that refuses storage just gets the default first.
 */
const CACHE_KEY = "golfreelz.theme";

export const DEFAULT_THEME = { direction: "sunset", mode: "light" };

// FOUR LOCKUPS, ONE LOOK. An admin picks one in Settings and the six
// band tokens follow it — the rule under the masthead, the clip stills,
// the hero stripe. Nothing else moves: the type, the buttons, the ink
// and the linen ground are the same on all four, because they are
// furniture rather than branding. styles.css pins --primary and --warn
// for exactly that reason.
//
// `bands` here is the pair shown on the picker swatch, not the ramp
// itself — the real six live in styles.css under [data-direction], so
// the stylesheet stays the one place colour is declared.
//
// ADDING A FIFTH: an entry here, a block in styles.css, the key in
// site_theme.py, and the two PNGs under public/logos/.
export const DIRECTIONS = [
  {
    key: "sunset",
    name: "Sunset",
    logo: "/logos/sunset.png",
    mark: "/logos/sunset-mark.png",
    bands: ["#0047e7", "#fe6610"],
    note: "Blue through to orange, the way the original lockup runs.",
  },
  {
    key: "fairway",
    name: "Fairway",
    logo: "/logos/fairway.png",
    mark: "/logos/fairway-mark.png",
    bands: ["#03855c", "#fee540"],
    note: "Deep green down to a bright yellow.",
  },
  {
    key: "sky",
    name: "Sky",
    logo: "/logos/sky.png",
    mark: "/logos/sky-mark.png",
    bands: ["#0045fd", "#baeffd"],
    note: "One hue, dark to pale — the quietest of the four.",
  },
  {
    key: "ember",
    name: "Ember",
    logo: "/logos/ember.png",
    mark: "/logos/ember-mark.png",
    bands: ["#fed121", "#fc131d"],
    note: "Yellow into red. The loudest, and the warmest on linen.",
  },
];
// LIGHT IS THE ONLY GROUND. The dark blocks are still in styles.css and
// still correct; nothing selects them, because clean() below pins the
// mode. Putting the choice back is this list plus the picker in
// AppearanceCard — deliberately a small undo rather than a deletion.
export const MODES = [
  { key: "light", name: "Light" },
];

const DIRECTION_KEYS = DIRECTIONS.map((d) => d.key);

export function directionMeta(key) {
  return DIRECTIONS.find((d) => d.key === key) || DIRECTIONS[0];
}

/** The logo file that goes with a direction. */
export function logoUrl(direction, size = "full") {
  const d = directionMeta(direction);
  return size === "mark" ? d.mark : d.logo;
}

function clean(theme) {
  const t = theme || {};
  return {
    // "linen" was this look's only key until the lockups became a
    // choice; the same artwork is "sunset" now. A theme stored under
    // the old name is still in the database and in every visitor's
    // localStorage, so it is translated rather than bounced to the
    // default — which would be the same picture under a different
    // name, but only by luck.
    direction: DIRECTION_KEYS.includes(t.direction)
      ? t.direction
      : (t.direction === "linen" ? "sunset" : DEFAULT_THEME.direction),
    // PINNED, not defaulted. A theme stored back when dark was the
    // default still lives in the database and in people's localStorage,
    // and honouring it would put some visitors on a ground the site no
    // longer has a logo for.
    mode: "light",
  };
}

export function readCachedTheme() {
  try {
    return clean(JSON.parse(localStorage.getItem(CACHE_KEY) || "null"));
  } catch {
    return { ...DEFAULT_THEME };
  }
}

/**
 * Stamp the theme on <html> and remember it. Safe to call on every
 * render — writing the same attribute twice costs nothing.
 */
export function applyTheme(theme) {
  const t = clean(theme);
  const root = document.documentElement;
  root.setAttribute("data-mode", t.mode);
  root.setAttribute("data-direction", t.direction);
  // The browser chrome around the page (phone status bar, tab strip)
  // should match the ground the page paints, not stay emerald forever.
  //
  // READ THE BODY'S RESOLVED BACKGROUND, not the --bg token. A custom
  // property hands back its DECLARED value, and in dark mode that is a
  // color-mix() expression — which is a perfectly good CSS value and a
  // meaningless theme-color, so the tag ended up holding the literal
  // text "color-mix(in srgb, #3aa8f0 4%, #070b10)". The computed
  // background of an element resolves it to an rgb() the browser can use.
  // Then normalise it through a canvas, which hands back a plain
  // #rrggbb. Chrome resolves a color-mix() to CSS Color 4 syntax —
  // color(srgb 0.03 0.06 0.09) — and a theme-color is read by phone
  // browser chrome that may not parse that form.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && document.body) {
    const painted = getComputedStyle(document.body).backgroundColor;
    let hex = null;
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.fillStyle = "#000000";
      ctx.fillStyle = painted;   // an unparseable value leaves the last one
      hex = ctx.fillStyle;
    } catch {
      hex = null;                // no canvas (very old or locked-down)
    }
    if (typeof hex === "string" && hex.startsWith("#")) {
      meta.setAttribute("content", hex);
    }
  }
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(t));
  } catch {
    /* private window or blocked storage — the theme still applies */
  }
  return t;
}

export { clean as normalizeTheme };
