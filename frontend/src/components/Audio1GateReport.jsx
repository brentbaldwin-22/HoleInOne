/*
 * What this tee box's audio1 gates did to this run.
 *
 * THE REPORT WAS ALWAYS IN THE RESPONSE and nothing rendered it, so
 * "are the gates even being applied?" had no answer on the screen
 * that applied them — only a cut list to squint at and a crest column
 * to compare against a bar by eye.
 *
 * THE THREE STATES ARE NOT THE SAME and look identical from the cut
 * list alone:
 *
 *   not armed   the gates were worked out and refused nothing. The
 *               cuts are what the detector's own thresholds gave.
 *   armed       they decided. Anything dropped is named, with its
 *               reading and the bar it missed.
 *   did not run no gate is switched on, or the measurement failed —
 *               and a failure KEEPS every peak, so a clip that could
 *               not be measured looks exactly like a clip nothing
 *               objected to.
 *
 * The third is the one worth printing loudest: it is the state where
 * the gates are set, armed, and silently doing nothing.
 */
export default function Audio1GateReport({ rep }) {
  if (!rep) return null;

  const armed = !!rep.armed;
  const ran = !!rep.ran;
  const dropped = (rep.rejected || []).length;
  const failed = !ran && !!rep.why
    && /could not measure|gate pass failed/.test(rep.why);

  const tone = failed ? "err-text" : "";
  const pill = !ran ? (failed ? "could not judge" : "not applied")
    : armed ? `dropped ${dropped}` : "reporting only";

  return (
    <div className={`tiny ${tone}`} style={{ marginBottom: 6 }}>
      <span className="pill small">audio1 gates</span>{" "}
      <b>{pill}</b>
      {ran && (
        <> · {rep.n_in} peak{rep.n_in === 1 ? "" : "s"} judged ·{" "}
          {rep.kept} kept
          {!armed && dropped > 0 && (
            <> · <b>{dropped} would have been dropped if armed</b></>
          )}
        </>
      )}
      {rep.why && <> · {rep.why}</>}
      {failed && (
        <> — a measurement that falls over KEEPS every peak, so this
          run's cuts are the detector's alone.</>
      )}
      {(rep.rejected || []).length > 0 && (
        <div style={{ marginTop: 2, opacity: 0.85 }}>
          {rep.rejected.slice(0, 8).map((r) => (
            <div key={r.peak_time_sec}>
              {r.peak_time_sec.toFixed(2)}s —{" "}
              {(r.failed || []).map((f) => (
                `${f.of} ${f.read} ${f.cmp === "min" ? "<" : ">"} ${f.bar}`
              )).join(", ")}
              {(r.not_measured || []).length > 0 && (
                <span className="muted">
                  {" "}· not measured: {r.not_measured.join(", ")}
                </span>
              )}
            </div>
          ))}
          {rep.rejected.length > 8 && (
            <div className="muted">…and {rep.rejected.length - 8} more</div>
          )}
        </div>
      )}
    </div>
  );
}
