import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, API_BASE } from "../api.js";
import { Brand, Icon } from "../components/Brand.jsx";
import useSiteTheme from "../hooks/useSiteTheme.js";
import { logoUrl } from "../theme.js";
import AppearanceCard from "../components/AppearanceCard.jsx";
import { fmtDateTime } from "../time.js";

const ADMIN_PW_STORAGE = "golfreelz.adminPassword";
const LEGACY_ADMIN_PW_STORAGE = "parone.adminPassword";

function readStoredPassword() {
  const v = localStorage.getItem(ADMIN_PW_STORAGE);
  if (v) return v;
  const legacy = localStorage.getItem(LEGACY_ADMIN_PW_STORAGE);
  if (legacy) {
    localStorage.setItem(ADMIN_PW_STORAGE, legacy);
    localStorage.removeItem(LEGACY_ADMIN_PW_STORAGE);
    return legacy;
  }
  return "";
}

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function Admin() {
  const theme = useSiteTheme();
  const [adminPassword, setAdminPassword] = useState(() => readStoredPassword());
  // TRUSTED UNTIL THE SERVER SAYS OTHERWISE. This started false, so
  // every page load began signed out and only the first successful
  // fetch let you in — which meant the backend being slow to wake put
  // you back at the password box with a perfectly good password still
  // in localStorage. Starting from "is a password stored" renders the
  // console immediately and lets a 401 be the only thing that signs
  // you out. Nothing is exposed by guessing: every request still
  // carries the password and the server still checks it.
  const [authed, setAuthed] = useState(() => !!readStoredPassword());
  const [courses, setCourses] = useState([]);
  const [stats, setStats] = useState(null);
  const [disk, setDisk] = useState(null);
  const [flagged, setFlagged] = useState([]);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  async function load(key = adminPassword) {
    try {
      const [c, s, f] = await Promise.all([api.listCourses(key), api.stats(key), api.flaggedClips(key)]);
      setCourses(c); setStats(s); setFlagged(f);
      // SEPARATE, AND ALLOWED TO FAIL. The disk readout walks the
      // clips directory, so it is the slowest call on this page and
      // the most likely to time out on the very deployment whose disk
      // is in trouble. Joining it to the Promise.all above would mean
      // a struggling server showed no dashboard at all.
      api.storage(key).then(setDisk).catch(() => setDisk(null));
      setAuthed(true);
      localStorage.setItem(ADMIN_PW_STORAGE, key);
      setError(null);
    } catch (e) {
      const msg = String(e?.message || e);
      setError(msg);
      // ONLY A 401 MEANS THE PASSWORD IS WRONG. A cold-starting
      // backend, a timeout, a 502 — all of them used to land here and
      // sign you out, which is what "it keeps logging me out" was. The
      // stored password is cleared on a real rejection so a bad one
      // cannot loop, and kept on everything else so a blip costs a
      // retry rather than a login.
      if (/^401\b/.test(msg)) {
        localStorage.removeItem(ADMIN_PW_STORAGE);
        setAuthed(false);
      }
    }
  }

  useEffect(() => { if (adminPassword) load(adminPassword); /* eslint-disable-next-line */ }, []);

  function showToast(msg) { setToast(msg); setTimeout(() => setToast(null), 2500); }

  // Shown while signed in: the console is usable, this one fetch is not.
  const loadError = authed && error ? (
    <div className="card warn-text small" style={{ display: "flex", gap: 12,
                                                   alignItems: "center",
                                                   flexWrap: "wrap" }}>
      <span style={{ flex: "999 1 240px", minWidth: 0 }}>
        Could not refresh the dashboard: {error}
      </span>
      <button className="small secondary" style={{ width: "auto" }}
              onClick={() => load()}>Retry</button>
    </div>
  ) : null;

  if (!authed) {
    return (
      <div className="wrap">
        <Brand subtitle="Operator Console" />
        <div className="card" style={{ maxWidth: 420, margin: "40px auto 0" }}>
          <h2 style={{ marginBottom: 4 }}>Sign in</h2>
          <p className="small muted" style={{ marginBottom: 18 }}>Admin password required.</p>
          <div className="field">
            <label>Password</label>
            <input
              type="password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              placeholder="Enter admin password"
              onKeyDown={(e) => e.key === "Enter" && load(adminPassword)}
              autoFocus
            />
          </div>
          {error && <p className="err-text small">{error}</p>}
          <button onClick={() => load(adminPassword)}>Sign in</button>
        </div>
      </div>
    );
  }

  return (
    <div className="wrap wide">
      <div className="brand" style={{ justifyContent: "space-between", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          {/* Same lockup the public header wears, through the same
              lookup — this used to hard-code /golfreelz-logo.png in a
              60px box clipping a 100px image, which was a crop built
              around the old banner's whitespace and would have cut the
              top and bottom off the current mark. */}
          <img
            src={logoUrl(theme.direction)}
            alt="GolfReelz"
            style={{ height: 62, width: "auto", display: "block" }}
          />
          <div className="tag">Operator Console</div>
        </div>
      </div>

      <div className="nav">
        <Link to="/admin" className="active">Dashboard</Link>
        <Link to="/admin/participants">Players</Link>
        <Link to="/admin/claims">Claims</Link>
        <Link to="/admin/reviews">Reviews</Link>
        <Link to="/admin/courses">Courses</Link>
        <Link to="/admin/upload-videos">Upload</Link>
        <Link to="/admin/production">Production</Link>
        <Link to="/admin/produced-clips">Produced Clips</Link>
        <Link to="/admin/broadcast-clips">Broadcast</Link>
        <Link to="/admin/cameras">Cameras</Link>
        <button
          className="ghost"
          onClick={() => { localStorage.removeItem(ADMIN_PW_STORAGE); window.location.reload(); }}
          style={{ marginLeft: "auto" }}
        >
          Sign out
        </button>
      </div>

      {loadError}

      {stats && (
        <div className="stat-grid">
          <Link to="/admin/participants" className="stat" style={{ textDecoration: "none", color: "inherit" }}>
            <div className="icon-bg"><Icon name="users" size={16} /></div>
            <div className="label">Players</div>
            <div className="value">{stats.participants.total}</div>
            <div className="sub">
              <Link to={`/admin/participants?date=${today()}`}>Today {stats.participants.day}</Link>
              {" · "}
              <Link to={`/admin/participants`}>Week {stats.participants.week}</Link>
              {" · Month "}{stats.participants.month}
            </div>
          </Link>
          <div className="stat">
            <div className="icon-bg"><Icon name="dollar" size={16} /></div>
            <div className="label">Revenue</div>
            <div className="value">${(stats.revenue_cents / 100).toFixed(2)}</div>
            <div className="sub">Lifetime gross</div>
          </div>
          <div className="stat">
            <div className="icon-bg"><Icon name="chart" size={16} /></div>
            <div className="label">Clips</div>
            <div className="value">
              {Object.values(stats.clips_by_status).reduce((a, b) => a + b, 0) || 0}
            </div>
            <div className="sub">
              {Object.entries(stats.clips_by_status).length === 0
                ? "No clips yet"
                : Object.entries(stats.clips_by_status).map(([k, v]) => `${k}:${v}`).join("  ·  ")}
            </div>
          </div>
          {disk && (
            <div className="stat">
              <div className="icon-bg"><Icon name="chart" size={16} /></div>
              <div className="label">Disk</div>
              <div
                className="value"
                // A FULL DISK IS NOT A STATISTIC, it is an outage: every
                // ffmpeg write fails, previews stop rendering, frames
                // stop extracting, and nothing in the UI says "disk".
                // So this tile shouts before it is too late to act,
                // rather than reporting a number nobody reads.
                style={{ color: disk.disk_used_pct >= 90 ? "#dc2626"
                  : disk.disk_used_pct >= 75 ? "#d97706" : undefined }}
              >
                {disk.disk_used_pct}%
              </div>
              <div className="sub">
                {disk.disk_free_gb} GB free of {disk.disk_total_gb}
                {disk.disk_used_pct >= 90 && (
                  <><br /><b>Full. Produce and previews will fail.</b></>
                )}
                {disk.by_category?.raw_video && (
                  <><br />raw footage {disk.by_category.raw_video.gb} GB
                  {" "}in {disk.by_category.raw_video.files} files,
                  {" "}kept {disk.retention_days} days</>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {stats?.by_course?.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 10 }}>Players by course</h3>
          <div className="chip-row">
            {stats.by_course.map((row) => {
              const course = courses.find((c) => c.name === row.course);
              return (
                <Link
                  key={row.course}
                  to={course ? `/admin/participants?course_id=${course.id}` : "/admin/participants"}
                  className="chip"
                >
                  {row.course} <b style={{ marginLeft: 6 }}>{row.participants}</b>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      <RevenueCard adminPassword={adminPassword} courses={courses} onToast={showToast} />

      <TestEmailCard adminPassword={adminPassword} onToast={showToast} />

      <AppearanceCard adminPassword={adminPassword} onToast={showToast} />

      <div className="card">
        <h3 style={{ marginBottom: 4 }}>Manual review queue</h3>
        <p className="small muted" style={{ marginBottom: 14 }}>
          Clips the auto-matcher couldn't assign with confidence. Tap a candidate to assign.
        </p>
        {flagged.length === 0 && <div className="muted small">Nothing flagged. ✨</div>}
        {flagged.map((c) => (
          <div key={c.id} className="clip" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <div className="thumb" style={c.thumbnail_url ? { backgroundImage: `url(${c.thumbnail_url})` } : {}} />
            <div className="meta">
              <div className="inline" style={{ gap: 8 }}>
                <b>Hole {c.hole_number}</b>
                <span className="pill dark" style={{ textTransform: "lowercase" }}>{c.camera_type.replace("_", " ")}</span>
                <span className={`pill ${c.status === "flagged" ? "err" : "warn"}`}>{c.status}</span>
              </div>
              <div className="small muted" style={{ marginTop: 4 }}>
                {fmtDateTime(c.captured_at)}{c.note ? ` · ${c.note}` : ""}
              </div>
              {c.candidates?.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div className="tiny upper muted" style={{ marginBottom: 6 }}>Candidates in window</div>
                  <div className="chip-row">
                    {c.candidates.map((cand) => (
                      <button
                        key={cand.id}
                        type="button"
                        className="chip"
                        title={`Assign to ${cand.name}`}
                        onClick={async () => {
                          try {
                            await fetch(
                              `${API_BASE}/api/admin/clips/${c.id}/assign?participant_id=${cand.id}`,
                              { method: "POST", headers: { "X-Admin-Password": adminPassword } },
                            );
                            showToast(`Assigned to ${cand.name}`);
                            load();
                          } catch (e) {
                            showToast(`Error: ${e.message}`);
                          }
                        }}
                      >
                        {cand.selfie_url && (
                          <img src={cand.selfie_url} alt="" style={{ width: 22, height: 22, borderRadius: "50%", objectFit: "cover" }} />
                        )}
                        {cand.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

const money = (cents) =>
  `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Revenue per course, and the download of it.
 *
 * The figures on screen and the figures in the .xlsx come from one
 * endpoint pair backed by one function, so there is no version of this
 * where the dashboard and the spreadsheet a course gets sent disagree.
 *
 * "Paid" here means paid AND not refunded. The Revenue tile at the top
 * of this page counts `paid` alone, so the two can differ by however
 * many refunds there have been — that is the tile being loose, not this
 * being wrong.
 */
function RevenueCard({ adminPassword, courses, onToast }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [courseId, setCourseId] = useState("");
  const [rep, setRep] = useState(null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState(null);

  const params = { start, end, courseId: courseId || undefined };

  async function load() {
    if (!adminPassword) return;
    setBusy(true);
    setError(null);
    try {
      setRep(await api.revenueReport(adminPassword, params));
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  // First paint shows all-time, so the card is never an empty form.
  useEffect(() => {
    if (adminPassword) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminPassword]);

  async function download() {
    setDownloading(true);
    setError(null);
    try {
      const { blob, filename } = await api.revenueWorkbook(adminPassword, params);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoking immediately races the download in Safari; a tick is enough.
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      onToast?.(`Downloaded ${filename}`);
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setDownloading(false);
    }
  }

  const t = rep?.totals;

  return (
    <div className="card">
      <h3 style={{ marginBottom: 4 }}>Revenue by course</h3>
      <p className="small muted" style={{ marginBottom: 14 }}>
        {rep
          ? `${rep.period} · $${(rep.price_cents / 100).toFixed(2)} an entry · we keep ${rep.golfreelz_share_pct}%, the course takes ${rep.course_share_pct}%.`
          : "Signups and the split, per course."}
      </p>

      <div className="chip-row" style={{ alignItems: "flex-end", gap: 10, marginBottom: 14 }}>
        <label className="small">
          <div className="muted">From</div>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="small">
          <div className="muted">To</div>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="small">
          <div className="muted">Course</div>
          <select value={courseId} onChange={(e) => setCourseId(e.target.value)}>
            <option value="">All courses</option>
            {(courses || []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        <button className="btn secondary" onClick={load} disabled={busy}>
          {busy ? "Loading…" : "Apply"}
        </button>
        <button className="btn" onClick={download} disabled={downloading || !rep}>
          {downloading ? "Building…" : "Download Excel"}
        </button>
      </div>

      {error && <div className="small" style={{ color: "var(--danger)", marginBottom: 10 }}>{error}</div>}

      {rep && rep.by_course.length === 0 && (
        <div className="muted small">No signups in this period.</div>
      )}

      {rep && rep.by_course.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Course</th>
              <th>Signups</th>
              <th>Paid</th>
              <th>Gross</th>
              <th>Golf Reelz ({rep.golfreelz_share_pct}%)</th>
              <th>Course ({rep.course_share_pct}%)</th>
            </tr>
          </thead>
          <tbody>
            {rep.by_course.map((r) => (
              <tr key={r.course_id}>
                <td>
                  <b>{r.course}</b>
                  <div className="small muted">{r.location || "—"}</div>
                </td>
                <td>{r.signups}</td>
                <td>
                  {r.paid}
                  {(r.refunded > 0 || r.unpaid > 0) && (
                    <div className="small muted">
                      {[r.refunded > 0 && `${r.refunded} refunded`, r.unpaid > 0 && `${r.unpaid} unpaid`]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  )}
                </td>
                <td>{money(r.gross_cents)}</td>
                <td><b>{money(r.golfreelz_cents)}</b></td>
                <td>{money(r.course_cents)}</td>
              </tr>
            ))}
            {t && (
              <tr>
                <td><b>TOTAL</b></td>
                <td><b>{t.signups}</b></td>
                <td><b>{t.paid}</b></td>
                <td><b>{money(t.gross_cents)}</b></td>
                <td><b>{money(t.golfreelz_cents)}</b></td>
                <td><b>{money(t.course_cents)}</b></td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TestEmailCard({ adminPassword, onToast }) {
  const [to, setTo] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState(null);
  const [tpl, setTpl] = useState(null);
  const [mail, setMail] = useState(null);

  // Transport status on mount, so "not configured" is visible before
  // anyone wonders why their inbox is empty.
  useEffect(() => {
    if (!adminPassword) return;
    api.emailStatus(adminPassword).then(setMail).catch(() => setMail(null));
  }, [adminPassword]);

  async function sendTemplates() {
    setSending(true);
    setTpl(null);
    setResult(null);
    try {
      const r = await api.emailSendTemplates(adminPassword, to);
      setTpl(r);
      onToast?.(
        r.transport === "mock"
          ? "Mock mode — nothing delivered"
          : `Sent ${(r.results || []).filter((x) => x.ok).length} template(s)`,
      );
    } catch (e) {
      setTpl({ results: [], note: e.message });
    } finally {
      setSending(false);
    }
  }

  async function send() {
    setSending(true);
    setResult(null);
    try {
      const r = await api.sendTestEmail(adminPassword, { to });
      setResult({ ok: true, provider: r.provider, to: r.to });
      onToast?.(`Test sent via ${r.provider}`);
    } catch (e) {
      setResult({ ok: false, error: e.message });
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginBottom: 6 }}>Email delivery</h3>
      {/* The transport, stated up front. The failure mode here is silent —
          with no credentials every send "succeeds" and only writes a log
          line — so it should not take a log dive to notice. */}
      {mail && (
        <div
          className="small"
          style={{
            marginBottom: 10, padding: "8px 10px", borderRadius: 6,
            background: mail.transport === "mock"
              ? "rgba(192,57,43,0.08)" : "var(--primary-soft)",
            border: `1px solid ${mail.transport === "mock"
              ? "rgba(192,57,43,0.35)" : "var(--emerald-200)"}`,
          }}
        >
          <b style={{
            color: mail.transport === "mock" ? "#c0392b" : "var(--emerald-800)",
          }}>
            {mail.transport === "mock"
              ? "Not configured — nothing is being sent"
              : `Sending via ${mail.transport}`}
          </b>
          <div className="muted">{mail.detail}</div>
          {mail.from && <div className="muted">from: {mail.from}</div>}
          {mail.logo_kb != null && (
            <div className="muted">footer logo: {mail.logo_kb} KB inline</div>
          )}
          {mail.missing?.length > 0 && (
            <div style={{ marginTop: 4 }}>
              set in Secrets: <b>{mail.missing.join(", ")}</b>
            </div>
          )}
        </div>
      )}
      <p className="small muted" style={{ marginBottom: 10 }}>
        <b>Send test</b> drops one generic message to check the wiring.{" "}
        <b>Send all templates</b> fires every email a golfer can get —
        registration, gallery ready, the thank-you, all four win emails
        with their prize-claim links, and the two we hope not to send: a
        refund and a no-clips apology.
      </p>
      <div className="row">
        <div className="field" style={{ flex: 2 }}>
          <label>Send test to</label>
          <input
            type="email"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="you@example.com"
          />
        </div>
        <div style={{ alignSelf: "end", display: "flex", gap: 8 }}>
          <button type="button" className="secondary"
            disabled={sending || !to} onClick={send}
            style={{ width: "auto" }}>
            {sending ? "Sending…" : "Send test"}
          </button>
          <button type="button" disabled={sending || !to}
            onClick={sendTemplates} style={{ width: "auto" }}>
            {sending ? "Sending…" : "Send all templates"}
          </button>
        </div>
      </div>
      {result?.ok && (
        <p className="small" style={{ color: "var(--emerald-700)" }}>
          ✓ Sent via <b>{result.provider}</b> to {result.to}.
          {result.provider === "mock" && " (Set SMTP secrets to actually deliver.)"}
        </p>
      )}
      {result && !result.ok && <p className="err-text small">{result.error}</p>}
      {tpl && (
        <div className="small" style={{ marginTop: 8 }}>
          {tpl.note && (
            <div style={{ color: "#c0392b", marginBottom: 4 }}>{tpl.note}</div>
          )}
          {(tpl.results || []).map((r) => (
            <div key={r.template}
              style={{ color: r.ok ? "var(--emerald-700)" : "#c0392b" }}>
              {r.ok ? "✓" : "✕"} {r.template}
              {r.error ? ` — ${r.error}` : ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
