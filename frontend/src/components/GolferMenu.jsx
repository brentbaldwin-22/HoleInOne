import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import useAuth from "../hooks/useAuth.js";

/**
 * The golfer's way around the app, top left.
 *
 * Everything here was already reachable and most of it only from the
 * home page or the footer: a golfer who scanned a QR code at the first
 * tee landed on a registration form with no route to their own rounds,
 * their gallery history, or the contest they had just entered.
 *
 * FOR GOLFERS, NOT OPERATORS. Brand is also the header of every admin
 * page, and those already carry their own nav across the top; a second
 * menu offering "Register a round" to somebody triaging clips is noise.
 * Hidden on /admin and /operator.
 *
 * There is no leaderboard to link to. The monthly and yearly boards were
 * removed from the product, and Contests is where the prizes and Shot of
 * the Week voting live, so that is what it says.
 */
export default function GolferMenu() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  // Close on navigation: without this the panel survives the click that
  // navigated and hangs over the page it just opened.
  useEffect(() => { setOpen(false); }, [pathname]);

  useEffect(() => {
    if (!open) return undefined;
    function onDown(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (pathname.startsWith("/admin") || pathname.startsWith("/operator")) return null;

  return (
    <div className="gmenu" ref={boxRef}>
      <button
        type="button"
        className="gmenu-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Menu"
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
          <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>

      {open && (
        <div className="gmenu-panel" role="menu">
          {user ? (
            <>
              <div className="gmenu-head">{user.name || user.email}</div>
              <Link to="/me" role="menuitem">My rounds &amp; clips</Link>
              <Link to={`/p/${user.id}`} role="menuitem">My player profile</Link>
            </>
          ) : (
            <>
              <div className="gmenu-head">Not signed in</div>
              <Link to="/login" role="menuitem">Log in</Link>
              <Link to="/signup" role="menuitem">Create an account</Link>
            </>
          )}

          <div className="gmenu-sep" />
          <Link to="/courses" role="menuitem">Register for a round</Link>
          <Link to="/contests" role="menuitem">Contests &amp; prizes</Link>
          <Link to="/watch" role="menuitem">Live broadcast</Link>
          <Link to="/sample" role="menuitem">See a sample gallery</Link>

          <div className="gmenu-sep" />
          <Link to="/legal/faq" role="menuitem">FAQ</Link>
          <Link to="/legal/rules" role="menuitem">Hole-in-one contest rules</Link>
          <a href="mailto:hello@golfreelz.com" role="menuitem">Contact us</a>

          {user && (
            <>
              <div className="gmenu-sep" />
              <button
                type="button"
                className="gmenu-signout"
                role="menuitem"
                onClick={() => { setOpen(false); logout(); }}
              >
                Sign out
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
