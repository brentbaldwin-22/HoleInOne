import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Brand, Icon } from "../components/Brand.jsx";
import { api } from "../api.js";
import useAuth from "../hooks/useAuth.js";
import { logoUrl } from "../theme.js";
import useSiteTheme from "../hooks/useSiteTheme.js";

// The four prize games, in the order they matter to a player standing on
// the tee: the one you could win today, the one you could win with one
// swing, the weekly, then the monthly. Cadence and prize are deliberately
// on the card -- "what could I win and how often" is the whole question.
//
// Design and rules live in docs/contests.md. Prize copy is hard-coded for
// launch; when the amounts move with volume it should come from the API.
const GAMES = [
  {
    key: "ctp",
    title: "Closest to the Pin",
    cadence: "Daily · per course",
    icon: "flag",
    blurb:
      "Nearest the hole on any camera'd par 3 at your course wins the day. Resets at midnight.",
    prize: "Free round",
    to: "/contests#ctp",
  },
  {
    key: "ace",
    title: "Hole-in-One",
    cadence: "Anytime",
    icon: "dollar",
    blurb:
      "Every ace is on camera and on the wall. One swing, any round, any course.",
    prize: "$10,000",
    to: "/contests#ace",
  },
  {
    key: "sotw",
    title: "Shot of the Week",
    cadence: "Weekly · all courses",
    icon: "sparkle",
    blurb:
      "The best shot of the week, picked by us. Winner gets posted on our socials.",
    prize: "Free round + featured",
    to: "/contests#sotw",
  },
  {
    key: "draw",
    title: "Monthly Draw",
    cadence: "Monthly · all courses",
    icon: "users",
    blurb:
      "Every round you play is one entry. Play more, more chances. Drawn on the 1st.",
    prize: "$100",
    to: "/contests#draw",
  },
];

// Five steps, and they really are a sequence: each one only happens
// after the one above it. That is why they carry numbers.
const STEPS = [
  {
    icon: "qr",
    title: "Register before you play",
    body: "On this site, or scan the QR code at the pro shop. Under a minute.",
  },
  {
    icon: "camera",
    title: "Snap an outfit photo",
    body: "Head-to-toe is all we need — that's how we match shots to you.",
  },
  {
    icon: "sparkle",
    title: "Play your par 3s",
    body: "The cameras start themselves. Nothing to press, nothing to carry.",
  },
  {
    icon: "share",
    title: "Check your inbox",
    body: "One email, every par-3 clip attached, tracer drawn on, ready to post.",
  },
];

export default function Home() {
  const { user } = useAuth();
  const theme = useSiteTheme();
  const [showcase, setShowcase] = useState(null);
  const [courses, setCourses] = useState(null);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.listShowcase().then(setShowcase).catch(() => setShowcase([]));
    api.listPublicCourses().then(setCourses).catch(() => setCourses([]));
    api.publicStats().then(setStats).catch(() => setStats(null));
  }, []);

  // Single featured video for now — only slot 1 appears on Home.
  const featured = (showcase || []).find((s) => s.position === 1 && s.source_url);
  const showcaseLoaded = showcase !== null;

  // The ticker only earns its row when there is something live to say.
  const ticker = [];
  if (stats?.clips_this_week > 0) {
    ticker.push([stats.clips_this_week,
      `clip${stats.clips_this_week === 1 ? "" : "s"} delivered this week`]);
  }
  if (stats?.golfers_today > 0) {
    ticker.push([stats.golfers_today,
      `golfer${stats.golfers_today === 1 ? "" : "s"} playing today`]);
  }
  if (stats?.aces_pending > 0) {
    ticker.push([stats.aces_pending,
      `ace claim${stats.aces_pending === 1 ? "" : "s"} under review`]);
  }
  if (!ticker.length && stats?.total_clips_delivered > 0) {
    ticker.push([stats.total_clips_delivered, "clips delivered to date"]);
  }

  return (
    <div className="home">
      <div className="home-wrap" style={{ paddingTop: 20 }}>
        <Brand />
      </div>

      <header className="home-hero">
        <div className="home-hero-bands" aria-hidden="true" />
        <div className="home-wrap home-hero-in">
          <div>
            <p className="home-eyebrow">Par-3 video system</p>
            <h1>
              Every par&#8209;3 shot,<br />traced and <em>delivered.</em>
            </h1>
            <p className="home-sub">
              Two cameras on every camera&apos;d par 3 — one at the tee, one on
              the green. Your tee shot comes back with the ball&apos;s flight
              drawn on it, in your inbox after the round. Make an ace and
              win <b>$10,000</b>.
            </p>
            <div className="home-cta">
              <Link to="/courses" className="btn">Pick a course — $20</Link>
              <Link to="/sample" className="btn secondary">See sample clips</Link>
            </div>
          </div>
          <div className="home-logo-plate">
            <img src={logoUrl(theme.direction)} alt="GolfReelz" />
          </div>
        </div>
      </header>

      {ticker.length > 0 && (
        <div className="home-ticker">
          <div className="home-wrap home-ticker-in">
            <span className="pulse" aria-hidden="true" />
            {ticker.map(([n, label]) => (
              <span key={label}><b>{n}</b>{label}</span>
            ))}
          </div>
        </div>
      )}

      {courses && courses.length > 0 && (
        <section className="home-section">
          <div className="home-wrap home-section-in">
            <div className="home-rule" />
            <div className="home-sec-head">
              <h2>Now live at</h2>
              <p>
                Cameras are installed hole by hole. If your home course
                isn&apos;t here yet, tell us and we&apos;ll reach out to them.
              </p>
            </div>
            <div className="home-courses">
              {courses.map((c) => (
                <div key={c.id} className="course-plate">
                  <div className="name">{c.name}</div>
                  {c.location && <div className="loc">{c.location}</div>}
                </div>
              ))}
              <div className="course-plate open">
                <div className="name">Your course here</div>
                <div className="loc">
                  <a href="mailto:hello@golfreelz.com">Request an install</a>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="home-section">
        <div className="home-wrap home-section-in">
          <div className="home-rule" />
          <div className="home-sec-head">
            <h2>Four ways to win</h2>
            <p>
              Every round you play enters you automatically. No extra
              sign-up, no separate entry fee.
            </p>
          </div>
          <div className="game-grid">
            {GAMES.map((g) => (
              <Link key={g.key} to={g.to} className="game-tile">
                <div className="strip" aria-hidden="true" />
                <div className="body">
                  <div className="cadence">{g.cadence}</div>
                  <h3>
                    <span className="inline" style={{ gap: 8 }}>
                      <Icon name={g.icon} size={16} /> {g.title}
                    </span>
                  </h3>
                  <p>{g.blurb}</p>
                </div>
                <div className="prize">
                  <span className="label">Prize</span>
                  <span className="amount">{g.prize}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section className="home-section">
        <div className="home-wrap home-section-in">
          <div className="home-rule" />
          <div className="home-sec-head"><h2>How it works</h2></div>
          <div className="step-row">
            {STEPS.map((s, i) => (
              <div key={s.title} className="step">
                <div className="num">{i + 1}</div>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="home-section">
        <div className="home-wrap home-section-in">
          <div className="home-rule" />
          <div className="home-sec-head"><h2>What you get back</h2></div>
          <div className="home-clip-grid">
            <div className="home-clip-frame">
              {!showcaseLoaded ? (
                <div className="shimmer" />
              ) : featured ? (
                <video
                  src={featured.source_url}
                  poster={featured.thumbnail_url || undefined}
                  controls
                  playsInline
                  preload="metadata"
                />
              ) : (
                <div className="bandbg" aria-hidden="true" />
              )}
            </div>
            <div>
              <h3>Tee to green, in one cut.</h3>
              <p className="muted" style={{ marginTop: 10 }}>
                The tee camera follows the strike and draws the ball&apos;s
                flight. The green camera catches it landing. Both halves are
                spliced into a single clip with your name, the hole and the
                distance from the pin.
              </p>
              <ul className="home-speclist">
                <li>Tracer drawn from the real ball track, not an animation</li>
                <li>Distance to the pin measured, not estimated</li>
                <li>Vertical cut for stories, landscape for everywhere else</li>
                <li>Yours to keep and post — no watermark on your own shot</li>
              </ul>
              {(featured?.title || featured?.caption) && (
                <p className="small muted" style={{ marginTop: 12 }}>
                  {featured.title}
                  {featured.title && featured.caption ? " — " : ""}
                  {featured.caption}
                </p>
              )}
            </div>
          </div>
        </div>
      </section>

      {!user && (
        <section className="home-section">
          <div className="home-wrap home-section-in">
            <div className="home-rule" />
            <div className="home-sec-head">
              <h2>Make an account, keep every shot</h2>
              <p>
                Every round you ever play with GolfReelz lands in one
                dashboard. Pull up past shots, re-share clips, and
                re-register in a tap — no digging through old emails.
              </p>
            </div>
            <div className="home-cta">
              <Link to="/signup" className="btn">Create free account</Link>
              <Link to="/login" className="btn secondary">Log in</Link>
            </div>
          </div>
        </section>
      )}

      <div className="home-close">
        <div className="home-wrap home-close-in">
          <div className="home-rule" />
          <h2>Get your next round on camera.</h2>
          <p>
            Pick your course, play your par 3s, and check your email.
            That&apos;s the whole thing.
          </p>
          <div className="home-cta">
            <Link to="/courses" className="btn">Pick a course — $20</Link>
            <Link to="/sample" className="btn secondary">See sample clips</Link>
          </div>
          <p className="home-price">
            $20 per round, per course · $10,000 for an ace
          </p>
        </div>
      </div>

      <div className="home-wrap" style={{ paddingBlock: 28 }}>
        <details className="card" style={{ marginBottom: 0 }}>
          <summary className="small muted" style={{ cursor: "pointer" }}>
            For operators + testers
          </summary>
          <div className="stack" style={{ gap: 4, marginTop: 10 }}>
            <div className="small"><code>/r/&lt;course_token&gt;</code> — mobile registration</div>
            <div className="small"><code>/g/&lt;gallery_token&gt;</code> — golfer gallery</div>
            <div className="small"><Link to="/admin">/admin</Link> — operator dashboard</div>
            <div className="small"><Link to="/admin/long-upload">/admin/long-upload</Link> — long video upload + auto-cut</div>
            <div className="small"><Link to="/admin/broadcast-clips">/admin/broadcast-clips</Link> — produced clips + share</div>
            <div className="small"><Link to="/admin/cameras">/admin/cameras</Link> — on-course capture devices</div>
            <div className="small"><Link to="/admin/review">/admin/review</Link> — hole-in-one verification queue</div>
          </div>
        </details>
      </div>
    </div>
  );
}
