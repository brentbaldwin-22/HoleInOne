/**
 * LOGO AND COLOURS — pick a lockup, the bands follow it.
 *
 * This was a picker, then a readout while there was only one lockup to
 * pick. There are four now, so it is a picker again — and the comment
 * on the readout said this would be "an entry here plus a block in the
 * stylesheet, not a re-plumbing", which is what it turned out to be.
 *
 * WHAT CHANGES AND WHAT DOES NOT, because that is the whole design:
 * the six band tokens change, and nothing else. They are the logo's own
 * stripes, so the rule under the masthead, the clip stills and the hero
 * follow the lockup. The type, the buttons, the ink and the linen
 * ground are the same on all four — furniture, not branding.
 * styles.css pins --primary and --warn so a green lockup cannot turn
 * every button green or recolour a warning.
 *
 * ONE THEME, NOT ONE PER ADMIN. Saving changes the site for every
 * visitor immediately, so the card says so rather than letting it be a
 * surprise.
 */
import { useEffect, useState } from "react";

import { api } from "../api.js";
import { DIRECTIONS, directionMeta, logoUrl } from "../theme.js";
import useSiteTheme, { setSiteTheme } from "../hooks/useSiteTheme.js";

export default function AppearanceCard({ adminPassword, onToast }) {
  const theme = useSiteTheme();
  const [served, setServed] = useState(null);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState(null);

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

  const current = theme?.direction || "sunset";

  async function pick(key) {
    if (key === current || saving) return;
    setSaving(key);
    setError(null);
    // Paint it before the round trip. The attribute drives the CSS, so
    // the bands change under the pointer and the save either confirms
    // it or puts it back — which reads as a control rather than a form.
    setSiteTheme({ ...theme, direction: key });
    try {
      const saved = await api.adminSetTheme(adminPassword, { direction: key });
      setServed(saved);
      setSiteTheme(saved);
      onToast?.(`Logo set to ${directionMeta(key).name} — live for everyone.`);
    } catch (e) {
      setSiteTheme({ ...theme, direction: current });
      setError(e?.message || String(e));
    } finally {
      setSaving(null);
    }
  }

  const stale = served && theme && served.direction !== theme.direction
    && saving == null;

  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: "space-between",
                                       width: "100%", flexWrap: "wrap",
                                       gap: 8 }}>
        <h3>Logo and colours</h3>
        <span className="tiny upper muted">Live for everyone</span>
      </div>
      <p className="small muted" style={{ marginTop: 6, marginBottom: 0 }}>
        Pick a lockup. The bands across the site — the rule under the
        masthead, the clip stills, the hero — take its colours. Type,
        buttons and the paper stay the same.
      </p>

      <div className="bands" style={{ marginTop: 16, height: 9 }} />

      <div className="grid" style={{ marginTop: 18 }}>
        {DIRECTIONS.map((d) => {
          const on = d.key === current;
          return (
            <button
              key={d.key}
              type="button"
              onClick={() => pick(d.key)}
              disabled={!!saving}
              aria-pressed={on}
              title={d.note}
              style={{
                // Not .chip: this is a picture to compare, not a word.
                display: "block", width: "100%", textAlign: "left",
                padding: 12, minHeight: 0,
                textTransform: "none", letterSpacing: 0,
                background: on ? "var(--primary-soft)" : "var(--paper)",
                color: "var(--ink)",
                border: `1px solid ${on ? "var(--primary)" : "var(--rule)"}`,
                borderRadius: "var(--radius-lg)",
              }}
            >
              <img
                src={logoUrl(d.key)}
                alt=""
                style={{ width: "100%", height: "auto", display: "block" }}
              />
              <div className="inline" style={{ marginTop: 10, gap: 8 }}>
                <span className="name">{d.name}</span>
                {on && <span className="pill tiny live">in use</span>}
                {saving === d.key && (
                  <span className="tiny muted">saving…</span>
                )}
              </div>
              <div className="tiny muted" style={{ marginTop: 2,
                                                   whiteSpace: "normal" }}>
                {d.note}
              </div>
            </button>
          );
        })}
      </div>

      {error && (
        <div className="err-text small" style={{ marginTop: 14 }}>
          Could not save: {error}
        </div>
      )}

      {stale && (
        <div className="warn-text small" style={{ marginTop: 14 }}>
          This browser had <code>{theme.direction}</code> cached while the
          server has <code>{served.direction}</code>. Reload to clear it.
        </div>
      )}
    </div>
  );
}
