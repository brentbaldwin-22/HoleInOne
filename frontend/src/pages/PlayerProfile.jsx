import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";
import { Brand, Icon } from "../components/Brand.jsx";

function StatTile({ label, value, unit }) {
  return (
    <div className="stat" style={{ textAlign: "center" }}>
      <div className="label">{label}</div>
      <div className="value">{value ?? "—"}</div>
      {value != null && unit && <div className="sub">{unit}</div>}
    </div>
  );
}

export default function PlayerProfile() {
  const { userId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  // -1 = not playing. Otherwise the index of the clip on screen.
  const [reelAt, setReelAt] = useState(-1);

  useEffect(() => {
    api.publicProfile(userId).then(setData).catch((e) => setError(e.message));
  }, [userId]);

  function share() {
    const url = `${window.location.origin}/p/${userId}`;
    if (navigator.share) {
      navigator.share({ title: `${data?.display_name || "GolfReelz player"} on GolfReelz`, url }).catch(() => {});
    } else {
      navigator.clipboard?.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }
  }

  if (error) {
    return (
      <div className="wrap">
        <Brand />
        <div className="card"><h2>Player not found</h2><p className="small muted">{error}</p></div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="wrap">
        <Brand />
        <div className="card"><div className="shimmer" style={{ height: 200 }} /></div>
      </div>
    );
  }

  const s = data.stats;

  return (
    <div className="wrap">
      <Brand />

      <div className="hero" style={{ padding: "32px 24px" }}>
        <span className="eyebrow"><Icon name="users" size={14} /> Player profile</span>
        <h1 style={{ fontSize: "clamp(1.8rem, 3.6vw, 2.4rem)" }}>{data.display_name}</h1>
        <p>
          {s.total_rounds} round{s.total_rounds === 1 ? "" : "s"}
          {s.total_aces > 0 && ` · ${s.total_aces} hole-in-one${s.total_aces === 1 ? "" : "s"}`}
          {s.courses_played.length > 0 && ` · ${s.courses_played.length} course${s.courses_played.length === 1 ? "" : "s"}`}
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
          <button onClick={share} className="btn small" style={{ width: "auto" }}>
            <Icon name="share" size={14} /> {copied ? "Copied!" : "Share profile"}
          </button>
        </div>
      </div>

      <div className="stat-grid">
        <StatTile label="Rounds played" value={s.total_rounds} />
        <StatTile label="Hole-in-ones" value={s.total_aces} />
        <StatTile label="Closest to pin" value={s.closest_ctp_feet} unit="ft" />
      </div>

      {s.courses_played.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 10 }}>Courses played</h3>
          <div className="chip-row">
            {s.courses_played.map((c) => (
              <span key={c} className="chip" style={{ cursor: "default" }}>{c}</span>
            ))}
          </div>
        </div>
      )}

      {data.highlights.length > 0 && (
        <div className="card">
          <div className="inline" style={{ justifyContent: "space-between", width: "100%", marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Highlight reel</h3>
            <div className="inline" style={{ gap: 12 }}>
              <span className="small muted">
                {data.highlights.length} closest shot{data.highlights.length === 1 ? "" : "s"}
              </span>
              <button type="button" onClick={() => setReelAt(0)}>
                Play all
              </button>
            </div>
          </div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {data.highlights.map((h) => (
              <div key={h.clip_id}>
                <video
                  src={h.source_url}
                  poster={h.thumbnail_url || undefined}
                  controls
                  playsInline
                  preload="metadata"
                  style={{ width: "100%", aspectRatio: "16/9", borderRadius: 8, background: "#000", display: "block" }}
                />
                <div className="small" style={{ marginTop: 6 }}>
                  <b>{h.course}</b> · Hole {h.hole}{" "}
                  <span className={`pill ${h.ball_in_cup ? "ok" : "info"}`} style={{ marginLeft: 6 }}>{h.tag}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* THE REEL, BACK TO BACK. One <video> that advances on `ended`,
          which is how Watch.jsx plays the broadcast -- no stitched file,
          so nothing to encode, nothing to store on a disk that is
          already filling, and a clip re-rendered tomorrow is in the reel
          tonight. The `key` forces React to rebuild the element on each
          change so the new src actually loads and autoplays, rather than
          the browser holding the old buffered stream. */}
      {reelAt >= 0 && data.highlights[reelAt] && createPortal(
        <div className="clip-watch" onClick={() => setReelAt(-1)}>
          <video
            key={data.highlights[reelAt].clip_id}
            src={data.highlights[reelAt].source_url}
            poster={data.highlights[reelAt].thumbnail_url || undefined}
            controls
            autoPlay
            playsInline
            onEnded={() => setReelAt((i) => (i + 1 < data.highlights.length ? i + 1 : -1))}
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: "min(100%, 900px)", maxHeight: "72vh" }}
          />
          {/* The backdrop closes the reel, and these sit on top of it.
              Without this, Next both advanced AND bubbled up to the
              close handler -- which ran second, so every press shut the
              player. Close still works: it sets -1 itself. */}
          <div className="clip-watch-bar" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ghost small"
              disabled={reelAt === 0}
              onClick={() => setReelAt((i) => Math.max(0, i - 1))}
            >
              ← Previous
            </button>
            <span className="small">
              {reelAt + 1} of {data.highlights.length}
              {" · "}
              <b>{data.highlights[reelAt].course}</b>
              {" · "}Hole {data.highlights[reelAt].hole}
              {data.highlights[reelAt].tag ? ` · ${data.highlights[reelAt].tag}` : ""}
            </span>
            <button
              type="button"
              className="ghost small"
              disabled={reelAt + 1 >= data.highlights.length}
              onClick={() => setReelAt((i) => i + 1)}
            >
              Next →
            </button>
            <button type="button" onClick={() => setReelAt(-1)}>Close</button>
          </div>
        </div>,
        document.body,
      )}

      {data.recent_rounds.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 10 }}>Recent rounds</h3>
          <div className="stack">
            {data.recent_rounds.map((r) => (
              <Link
                key={r.participant_id}
                to={`/g/${r.gallery_token}`}
                className="card tight"
                style={{ margin: 0, display: "flex", justifyContent: "space-between", alignItems: "center", textDecoration: "none", color: "inherit" }}
              >
                <div>
                  <b>{r.course}</b>
                  <div className="small muted">
                    {new Date(r.tee_time).toLocaleString([], { month: "short", day: "numeric", year: "numeric" })}
                  </div>
                </div>
                <span className="muted">→</span>
              </Link>
            ))}
          </div>
        </div>
      )}

      <div className="card" style={{ background: "var(--primary-soft)", border: "1px solid var(--emerald-200)" }}>
        <h3 style={{ color: "var(--emerald-800)" }}>Want a profile?</h3>
        <p className="small" style={{ color: "var(--emerald-800)", marginBottom: 12 }}>
          Sign up + register a round, every round you play stacks here automatically.
        </p>
        <Link to="/courses" className="btn small" style={{ width: "auto" }}>Pick a course — $20</Link>
      </div>
    </div>
  );
}
