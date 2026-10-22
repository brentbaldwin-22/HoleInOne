/**
 * The curve the rig's detector thresholds, over the whole clip.
 *
 * WHY A GRAPH AND NOT MORE NUMBERS. "No swing found, best peak 2.3
 * against 4.0" says a peak missed; it cannot say whether the clip held
 * one sharp spike that fell short, three swings that all fell short, or
 * nothing but a slow rise from people walking. Those need different
 * fixes -- a lower threshold, a tighter zone, a re-aimed camera -- and
 * the shape of the line tells them apart at a glance.
 *
 * THE Y AXIS IS THE RATIO, not raw motion: smoothed frame-difference
 * divided by the session's own median, which is exactly what
 * find_swings compares to min_ratio. So the dashed line IS the
 * threshold, and a peak above it is a peak that counted.
 */
const H = 170;
const PAD_L = 34;
const PAD_B = 20;
const PAD_T = 8;

export function MotionGraph({ trace, windows, width = 900 }) {
  const times = trace?.times || [];
  const ratio = trace?.ratio || [];
  if (times.length < 2 || ratio.length < 2) {
    return (
      <div className="small muted" style={{ padding: 6 }}>
        No motion curve — the signal was flat (a frozen camera reads this
        way), so there was nothing to take a median from.
      </div>
    );
  }
  const thr = trace.threshold || 0;
  const t0 = times[0];
  const t1 = times[times.length - 1] || 1;
  // Headroom so the tallest peak is not clipped by the frame, and so a
  // threshold above every peak is still visible -- that case is the
  // whole diagnosis when nothing is found.
  const yMax = Math.max(...ratio, thr * 1.15, 2);
  const W = width;
  const x = (t) => PAD_L + ((t - t0) / (t1 - t0)) * (W - PAD_L - 6);
  const y = (v) => PAD_T + (1 - v / yMax) * (H - PAD_T - PAD_B);

  const pts = times.map((t, i) => `${x(t).toFixed(1)},${y(ratio[i]).toFixed(1)}`)
    .join(" ");

  // Gridlines at 1 (the median itself) and the threshold.
  const ticks = [1, thr, yMax].filter((v, i, a) => v > 0 && a.indexOf(v) === i);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", height: "auto", display: "block" }}
      role="img"
      aria-label="motion ratio over the clip"
    >
      {/* Windows the detector kept, behind the line. */}
      {(windows || []).map((w, i) => (
        <rect
          key={i}
          x={x(w.start_sec)}
          y={PAD_T}
          width={Math.max(1, x(w.end_sec) - x(w.start_sec))}
          height={H - PAD_T - PAD_B}
          fill="rgba(46,204,113,0.18)"
        />
      ))}
      {ticks.map((v, i) => (
        <g key={i}>
          <line
            x1={PAD_L} x2={W - 6} y1={y(v)} y2={y(v)}
            stroke={v === thr ? "#e74c3c" : "rgba(128,128,128,0.35)"}
            strokeDasharray={v === thr ? "5 4" : "2 4"}
            strokeWidth={v === thr ? 1.5 : 1}
          />
          <text
            x={4} y={y(v) + 3.5} fontSize="10"
            fill={v === thr ? "#e74c3c" : "currentColor"}
            opacity={v === thr ? 1 : 0.6}
          >
            ×{v.toFixed(1)}
          </text>
        </g>
      ))}
      <polyline
        points={pts}
        fill="none"
        stroke="#2d7ff9"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      {/* Peaks the detector kept, marked where it put impact. */}
      {(windows || []).filter((w) => w.peak_time_sec != null).map((w, i) => (
        <circle
          key={i}
          cx={x(w.peak_time_sec)}
          cy={y(Math.min(yMax, w.ratio || 0))}
          r="3.5"
          fill="#27ae60"
          stroke="#fff"
          strokeWidth="1"
        />
      ))}
      <line
        x1={PAD_L} x2={W - 6} y1={H - PAD_B} y2={H - PAD_B}
        stroke="currentColor" opacity="0.3"
      />
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const t = t0 + f * (t1 - t0);
        return (
          <text
            key={i} x={x(t)} y={H - 6} fontSize="10"
            textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}
            fill="currentColor" opacity="0.6"
          >
            {t.toFixed(0)}s
          </text>
        );
      })}
    </svg>
  );
}
