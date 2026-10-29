import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../components/Brand.jsx";
import { fmtDateTime } from "../time.js";

/**
 * "Which of these is you?"
 *
 * Nothing assigns a swing to a golfer automatically, so they do it. The
 * server hands back ONE group -- the shots hit in the same few minutes,
 * cut on the gaps between groups rather than by a window around a tee
 * time that is for a different hole and an hour and a half earlier.
 *
 * THE CLIPS LOOP, SILENTLY, ALL AT ONCE. A list you play one at a time
 * turns four taps into four waits; four loops side by side and you spot
 * your own swing in about two seconds. Muted and autoplay+playsInline
 * because that is the only combination phones will start without a tap.
 */
export default function PickYourShots({ galleryToken, onClaimed, onAvailable }) {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [picked, setPicked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const live = useRef(true);

  useEffect(() => () => { live.current = false; }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.galleryCandidates(galleryToken, offset);
        if (!cancelled) {
          setData(d); setPicked(new Set()); setError(null);
          onAvailable?.(d.total_unclaimed || 0);
        }
      } catch (e) {
        if (!cancelled) { setError(String(e?.message || e)); onAvailable?.(0); }
      }
    })();
    return () => { cancelled = true; };
  }, [galleryToken, offset]);

  function toggle(id) {
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function confirm() {
    if (!picked.size) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.claimClips(galleryToken, [...picked]);
      // A clip someone else took a second earlier is not an error --
      // say so plainly rather than failing the whole tap.
      if (res.already_taken?.length) {
        setError(
          `${res.already_taken.length} of those had just been claimed by ` +
          `someone else in your group.`,
        );
      }
      onClaimed?.(res.claimed?.length || 0);
      const d = await api.galleryCandidates(galleryToken, offset);
      if (live.current) { setData(d); setPicked(new Set()); }
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      if (live.current) setBusy(false);
    }
  }

  if (!data || (data.clips.length === 0 && data.group_count === 0)) return null;

  return (
    <div className="card pick-card">
      <h3 style={{ marginBottom: 4 }}>Which of these is you?</h3>
      <p className="small muted" style={{ marginBottom: 14 }}>
        Shots from your group on the tee. Tap the ones that are yours — they'll
        drop straight into your gallery.
      </p>

      <div className="pick-grid">
        {data.clips.map((c) => {
          const on = picked.has(c.id);
          return (
            <button
              key={c.id}
              type="button"
              className={`pick-tile${on ? " on" : ""}`}
              onClick={() => toggle(c.id)}
              aria-pressed={on}
            >
              <video
                src={c.preview_url}
                poster={c.thumbnail_url || undefined}
                muted
                loop
                autoPlay
                playsInline
                preload="metadata"
              />
              <span className="pick-time">
                {fmtDateTime(c.captured_at, [], {
                  hour: "numeric", minute: "2-digit",
                })}
              </span>
              {on && <span className="pick-check"><Icon name="check" size={16} /></span>}
            </button>
          );
        })}
      </div>

      {error && <p className="err-text small" style={{ marginTop: 10 }}>{error}</p>}

      <div className="pick-actions">
        <button disabled={!picked.size || busy} onClick={confirm}>
          {busy
            ? "Saving…"
            : picked.size
              ? `That's me — add ${picked.size} clip${picked.size === 1 ? "" : "s"}`
              : "Tap your shots above"}
        </button>
        {data.group_count > 1 && (
          <div className="pick-nav small">
            <button
              className="ghost small"
              disabled={offset === 0 || busy}
              onClick={() => setOffset((o) => Math.max(0, o - 1))}
            >
              ← Earlier group
            </button>
            <span className="muted">
              Group {data.group_index + 1} of {data.group_count}
            </span>
            <button
              className="ghost small"
              disabled={offset >= data.group_count - 1 || busy}
              onClick={() => setOffset((o) => o + 1)}
            >
              Later group →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
