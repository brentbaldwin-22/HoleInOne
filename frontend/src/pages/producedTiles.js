/*
 * WHAT THE "DRAFT PRODUCED" COLUMN SHOWS.
 *
 * Two queries feed the clip columns -- one for stage "draft", one for
 * stage "final" -- and approving a clip moves its row from the first
 * to the second. So the draft tile VANISHED at the moment of
 * approval, and a row whose swing had gone all the way through read
 * "1 cut · 0 drafts · 1 final": as though the draft had never existed
 * and the final had arrived from nowhere. The middle of the process
 * was the one part of it the page could not show.
 *
 * Approving is not un-producing. This column means "was produced",
 * and `final` is a subset of it rather than somewhere a clip moves
 * away to.
 *
 * ITS OWN FILE SO IT CAN BE TESTED. The duplicate case below is a
 * race -- not something that shows up while clicking around -- and it
 * surfaces as the same swing twice in one cell, which reads as a
 * data bug rather than a display one.
 */

/**
 * The tiles for the produced column: every draft, plus every approved
 * clip that is not already among them.
 *
 * DEDUPLICATED BY ID, because the two fetches are not atomic. A clip
 * approved in the moment between them comes back in BOTH -- as a
 * draft from the first query and as a final from the second -- and
 * concatenating blind puts the same swing in the cell twice.
 *
 * DRAFTS FIRST, THEN THE APPROVED ONES, and within each the order the
 * server sent. The column is read top to bottom for what still needs
 * attention, and a clip that has been signed off does not.
 *
 * Returns a new array; neither argument is mutated.
 */
export function producedTiles(drafts, finals) {
  const d = Array.isArray(drafts) ? drafts : [];
  const f = Array.isArray(finals) ? finals : [];
  const seen = new Set();
  const out = [];
  for (const c of d) {
    // A LIST THAT ALREADY REPEATS ITSELF is not made better by
    // passing the repeat through: the id is the identity here, and
    // one tile per clip is the whole contract.
    if (c && !seen.has(c.id)) { seen.add(c.id); out.push(c); }
  }
  for (const c of f) {
    if (c && !seen.has(c.id)) { seen.add(c.id); out.push(c); }
  }
  return out;
}
