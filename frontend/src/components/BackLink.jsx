import { useLocation, useNavigate } from "react-router-dom";

/**
 * "← Back", one step through the app's own history.
 *
 * Rendered by Brand, so it lands on every page with a header without
 * thirty-five copies of itself. The three pages with no Brand import it
 * directly.
 *
 * IT HIDES WHEN THERE IS NOWHERE TO GO. React Router keeps its position
 * in the history stack on window.history.state.idx, and at zero this tab
 * has no earlier page of ours -- a QR code scanned at the first tee, a
 * shared profile link opened fresh. navigate(-1) there either does
 * nothing or walks the golfer out of the site, and a button that
 * sometimes does nothing is worse than no button. Home is already one
 * tap away on the logo.
 */
export default function BackLink() {
  const nav = useNavigate();
  const { pathname } = useLocation();

  if (pathname === "/") return null;
  let idx = 0;
  try {
    idx = window.history.state?.idx ?? 0;
  } catch {
    idx = 0;
  }
  if (!idx) return null;

  return (
    <button
      type="button"
      className="ghost small back-link"
      onClick={() => nav(-1)}
      aria-label="Go back to the previous page"
    >
      ← Back
    </button>
  );
}
