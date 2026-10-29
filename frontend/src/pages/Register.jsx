import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { Brand, Icon } from "../components/Brand.jsx";
import useAuth from "../hooks/useAuth.js";

// Today, and a sensible first guess at "now", both on the GOLFER's clock
// -- they are standing at the course when they fill this in. Local
// strings, not UTC: these feed <input type="date"/"time">, whose values
// are wall-clock, and the conversion to UTC happens once on submit.
function todayLocal() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function nowLocalRounded() {
  const d = new Date();
  d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5, 0, 0);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function Register() {
  const { courseToken } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const [course, setCourse] = useState(null);
  // THE DATE AND TIME FIELDS ARE UNCONTROLLED, ON PURPOSE.
  //
  // Held in React state they could not be typed into. A native date or
  // time input is a row of segments, and the moment you type the first
  // digit of the hour the value is incomplete, so the browser fires
  // onChange with "" -- state became "", React re-rendered, and the
  // segment you were halfway through was wiped. Every keystroke reset
  // the one before it. Only the arrow keys worked, which nobody would
  // ever guess, and that is exactly what "you can not adjust the time"
  // was. Uncontrolled, the segments keep their own state and behave the
  // way the platform intends -- including the wheel on a phone.
  const dateRef = useRef(null);
  const clockRef = useRef(null);
  const [name, setName] = useState(user?.name || "");
  const [mobile, setMobile] = useState("");
  const [email, setEmail] = useState(user?.email || "");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setCourse(await api.courseByToken(courseToken));
      } catch (e) {
        setError(e.message);
      }
    })();
  }, [courseToken]);

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (!mobile && !email) throw new Error("Enter a mobile number or an email so we can text you your clips.");
      const teeDate = dateRef.current?.value || "";
      const teeClock = clockRef.current?.value || "";
      if (!teeDate || !teeClock) throw new Error("Pick the day and time you're teeing off.");

      // The pickers hand back wall-clock strings. Parsed WITHOUT a
      // timezone this is the golfer's own local time, which is what they
      // read off the wheel; toISOString is the one conversion to UTC,
      // and the backend stores that clock naive like every other
      // datetime in the app.
      const when = new Date(`${teeDate}T${teeClock}`);
      if (isNaN(when.getTime())) throw new Error("That tee time didn't look like a real date and time.");

      const fd = new FormData();
      fd.append("course_token", courseToken);
      fd.append("tee_time_at", when.toISOString());
      fd.append("name", name);
      fd.append("mobile", mobile);
      fd.append("email", email);

      const res = await api.register(fd);
      if (res.client_secret && !res.paid) {
        nav(`/pay/${res.participant_id}`, {
          state: {
            client_secret: res.client_secret,
            gallery_url: res.gallery_url,
            total: 20,
          },
        });
      } else {
        nav(`/confirm/${res.participant_id}`, { state: res });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (error && !course) {
    return (
      <div className="wrap">
        <Brand />
        <div className="card">
          <h2>Couldn't load this course</h2>
          <p className="muted small">{error}</p>
        </div>
      </div>
    );
  }

  if (!course) {
    return (
      <div className="wrap">
        <Brand />
        <div className="card"><div className="shimmer" style={{ height: 140 }} /></div>
      </div>
    );
  }

  return (
    <div className="wrap">
      <Brand />

      <div className="hero" style={{ padding: "28px 24px" }}>
        <span className="eyebrow"><Icon name="flag" size={14} /> {course.name}</span>
        <h1 style={{ fontSize: "clamp(1.6rem, 3.2vw, 2.1rem)" }}>Let's get your shots on tape.</h1>
        <p style={{ fontSize: "0.95rem" }}>{course.location}</p>
        {!user && (
          <p className="small" style={{ marginTop: 12, color: "rgba(255,255,255,0.85)" }}>
            Have an account? <Link to={`/login?next=/r/${courseToken}`} style={{ color: "white", textDecoration: "underline" }}>Log in</Link> to save this round to your history.
          </p>
        )}
      </div>

      {course.livestream_url && (
        <a href={course.livestream_url} target="_blank" rel="noreferrer" className="live-banner" style={{ textDecoration: "none" }}>
          <span className="dot" />
          <div>
            <div className="label">Live now</div>
            <div className="small">Watch the course livestream while you wait.</div>
          </div>
          <span>Watch →</span>
        </a>
      )}

      <form className="card" onSubmit={submit}>
        <div className="field">
          <label>Your name</label>
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Alex Morgan" />
        </div>
        <div className="row">
          <div className="field">
            <label>Mobile</label>
            <input type="tel" value={mobile} onChange={(e) => setMobile(e.target.value)} placeholder="+1 555 123 4567" />
          </div>
          <div className="field">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@email.com" />
          </div>
        </div>
        <div className="hint small muted" style={{ margin: "-8px 0 14px" }}>
          We'll text or email when your gallery's ready. At least one required.
        </div>

        {/* Date and time rather than a pick from a published list: the
            course does not hand us its tee sheet, so the old dropdown
            was offering slots generated on the spot that nobody had
            actually booked. On a phone these are the native wheels. */}
        <div className="row">
          <div className="field">
            <label>Date</label>
            <input
              type="date"
              required
              ref={dateRef}
              defaultValue={todayLocal()}
              min={todayLocal()}
            />
          </div>
          <div className="field">
            <label>Tee time</label>
            <input
              type="time"
              required
              ref={clockRef}
              defaultValue={nowLocalRounded()}
            />
          </div>
        </div>
        <div className="hint small muted" style={{ margin: "-8px 0 14px" }}>
          Roughly when you're teeing off — it doesn't have to be exact.
        </div>

        <div className="divider" />
        <div className="inline" style={{ justifyContent: "space-between", marginBottom: 14, width: "100%" }}>
          <span className="small muted"><Icon name="lock" size={14} /> Secure Stripe checkout</span>
          <span className="inline" style={{ fontWeight: 600 }}>$20</span>
        </div>

        {error && <p className="err-text small">{error}</p>}
        <button disabled={submitting || !name}>
          {submitting ? "Processing…" : "Pay $20 and register"}
        </button>
      </form>
    </div>
  );
}
