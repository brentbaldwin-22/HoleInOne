/**
 * WHAT THE SITE IS WEARING.
 *
 * This was a picker — a colourway and a dark/light switch, saved
 * against the site and worn by every visitor. There is one look now,
 * built from the mark, so there is nothing left to choose, and a
 * control offering a choice that changes nothing is worse than no
 * control at all.
 *
 * It stays as a READOUT rather than being deleted, because one
 * question outlived the choice: is this browser showing what the
 * server actually has? The theme is cached in localStorage so the
 * first paint is not a flash of the wrong colours, and a stale cache
 * used to be invisible. Now it says so.
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";
import { directionMeta, logoUrl } from "../theme.js";
import useSiteTheme, { setSiteTheme } from "../hooks/useSiteTheme.js";

const BANDS = [
  ["#0047e7", "band 1 — primary"],
  ["#01acfd", "band 2"],
  ["#76d0c9", "band 3"],
  ["#fcdc5e", "band 4"],
  ["#fea931", "band 5"],
  ["#fe6610", "band 6"],
  ["#f54304", "accent — the red REELZ is set in"],
];

export default function AppearanceCard({ adminPassword }) {
  const theme = useSiteTheme();
  const [served, setServed] = useState(null);

  // The server is the authority; the hook may still be showing this
  // browser's cached copy from before a deploy.
  useEffect(() => {
    if (!adminPassword) return;
    let alive = true;
    api.adminTheme(adminPassword)
      .then((t) => { if (alive) { setServed(t); setSiteTheme(t); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [adminPassword]);

  const meta = directionMeta(theme?.direction || "linen");
  const stale = served && theme && served.direction !== theme.direction;

  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: "space-between",
                                       width: "100%", flexWrap: "wrap",
                                       gap: 8 }}>
        <h3>Site appearance</h3>
        <span className="tiny upper muted">Live for everyone</span>
      </div>
      <p className="small muted" style={{ marginTop: 6, marginBottom: 0 }}>
        One look, built from the logo — the same colours and type on the
        home page, the registration flow, the galleries and this console.
      </p>

      <div className="bands" style={{ marginTop: 18, height: 9 }} />

      <div className="row" style={{ marginTop: 18, alignItems: "center" }}>
        <img
          src={logoUrl(meta.key)}
          alt=""
          style={{ width: 150, height: "auto", display: "block" }}
        />
        <div className="small" style={{ flex: "999 1 240px", minWidth: 0 }}>
          <div className="name">{meta.name}</div>
          <div className="muted">{meta.note}</div>
        </div>
      </div>

      <div className="chip-row" style={{ marginTop: 18 }}>
        {BANDS.map(([hex, label]) => (
          <span key={hex} className="inline tiny muted" title={label}>
            <span
              className="dot"
              style={{ background: hex, borderRadius: 4,
                       width: 14, height: 14 }}
            />
            <code>{hex}</code>
          </span>
        ))}
      </div>

      {stale && (
        <div className="warn-text small" style={{ marginTop: 14 }}>
          This browser had <code>{theme.direction}</code> cached while the
          server has <code>{served.direction}</code>. Reload to clear it.
        </div>
      )}
    </div>
  );
}
