/**
 * Choosing which physical camera to open.
 *
 * Its own module so the choice can be tested without a browser: these are
 * pure string decisions over what enumerateDevices() reports, and getting
 * them wrong is exactly how Flip came to toggle the mirroring while the
 * same lens stayed live.
 */

/** Which way a camera points, read off its label. */
export function sideOf(label) {
  const l = (label || "").toLowerCase();
  if (/front|face ?time|user|selfie/.test(l)) return "front";
  if (/back|rear|environment|world/.test(l)) return "back";
  return "unknown";
}

/**
 * The camera to use for a side.
 *
 * Among the back lenses, prefer the ultrawide: this is a head-to-toe
 * photograph taken at arm's length or across a cart path, and the wide
 * lens is the difference between fitting the golfer in and not.
 */
export function pickCamera(cameras, want) {
  const side = cameras.filter((c) => c.side === want);
  if (want === "back") {
    const wide = side.find((c) => /ultra.?wide|0\.?5/i.test(c.label));
    if (wide) return wide;
  }
  if (side.length) return side[0];
  // Nothing is labelled (some browsers withhold labels): fall back to
  // position, where the front camera is conventionally listed first.
  if (!cameras.length) return null;
  return want === "front" ? cameras[0] : cameras[cameras.length - 1];
}
