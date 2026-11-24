/*
 * TRIGGER ZONES, NAMED THE WAY THE OPERATOR SEES THEM.
 *
 * THE OFF-BY-ONE IS THE WHOLE REASON THIS FILE EXISTS. The rig counts
 * zones from 0 and the editor labels them from 1, so the stored index
 * 2 is the box printed "Zone 3" on the screen the operator drew it on.
 * An event that says "middle left (zone 2)" is naming the INDEX, and
 * anyone reading it goes and looks at the box marked Zone 2 — which is
 * a different piece of grass.
 *
 * It lives here rather than in a page because two screens now filter
 * by zone — Production and Long upload / Test Cut — and two copies of
 * a ±1 convention is two chances to drift apart. A filter that labels
 * its options differently from the one next door is worse than no
 * filter: both look authoritative.
 */

/** The on-screen name for a trigger_detail, or null. */
export function zoneWords(detail) {
  if (!detail) return null;
  const { zone, zone_label: label } = detail;
  const onScreen = zone == null ? null : `Zone ${Number(zone) + 1}`;
  if (label && onScreen) return `${label} (${onScreen})`;
  return label || onScreen || null;
}

/** The on-screen name for a bare stored index. */
export function zoneName(idx, label) {
  if (idx == null || idx === "") return null;
  const onScreen = `Zone ${Number(idx) + 1}`;
  return label ? `${label} (${onScreen})` : onScreen;
}

/**
 * Fold the zones present in a page of rows into a sorted option list.
 *
 * `seen` is a Map kept by the caller across pages: the list is
 * paginated and the options must not shrink when the next page
 * happens to hold fewer kinds of zone than the last one.
 *
 * Returns the new option list when it grew, or null when nothing
 * changed — so a caller can skip a setState and the re-render with it.
 *
 * ZONE 0 IS A ZONE. `if (!d.zone)` drops the box labelled "Zone 1" on
 * screen and silently widens the filter to everything.
 */
export function growZoneOptions(seen, rows, detailOf) {
  if (!Array.isArray(rows)) return null;
  let grew = false;
  for (const r of rows) {
    const d = detailOf ? detailOf(r) : r?.source?.trigger_detail;
    if (!d || d.zone == null) continue;
    const k = String(d.zone);
    const label = d.zone_label || "";
    const prev = seen.get(k);
    // A later row may carry a label where an earlier one had none.
    if (prev === undefined || (!prev && label)) {
      seen.set(k, label);
      grew = true;
    }
  }
  if (!grew) return null;
  return Array.from(seen.entries())
    .sort((a, b) => Number(a[0]) - Number(b[0]));
}
