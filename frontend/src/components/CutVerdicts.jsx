/* TEST CUT'S SWING VERDICTS, IN ONE PLACE.
 *
 * The per-cut table: peak time, the judges' verdicts, the three
 * thumbnails (full frame, heat composite, four-frame strip), the
 * front-candidate list and the ball check.
 *
 * It lived inline in Test Cut, reading `testCutResults[u.id].cuts`
 * directly. Find ascents needed the same thing, and the version it
 * got was a reimplementation: different wording, fewer columns, no
 * thumbnails, and a verdict field that came from a different judge.
 * Two screens describing the same cut differently is a bug that
 * looks like a disagreement about the golf swing.
 *
 * So it takes `cuts` -- test-cut cut entries, exactly as the backend
 * builds them, with no renaming in between -- and both screens render
 * this. A column added here appears on both.
 */
export function CutVerdicts({ cuts }) {
  if (!Array.isArray(cuts) || cuts.length === 0) return null;
  return (
  <div className="tiny" style={{ fontFamily: "monospace", lineHeight: 1.6 }}>
    <div className="muted" style={{ marginBottom: 4 }}>
      <b>{cuts.length}</b> swing
      {cuts.length === 1 ? "" : "s"} detected
      · cutting skipped
    </div>
    <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr style={{ textAlign: "left" }}>
          <th style={{ padding: "2px 10px 2px 0" }}>#</th>
          <th style={{ padding: "2px 10px 2px 0" }}>peak</th>
          <th style={{ padding: "2px 10px 2px 0" }}>×ratio</th>
          <th style={{ padding: "2px 10px 2px 0" }}>attack</th>
          <th style={{ padding: "2px 10px 2px 0" }}>swing?</th>
          <th style={{ padding: "2px 10px 2px 0" }}>window</th>
          <th style={{ padding: "2px 10px 2px 0" }}>conf</th>
        </tr>
      </thead>
      <tbody>
        {cuts.map((c) => (
          <tr key={c.index}>
            <td style={{ padding: "1px 10px 1px 0" }}>{c.index + 1}</td>
            <td style={{ padding: "1px 10px 1px 0" }}>
              {c.peak_time_sec != null ? `${c.peak_time_sec.toFixed(2)}s` : "—"}
            </td>
            <td style={{ padding: "1px 10px 1px 0" }}>
              {c.ratio != null ? `×${c.ratio.toFixed(1)}` : "—"}
            </td>
            <td style={{ padding: "1px 10px 1px 0" }}>
              {c.attack_ms == null ? "—" : (
                <span style={{
                  color: c.max_attack_ms
                    && c.attack_ms > c.max_attack_ms * 0.66
                    ? "#d97706" : undefined,
                }}>
                  {c.attack_measured === false ? "≥" : ""}
                  {c.attack_ms}ms
                  {c.max_attack_ms
                    ? ` / ${c.max_attack_ms}` : ""}
                </span>
              )}
            </td>
            <td style={{ padding: "1px 10px 1px 0",
                         verticalAlign: "top" }}
                title={c.verify_reason || ""}>
              <div style={{
                color: c.is_swing == null ? undefined
                  : (c.is_swing ? "#16a34a" : "#dc2626"),
              }}>
                {c.is_swing == null
                  ? (c.verify_reason ? "?" : "—")
                  : `${c.is_swing ? "✓ swing" : "✗ not"}${
                    c.verify_confidence
                      ? ` (${c.verify_confidence})` : ""}`}
              </div>
              {/* THE JUDGE SAID SWING AND WE DID NOT
                  USE IT. That is a different thing
                  from the judge saying no, and it
                  used to be invisible: the loser was
                  never judged at all, so the row
                  read as a bare "?". */}
              {c.is_swing === true && c.kept === false && (
                <div style={{ color: "#b45309" }}>
                  superseded
                </div>
              )}
              {/* SIDE BY SIDE, AND THE WHOLE
                  FRAME FIRST. Stacked, the
                  pictures pushed the verdict off
                  the screen; and the full frame
                  is the only one that shows what
                  the crop LEFT OUT, which is
                  where every wrong crop so far
                  has been found. Operator's
                  reference only -- the judge
                  never saw it. */}
              <div style={{
                display: "flex", gap: 4,
                alignItems: "flex-start",
                flexWrap: "wrap", marginTop: 3,
              }}>
              {c.full_frame_url && (
                <a href={c.full_frame_url}
                   target="_blank" rel="noreferrer"
                   title="The whole frame at the impact, uncropped, with the trigger zones and the measured feet drawn on. For your reference only — the judge was never shown this.">
                  <img src={c.full_frame_url}
                       alt={`full frame at ${
                         c.peak_time_sec}s`}
                       style={{
                         width: 260, display: "block",
                         borderRadius: 4,
                         border: "1px solid var(--border, #333)",
                       }} />
                </a>
              )}
              {c.heat_image_url && (
                <a href={c.heat_image_url}
                   target="_blank" rel="noreferrer"
                   title="Open the full-size composite. The trigger zones are outlined in magenta for you; the judge was shown the same picture without them.">
                  <img src={c.heat_image_url}
                       alt={`motion heat around ${
                         c.peak_time_sec}s`}
                       style={{
                         width: 200, display: "block",
                         borderRadius: 4,
                         border: "1px solid var(--border, #333)",
                       }} />
                </a>
              )}
              {/* AND THE FRAMES, when the judge was
                  shown them. The checkbox changed what
                  the MODEL saw and nothing changed what
                  the operator saw, which makes a
                  verdict from them unarguable. */}
              {c.frames_image_url && (
                <a href={c.frames_image_url}
                   target="_blank" rel="noreferrer"
                   title="Open the four frames: -0.25s, 0.00s, +0.15s, +0.40s around the impact. The trigger zones are outlined in magenta for you; the judge was shown the same tiles without them.">
                  <img src={c.frames_image_url}
                       alt={`frames around ${
                         c.peak_time_sec}s`}
                       style={{
                         width: 200, display: "block",
                         borderRadius: 4,
                         border: "1px solid var(--border, #333)",
                       }} />
                </a>
              )}
              </div>
              {/* SAY SO ONCE, UNDER BOTH PICTURES.
                  A magenta box in a motion heat
                  map is not self-explanatory, and
                  the thing that most needs saying
                  is that the judge did NOT see it
                  -- otherwise every verdict that
                  mentions a shape invites the
                  question of whether it meant the
                  outline. */}
              {(c.heat_image_url || c.frames_image_url)
                && c.gate !== undefined && (
                <div className="tiny muted"
                     style={{ marginTop: 2,
                              maxWidth: 200,
                              whiteSpace: "normal",
                              lineHeight: 1.3 }}>
                  <span style={{ color: "#c0f" }}>
                    magenta
                  </span>
                  {" "}= trigger zones, drawn for you
                  only — the judge saw these pictures
                  without them
                </div>
              )}
              {/* EACH GOLFER THAT MOVED, when more
                  than one did. Nothing in the heat
                  map says which of them swung, so
                  each was asked about separately and
                  this shows what each answered. */}
              {Array.isArray(c.candidates)
                && c.candidates.length > 1 && (
                <div className="tiny" style={{
                  marginTop: 4, maxWidth: 200,
                  whiteSpace: "normal", lineHeight: 1.35,
                }}>
                  <div className="muted">
                    {c.candidates.length} golfers moved —
                    each asked about separately:
                  </div>
                  {c.candidates.map((a, ai) => (
                    <div key={ai} style={{ marginTop: 3 }}>
                      <a href={a.image_url}
                         target="_blank" rel="noreferrer">
                        <img src={a.image_url} alt=""
                             style={{
                               width: 92, display: "block",
                               borderRadius: 3,
                               border: "1px solid var(--border, #333)",
                             }} />
                      </a>
                      <span style={{
                        color: a.is_swing
                          ? "#1a7f37" : "#c0392b",
                      }}>
                        {a.is_swing === true ? "swing"
                          : a.is_swing === false ? "not" : "?"}
                      </span>{" "}
                      <span className="muted">
                        ({a.confidence || "-"}) at x=
                        {a.box && a.box[0]}
                      </span>
                      <div className="muted">{a.reason}</div>
                    </div>
                  ))}
                </div>
              )}
              {/* AUDIO 2 ANSWERS IN FOUR CLASSES,
                  not yes/no, and the class is
                  the point: PROBABLE and
                  CONFIRMED both upload but mean
                  different things, and UNCERTAIN
                  is the one the operator asked
                  the model to use instead of
                  guessing. Flattening them to a
                  tick would throw away the whole
                  reason for the process. */}
              {c.classification && (
                <div className="tiny" style={{
                  marginTop: 4, maxWidth: 200,
                  whiteSpace: "normal",
                  lineHeight: 1.35,
                }}>
                  <div>
                    <b style={{
                      color:
                        c.classification === "CONFIRMED_SWING"
                          ? "#1a7f37"
                          : c.classification === "PROBABLE_SWING"
                          ? "#5a8f20"
                          : c.classification === "NOT_SWING"
                          ? "#c0392b" : "#b45309",
                    }}>
                      {c.classification.replace("_", " ").toLowerCase()}
                    </b>
                    {c.confidence_pct !== null
                      && c.confidence_pct !== undefined && (
                      <span className="muted">
                        {" "}· {c.confidence_pct}% confident
                      </span>
                    )}
                  </div>
                  <div className="muted">
                    {[["golfer", c.golfer_visible],
                      ["swing motion", c.swing_motion_visible],
                      ["impact aligned", c.impact_alignment]]
                      .filter(([, v]) => v !== null
                                      && v !== undefined)
                      .map(([k, v]) => (
                        <span key={k} style={{ marginRight: 6 }}>
                          {v === true || v === "true"
                            ? "✓" : v === false || v === "false"
                            ? "✗" : "?"}{" "}{k}
                        </span>
                      ))}
                  </div>
                </div>
              )}
              {/* THE BALL. The most direct
                  evidence there is, so it gets
                  its own line: a ball does not
                  leave a tee by itself. Silent
                  when it could not be read --
                  an obstructed ball must not
                  look like an absent one. */}
              {c.ball_why && (
                <div className="tiny" style={{
                  marginTop: 2, maxWidth: 200,
                  whiteSpace: "normal",
                  lineHeight: 1.3,
                  color: c.ball_left === true
                    ? "#1a7f37"
                    : c.ball_left === false
                    ? "#c0392b" : "#777",
                }}>
                  {/* ONE FIELD NAMES THE ANSWER. The detector has
                      four outcomes and `ball_left` can say three, so
                      this used to read `ball_balls` to tell "no ball
                      found" from "a ball, and no idea what it did" —
                      a count standing in for a verdict. `ball_state`
                      says which. The fallback is for rows stored
                      before it existed. */}
                  {{
                    BALL_PRESENT_DISAPPEARED: "⛳ ball was hit",
                    BALL_PRESENT_REMAINED: "⛳ ball was NOT hit",
                    NO_BALL_DETECTED: "⛳ no ball identified",
                    INCONCLUSIVE: "⛳ ball found — outcome unclear",
                  }[c.ball_state] ?? (
                    c.ball_left === true
                      ? "⛳ ball was hit"
                      : c.ball_left === false
                      ? "⛳ ball was NOT hit"
                      : c.ball_balls
                      ? "⛳ ball found — outcome unclear"
                      : "⛳ no ball identified")}
                  {c.decided_by === "majority"
                    && c.ball_left !== null
                    && c.ball_left !== undefined
                    ? " — one vote of three" : ""}
                  <div className="muted">
                    {c.ball_why}
                  </div>
                  {/* WHICH SEARCH ANSWERED. The wide
                      band and the stance box have
                      different bars, so "found" from
                      one is not the same claim as
                      "found" from the other. */}
                  {/* WHAT THE BALL SAYS ABOUT THE LAG.
                      The audio peak is the impact plus
                      whatever the mic and the encoder
                      add; the ball is the only thing in
                      the picture that knows when the
                      strike was. This is a reading off
                      one cut -- the `audio_lag_sec`
                      gate is what an operator sets from
                      several of them. */}
                  {c.ball_lag_implied != null && (
                    <div className="muted">
                      the ball says the strike was{" "}
                      <b>{c.ball_lag_implied}s</b> before
                      the sound
                      {c.ball_lag_used
                        ? ` (searching ${c.ball_lag_used}s early)`
                        : ""}
                    </div>
                  )}
                  {c.ball_found_by && (
                    <div className="muted">
                      found by {c.ball_found_by}
                    </div>
                  )}
                  {/* AND THE DISAGREEMENT, SAID OUT
                      LOUD. The judge is a vision
                      model and the ball check is
                      OpenCV; they know nothing about
                      each other. When the model can
                      see a ball on the grass and the
                      detector cannot, that is a
                      measurement about the detector,
                      and until now the two sentences
                      sat in the same panel with
                      nothing joining them up. */}
                  {c.frames_ball_seen === true
                    && c.ball_state === "NO_BALL_DETECTED" && (
                    <div style={{ color: "#b45309" }}>
                      the judge could see a ball here
                      and the detector could not
                    </div>
                  )}
                  {/* AND WHERE THE JUDGE PUT IT.
                      The model's answer, in frame
                      pixels, marked "AI" on the
                      strip in yellow -- never the
                      ball's own red, because this
                      is a claim from a different
                      process and nothing has
                      checked it yet. */}
                  {/* THE BOX, BLOWN UP, WITH BOTH ANSWERS.
                      Six tiles of nothing but the grass
                      the ball check searched: a circle
                      where the brightness detector put
                      the ball, crosshairs where the
                      judge did. The swing strip is
                      framed on the golfer and a ball is
                      three pixels in it; this is the
                      other question's picture. */}
                  {c.ball_box_url && (
                    <div style={{ marginTop: 3 }}>
                      <a href={c.ball_box_url} target="_blank"
                         rel="noreferrer">
                        <img src={c.ball_box_url}
                             alt="the search box, zoomed"
                             style={{ width: "100%", maxWidth: 200,
                                      borderRadius: 3,
                                      display: "block" }} />
                      </a>
                      <div className="muted">
                        the box at {c.ball_box_zoom}x
                        {c.ball_box_seen === true
                          ? " · judge: ball seen"
                          : c.ball_box_seen === false
                          ? " · judge: no ball in the box" : ""}
                        {c.ball_box_gone === true
                          ? ", and gone after" : ""}
                      </div>
                      {c.ball_box_reason && (
                        <div className="muted">
                          {c.ball_box_reason}
                        </div>
                      )}
                    </div>
                  )}
                  {Array.isArray(c.frames_ball_xy) && (
                    <div className="muted">
                      the judge puts the ball at{" "}
                      ({c.frames_ball_xy[0]},{" "}
                      {c.frames_ball_xy[1]})
                    </div>
                  )}
                  {/* WHICH OF THE SIX IT WAS READ FROM. The sentence
                      above argues about one candidate; the counts in
                      it are over the whole set. Neither can say that
                      a frame was never read — which quietly turns
                      "the same spot in 2 of the first 3" into "in 2
                      of the 2 that could be read", and is the
                      difference between "no ball" and "no frames".
                      ● kept something · – read and empty · ✕ unread,
                      and the strike frame never votes. */}
                  {Array.isArray(c.ball_frames)
                    && c.ball_frames.length > 0 && (
                    <div className="muted" style={{
                      fontFamily: "monospace", marginTop: 2,
                      display: "flex", gap: 5, flexWrap: "wrap",
                    }}>
                      {c.ball_frames.map((f, fi) => (
                        <span
                          key={fi}
                          title={[
                            f.at == null ? `frame ${fi}`
                              : `${f.at > 0 ? "+" : ""}${f.at.toFixed(2)}s`,
                            f.role === "strike" ? "the strike frame"
                              : f.role === "before" ? "before the strike"
                              : "after the strike",
                            !f.read ? "could not be read"
                              : `${f.kept} ball-like spot(s) kept`,
                            f.in_rule ? "counted" : "did not count",
                          ].join(" · ")}
                          style={{
                            opacity: f.read ? 1 : 0.45,
                            textDecoration: f.in_rule
                              ? "none" : "line-through",
                          }}
                        >
                          {f.at == null ? fi
                            : `${f.at > 0 ? "+" : ""}${f.at.toFixed(2)}`}
                          {!f.read ? "✕" : f.kept > 0 ? "●" : "–"}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* WHICH GOLFER, AND WHY THAT ONE.
                  OUTSIDE the audio-2 block: both
                  audio processes crop to a single
                  player now, and a crop nobody
                  can explain is a crop nobody can
                  argue with. Says so too when the
                  rule declined and fell back. */}
              {c.picked_front && (
                <div className="muted tiny"
                     style={{ marginTop: 2,
                              maxWidth: 200,
                              whiteSpace: "normal",
                              lineHeight: 1.3 }}>
                  crop: {c.picked_front}
                </div>
              )}
              {/* AND ITS WORKING: what was
                  measured on each candidate, and
                  which one won. */}
              {Array.isArray(c.front_candidates)
                && c.front_candidates.length > 0 && (
                <div className="muted tiny"
                     style={{ marginTop: 2 }}>
                  {c.front_candidates
                    .slice()
                    .sort((a, b) => a.feet[1] - b.feet[1])
                    .map((f, i) => (
                    <div key={i} style={{
                      color: f.picked
                        ? "#c0f" : undefined,
                    }}>
                      {f.picked ? "▸ " : "  "}
                      feet ({f.feet[0]},{f.feet[1]})
                      {" · "}{f.box[2]}×{f.box[3]}
                      {f.zone
                        ? ` · floor ${f.floor}`
                        : " · not in a zone"}
                      {f.seen_at !== undefined
                        && f.seen_at !== null
                        ? ` · seen ${f.seen_at > 0 ? "+" : ""}${f.seen_at}s`
                        : ""}
                    </div>
                  ))}
                </div>
              )}
              {/* BOTH VERDICTS, SIDE BY SIDE, when
                  the frames were asked for. Which
                  question to keep is the decision in
                  front of the operator, and one
                  merged answer hides the
                  disagreement that would settle it. */}
              {c.heat_is_swing !== undefined && (
                <div className="tiny" style={{
                  marginTop: 4, maxWidth: 200,
                  whiteSpace: "normal", lineHeight: 1.35,
                }}>
                  <div>
                    <b>heat only:</b>{" "}
                    <span style={{
                      color: c.heat_is_swing
                        ? "#1a7f37" : "#c0392b",
                    }}>
                      {c.heat_is_swing === true
                        ? "swing" : c.heat_is_swing === false
                        ? "not" : "?"}
                    </span>{" "}
                    <span className="muted">
                      ({c.heat_confidence || "-"})
                    </span>
                  </div>
                  <div className="muted">
                    {c.heat_reason}
                  </div>
                  <div style={{ marginTop: 3 }}>
                    <b>+ frames:</b>{" "}
                    <span style={{
                      color: c.frames_is_swing
                        ? "#1a7f37" : "#c0392b",
                    }}>
                      {c.frames_is_swing === true
                        ? "swing" : c.frames_is_swing === false
                        ? "not" : "?"}
                    </span>{" "}
                    <span className="muted">
                      ({c.frames_confidence || "-"})
                    </span>
                  </div>
                  <div className="muted">
                    {c.frames_reason}
                  </div>
                  {/* WHICH WAY ROUND THEY STAND.
                      From the same call that judged
                      the swing, so it costs
                      nothing extra -- and it is
                      NOT part of the verdict: a
                      golfer whose handedness could
                      not be read still swung. */}
                  {c.frames_handed && (
                    <div className="muted">
                      stance:{" "}
                      <b>{c.frames_handed}-handed</b>
                    </div>
                  )}
                  {/* AND WHICH ONE WON. The row's
                      own verdict column already
                      shows it, but not next to the
                      two answers it came from --
                      which is the whole point of
                      printing both. */}
                  <div style={{
                    marginTop: 3,
                    color: c.heat_is_swing
                      !== c.frames_is_swing
                      ? "#b45309" : "#555",
                  }}>
                    {c.heat_is_swing !== c.frames_is_swing
                      ? "they disagree — "
                      : "they agree — "}
                    final:{" "}
                    <b>
                      {c.is_swing === true ? "swing"
                        : c.is_swing === false ? "not"
                        : "?"}
                    </b>
                    {c.overruled ? " (overruled by ×ratio)" : ""}
                  </div>
                  {/* AND HOW THE SPLIT WAS
                      SETTLED. The ×ratio decides
                      it, and the number it
                      turned on is the thing the
                      operator will want to argue
                      with. */}
                  {c.tiebreak
                    && c.heat_is_swing !== c.frames_is_swing && (
                    <div className="muted"
                         style={{ marginTop: 2 }}>
                      {c.tiebreak}
                    </div>
                  )}
                </div>
              )}
              {c.verify_saw && c.heat_is_swing === undefined && (
                <div className="tiny muted"
                     style={{ marginTop: 2 }}>
                  judged from {c.verify_saw}
                </div>
              )}
              {c.verify_reason && (
                <div className="muted" style={{
                  maxWidth: 200, marginTop: 2,
                  whiteSpace: "normal", lineHeight: 1.3,
                }}>
                  {c.verify_reason}
                </div>
              )}
              {c.supersede_reason && (
                <div className="muted" style={{
                  maxWidth: 200, marginTop: 2,
                  whiteSpace: "normal", lineHeight: 1.3,
                }}>
                  {c.supersede_reason}
                </div>
              )}
            </td>
            <td style={{ padding: "1px 10px 1px 0" }}>
              {c.start_sec.toFixed(1)}–{c.end_sec.toFixed(1)}s
            </td>
            <td style={{ padding: "1px 10px 1px 0" }}>
              {c.confidence || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
  );
}
