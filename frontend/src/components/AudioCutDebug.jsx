/* THE AUDIO CUTTER'S WORKING, IN ONE PLACE.
 *
 * This lived inline in Test Cut, hard-wired to its own result object.
 * Production can now be SET to the audio cutter, and when an operator
 * asks why it cut where it did, the honest answer is the same table --
 * the noise floor it measured, the gate that came out of it, every
 * peak it looked at and which test each one failed. A second renderer
 * would drift from this one, and the one that drifted would be the one
 * nobody was reading at the time.
 *
 * `debug` is the dict the detector fills. Every field is optional: it
 * is written by whichever branch of the detector ran, and a run that
 * stopped early fills less of it.
 */
export function AudioCutDebug({ debug }) {
  const d = debug || {};
  if (d.median_envelope == null && !Array.isArray(d.top_peaks)) {
    // NOTHING MEASURED. Not an error -- a cutter that never got as far
    // as reading the audio has nothing to show, and an empty table
    // would read as "it looked and found nothing", which is a
    // different fact.
    return d.reason ? <span className="muted">{d.reason}</span> : null;
  }
  return (
    <>
    audio: noise floor={d.median_envelope?.toFixed(4)}{" "}
    · gate ×{d.min_peak_ratio_used}
    {" "}(min_ratio) = {d.threshold?.toFixed(4)}
    {d.highpass_hz_used > 0 && (
      <> · highpass @ {d.highpass_hz_used} Hz</>
    )}
    {d.threshold_floor_hit && (
      <> · <span className="warn-text">
        floor {d.threshold_floor ?? "0.001"} active —
        raise audio ratio above {(
          (d.threshold_floor ?? 0.001)
          / (d.median_envelope || 1)
        ).toFixed(1)} to bite
      </span></>
    )}
    <br />
    peaks: {d.n_raw_peaks} over the gate
    {d.n_after_attack != null && (
      <> · {d.n_after_attack} passed attack
      {" "}(≤{Math.round((d.max_attack_sec_used ?? 0)*1000)}ms, max_attack_ms)</>
    )}
    {" "}· {d.n_after_nms} after separation
    {" "}({d.min_separation_sec}s, min_separation_sec)
    {" "}over {d.duration_sec?.toFixed(1)}s
    {Array.isArray(d.top_peaks) &&
      d.top_peaks.length > 0 && (
      <>
        <br />
        top peaks (sec, ×ratio over the noise floor,
        {" "}attack ms, kept):
        {" "}✗attack = too slow a rise (voice/rumble);
        {" "}✗-sep = another peak within min_separation_sec.
        <br />
        {d.top_peaks.map((p, i) => (
          <span key={i} style={{ marginRight: 10 }}>
            {p.peak_sec}s ×{p.ratio}
            {p.attack_ms != null && (
              <>
                {" "}
                {/* "≥" marks the sentinel: the rise
                    never crossed the reference, so
                    this is "could not measure", not
                    a slow attack. Printing it as a
                    plain number hid a filter that
                    was dropping quiet strikes. */}
                {p.attack_measured === false ? "≥" : ""}
                {p.attack_ms}ms{p.passes_attack ? "" : "✗attack"}</>
            )}
            {" "}{p.kept ? "✓"
              : (p.passes_attack === false ? "" : "✗sep")}
          </span>
        ))}
      </>
    )}
    {/* NOTHING CLEARED THE GATE. Shown on its
        own line rather than mixed into the
        peaks above, because these failed a
        different test and the ✗attack / ✗sep
        marks up there would be a lie about
        them — they never got that far. */}
    {Array.isArray(d.below_gate_peaks) &&
      d.below_gate_peaks.length > 0 && (
      <>
        <br />
        <span className="warn-text">
          nothing reached the gate.
        </span>{" "}
        loudest peaks anyway (sec, ×ratio over the
        {" "}noise floor):
        {d.loudest_ratio != null && (
          <>
            {" "}a gate under{" "}
            ×{d.loudest_ratio}
            {" "}would have caught the loudest.
          </>
        )}
        <br />
        {d.below_gate_peaks.map((p, i) => (
          <span key={i} style={{ marginRight: 10 }}>
            {p.peak_sec}s ×{p.ratio}
            {/* Below the gate the rise usually
                cannot be measured at all — the
                reference sits above half the
                peak — so "≥" is the norm here,
                not the exception. */}
            {p.attack_ms != null && p.attack_measured !== false && (
              <> {p.attack_ms}ms</>
            )}
          </span>
        ))}
      </>
    )}
    </>
  );
}
