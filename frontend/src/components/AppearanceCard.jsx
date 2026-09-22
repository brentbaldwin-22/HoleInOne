/**
 * THE SITE'S COLOURS, changed without a deploy.
 *
 * Two choices — a direction and dark or light — saved against the site
 * and worn by every page for every visitor, not just this browser. So
 * the preview is the admin console itself: the choice is applied here
 * the moment it saves, which is also the honest preview, because the
 * console is built out of the same tokens as the public pages.
 *
 * Each direction ships its own logo colourway; picking one changes the
 * mark in the header too.
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";
import { DIRECTIONS, MODES, logoUrl } from "../theme.js";
import useSiteTheme, { setSiteTheme } from "../hooks/useSiteTheme.js";

export default function AppearanceCard({ adminPassword, onToast }) {
  const theme = useSiteTheme();
  const [saving, setSaving] = useState(null);
  const [err, setErr] = useState(null);

  // The server is the authority on what is currently set; the hook may
  // still be showing this browser's cached copy.
  useEffect(() => {
    if (!adminPassword) return;
    api.adminTheme(adminPassword).then(setSiteTheme).catch(() => {});
  }, [adminPassword]);

  async function save(patch) {
    setSaving(Object.values(patch)[0]);
    setErr(null);
    try {
      const out = await api.setAdminTheme(adminPassword, patch);
      setSiteTheme(out);
      onToast?.("Site colours updated — every visitor sees this now");
    } catch (e) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: "space-between",
                                       width: "100%", flexWrap: "wrap",
                                       gap: 8 }}>
        <h3>Site appearance</h3>
        <span className="tiny upper muted">Live for everyone</span>
      </div>
      <p className="small muted" style={{ marginTop: 6, marginBottom: 4 }}>
        Changes the colours and the logo across the whole site — the
        home page, the registration flow, the galleries and this
        console. Saved against the site, so it follows every visitor.
      </p>

      <div className="tiny upper muted" style={{ marginTop: 14 }}>Direction</div>
      <div className="theme-choice-row">
        {DIRECTIONS.map((d) => (
          <button
            key={d.key}
            type="button"
            className="theme-choice"
            aria-pressed={theme.direction === d.key}
            disabled={saving !== null}
            onClick={() => save({ direction: d.key })}
            style={{ "--swatch-a": d.bands[0], "--swatch-b": d.bands[1] }}
          >
            <span className="bands" aria-hidden="true" />
            <span>
              <span className="name">
                {d.name}{saving === d.key ? " — saving…" : ""}
              </span>
              <span className="note" style={{ display: "block" }}>{d.note}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="tiny upper muted" style={{ marginTop: 16 }}>Ground</div>
      <div className="theme-choice-row">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            className="theme-choice"
            aria-pressed={theme.mode === m.key}
            disabled={saving !== null}
            onClick={() => save({ mode: m.key })}
            style={{
              "--swatch-a": m.key === "dark" ? "#0b1119" : "#ffffff",
              "--swatch-b": m.key === "dark" ? "#1c2836" : "#dae3ed",
            }}
          >
            <span className="bands" aria-hidden="true" />
            <span>
              <span className="name">
                {m.name}{saving === m.key ? " — saving…" : ""}
              </span>
              <span className="note" style={{ display: "block" }}>
                {m.key === "dark"
                  ? "The logo is drawn on black, so dark hides its edge."
                  : "Puts the logo on a plate — lighter, more conventional."}
              </span>
            </span>
          </button>
        ))}
      </div>

      <div className="inline" style={{ gap: 12, marginTop: 16 }}>
        <img
          src={logoUrl(theme.direction, "mark")}
          alt=""
          style={{ height: 40, width: "auto", display: "block" }}
        />
        <span className="small muted">
          The mark this direction ships, as it appears in the header.
        </span>
      </div>

      {err && <p className="err-text small" style={{ marginTop: 10 }}>{err}</p>}
    </div>
  );
}
