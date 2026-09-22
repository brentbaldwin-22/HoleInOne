/**
 * THE SITE'S COLOURS, chosen once in /admin and worn by every page.
 *
 * Two attributes on <html> carry the whole thing:
 *
 *     data-mode="dark" | "light"
 *     data-direction="broadcast" | "dusk" | "turf"
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

export const DEFAULT_THEME = { direction: "broadcast", mode: "dark" };

// Keep in step with the [data-direction] blocks in styles.css and with
// DIRECTIONS in backend/app/services/site_theme.py.
export const DIRECTIONS = [
  {
    key: "broadcast",
    name: "Broadcast",
    logo: "/logos/broadcast.png",
    mark: "/logos/broadcast-mark.png",
    bands: ["#0a63b8", "#a9e4ff"],
    note: "Blue and cyan. Reads as a camera system — the TV-truck look.",
  },
  {
    key: "dusk",
    name: "Dusk",
    logo: "/logos/dusk.png",
    mark: "/logos/dusk-mark.png",
    bands: ["#e01f14", "#ffd36a"],
    note: "Sunset red into amber. The last tee time of the day.",
  },
  {
    key: "turf",
    name: "Turf",
    logo: "/logos/turf.png",
    mark: "/logos/turf-mark.png",
    bands: ["#0c7a3e", "#e6f5a3"],
    note: "Fairway green into lime. The most golf-course of the three.",
  },
];

export const MODES = [
  { key: "dark", name: "Dark" },
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
    direction: DIRECTION_KEYS.includes(t.direction)
      ? t.direction : DEFAULT_THEME.direction,
    mode: t.mode === "light" ? "light" : "dark",
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
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute(
      "content",
      getComputedStyle(root).getPropertyValue("--bg").trim() || "#0b1017",
    );
  }
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(t));
  } catch {
    /* private window or blocked storage — the theme still applies */
  }
  return t;
}

export { clean as normalizeTheme };
