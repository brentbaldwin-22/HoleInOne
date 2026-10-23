/**
 * THE SITE THEME, fetched once per page load and shared by everyone.
 *
 * The theme is already applied to <html> before React mounts (from the
 * cached copy, in main.jsx), so this hook is not what makes the page
 * the right colour — the stylesheet does that. It exists for the few
 * components that need to KNOW the choice rather than just wear it:
 * the brand, which has a logo file per direction, and the admin
 * picker, which has to show what is currently set.
 *
 * One fetch serves every subscriber. A component mounting later gets
 * the settled value immediately instead of asking again.
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";
import { applyTheme, readCachedTheme, normalizeTheme } from "../theme.js";

let current = readCachedTheme();
let inflight = null;
let fetched = false;
const listeners = new Set();

function publish(theme) {
  current = applyTheme(theme);
  for (const fn of listeners) fn(current);
}

/** Called by the admin picker once a change is saved. */
export function setSiteTheme(theme) {
  fetched = true;
  publish(normalizeTheme(theme));
}

function loadOnce() {
  if (fetched) return Promise.resolve(current);
  if (!inflight) {
    inflight = api
      .siteTheme()
      .then((t) => {
        fetched = true;
        publish(t);
        return current;
      })
      .catch(() => current) // offline or API down: the cached theme stands
      .finally(() => { inflight = null; });
  }
  return inflight;
}

/**
 * Fetch the theme once at boot, from main.jsx, so a visitor who lands
 * on a page with no theme-aware component still gets the current
 * colours rather than whatever this browser cached last week.
 */
export function ensureSiteTheme() {
  return loadOnce();
}

export default function useSiteTheme() {
  const [theme, setTheme] = useState(current);

  useEffect(() => {
    listeners.add(setTheme);
    loadOnce();
    return () => { listeners.delete(setTheme); };
  }, []);

  return theme;
}
