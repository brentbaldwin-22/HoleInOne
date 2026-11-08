/**
 * Mobile numbers, punctuated as they are typed.
 *
 * Its own module so the rules can be tested without a browser: this runs
 * on every keystroke, and the way it is got wrong is backspace -- strip
 * a dash the formatter puts back and the caret fights the field.
 * Formatting the DIGITS rather than patching the string avoids that: a
 * backspace removes a digit, the digits are re-punctuated from scratch,
 * and the dash simply is not there any more.
 */

/**
 * A US mobile as 469-847-9270, growing as the digits arrive.
 *
 * Anything that is not plainly a ten-digit US number is passed through
 * untouched -- a leading + is somebody typing an international number,
 * and more than ten digits is a number this format does not describe.
 * Mangling those would be worse than leaving them plain.
 */
export function formatUsPhone(raw) {
  const s = raw == null ? "" : String(raw);
  if (s.trim().startsWith("+")) return s;
  const d = s.replace(/\D/g, "");
  if (d.length > 10) return s;
  if (d.length <= 3) return d;
  if (d.length <= 6) return `${d.slice(0, 3)}-${d.slice(3)}`;
  return `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6)}`;
}
