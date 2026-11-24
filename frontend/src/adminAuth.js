/*
 * WHERE THE OPERATOR'S PASSWORD IS KEPT, in one place.
 *
 * THE BUG THIS EXISTS FOR. The Production Flow page read
 * localStorage["adminPassword"] -- a key nothing in the application
 * has ever written. It was signed out forever: the operator signed in
 * on the Dashboard, every other page saw it, and this one showed
 * "Admin password required" and a Sign in button that led back to the
 * Dashboard they were already signed into. A loop with no error in it
 * anywhere.
 *
 * Two keys, because the storage key was renamed and a signed-in
 * operator must not be signed out by a deploy. The legacy one is read
 * and never written.
 *
 * Every admin page should read through here rather than spelling the
 * key out, which is what made a third spelling possible.
 */
export const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
export const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

/** The stored operator password, or "" when there is none. */
export function readAdminPassword() {
  try {
    return localStorage.getItem(ADMIN_PW_STORAGE)
      || localStorage.getItem(LEGACY_ADMIN_PW_STORAGE)
      || "";
  } catch {
    // Private windows and blocked site data throw rather than return
    // null. A console that crashes on read is worse than one that
    // asks for the password again.
    return "";
  }
}
