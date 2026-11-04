import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

/** Fired by the identify page when it pairs or unpairs, so the badge follows. */
export const IDENTIFY_CHANGED = "golfreelz:identify-changed";

/** Today on the OPERATOR's clock, as a [start, end) of UTC instants. */
function todayWindow() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

/**
 * The Player ID link, carrying today's count of golfers still waiting.
 *
 * A COMPONENT RATHER THAN A SHARED NAV. The obvious move was to lift the
 * whole operator nav into one place, but the eighteen copies are not the
 * same nav: Admin.jsx carries eleven links, Claims and Reviews carry
 * five, the rest nine. Replacing them wholesale would have quietly
 * rewritten three pages' navigation to fix a badge. So only the link
 * that needs the number knows how to fetch it, and every nav keeps its
 * own shape.
 *
 * Red when there is something waiting, green when there is not. The
 * number is the SUM OF THE DROPDOWN on the identify page -- golfers who
 * have not been given their shots yet. It counted clips at first, which
 * read as 12 on the nav while every course in the dropdown said clear;
 * one question should not have two answers, so the badge now adds up the
 * same figure rather than measuring its own.
 */
export default function PlayerIdLink({ active = false }) {
  const adminPassword =
    localStorage.getItem(ADMIN_PW_STORAGE) ||
    localStorage.getItem(LEGACY_ADMIN_PW_STORAGE) ||
    "";
  const [count, setCount] = useState(null);

  useEffect(() => {
    if (!adminPassword) return undefined;
    let alive = true;
    async function read() {
      try {
        const r = await api.identifyPending(adminPassword, todayWindow());
        if (alive) setCount(r.golfers);
      } catch {
        // A badge is not worth an error on every admin page: if the
        // count cannot be read it simply does not appear.
        if (alive) setCount(null);
      }
    }
    read();
    window.addEventListener(IDENTIFY_CHANGED, read);
    return () => { alive = false; window.removeEventListener(IDENTIFY_CHANGED, read); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Link to="/admin/identify" className={active ? "active" : undefined}>
      Player ID
      {count !== null && (
        <span className={`nav-badge${count > 0 ? " waiting" : " clear"}`}>
          {count}
        </span>
      )}
    </Link>
  );
}
