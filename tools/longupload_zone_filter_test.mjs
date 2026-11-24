#!/usr/bin/env node
/*
 * The Long upload / Test Cut list filters by trigger zone.
 *
 * ASKED FOR: "I do want you to add the ability to filter by
 * triggerzone on the longupload (test cut) page."
 *
 * TWO THINGS THIS HAS TO GET RIGHT, both already paid for once on the
 * Production page:
 *
 *   ZONE 0 IS A ZONE. The rig counts from 0 and the editor labels
 *   from 1, so the box printed "Zone 1" is stored as 0 — and
 *   `if (zone)` drops it and silently widens the filter to
 *   everything, which then looks like a filter that found a lot.
 *
 *   THE OPTIONS MUST NOT SHRINK. They are built from the rows that
 *   come back, and once the list is narrowed to one zone the reply
 *   only holds that zone. Rebuilding the options from it leaves the
 *   select holding the value already chosen, with no way back.
 *
 *   node tools/longupload_zone_filter_test.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(60)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

const { growZoneOptions, zoneName, zoneWords } =
  await import(join(ROOT, "frontend/src/zones.js"));

console.log("\n1. the off-by-one, in one place");
check("stored 0 is the box printed Zone 1", zoneName(0), "Zone 1");
check("stored 2 is the box printed Zone 3", zoneName(2), "Zone 3");
check("a label rides along", zoneName(2, "middle left"),
      "middle left (Zone 3)");
check("and zoneWords agrees with it",
      zoneWords({ zone: 2, zone_label: "middle left" }),
      "middle left (Zone 3)");
check("no zone is no name", zoneName(null), null);
check("...and an empty string is not zone 0", zoneName(""), null);

console.log("\n2. the options grow from the rows, and keep zone 0");
const seen = new Map();
const rows = (...zs) => zs.map((z) => ({
  source: { trigger_detail: z == null ? null : { zone: z } } }));
let opts = growZoneOptions(seen, rows(0, 2, 2));
check("zone 0 is in the list", opts, [["0", ""], ["2", ""]]);
check("sorted numerically, not as strings",
      growZoneOptions(seen, rows(10, 1)).map(([i]) => i),
      ["0", "1", "2", "10"]);

console.log("\n3. a narrowed page does not shrink the list");
check("nothing new returns null, so the caller skips the setState",
      growZoneOptions(seen, rows(2)), null);
check("and the Map still holds every zone ever seen",
      Array.from(seen.keys()).sort(), ["0", "1", "10", "2"]);

console.log("\n4. a label arriving late is taken");
const seen2 = new Map();
growZoneOptions(seen2, rows(1));
const grown = growZoneOptions(seen2, [
  { source: { trigger_detail: { zone: 1, zone_label: "back left" } } }]);
check("the unlabelled entry is upgraded", grown, [["1", "back left"]]);

console.log("\n5. rows with no trigger at all are skipped, not crashed on");
check("null detail", growZoneOptions(new Map(), rows(null)), null);
check("no rows", growZoneOptions(new Map(), null), null);
check("a detail with no zone",
      growZoneOptions(new Map(),
                      [{ source: { trigger_detail: { kind: "audio" } } }]),
      null);

console.log("\n6. the page asks the SERVER, not the loaded rows");
const JSX = readFileSync(
  join(ROOT, "frontend/src/pages/AdminLongUpload.jsx"), "utf8");
check("the zone goes into the request", JSX.includes("zone: zoneFilter,"));
check("and a change refetches", JSX.includes("[triggerKind, zoneFilter]"));
check("the select only appears once a zone has fired",
      JSX.includes("{zoneOpts.length > 0 && ("));
check("options are grown, never rebuilt",
      JSX.includes("growZoneOptions(zonesSeenRef.current, list)"));
check("the list renders when a filter is on but empty",
      JSX.includes('|| triggerKind || zoneFilter !== ""'));

console.log("\n7. both screens share one definition");
const PROD = readFileSync(
  join(ROOT, "frontend/src/pages/AdminProduction.jsx"), "utf8");
check("Production imports it",
      PROD.includes('from "../zones"'));
check("...and no longer defines its own",
      PROD.includes("function zoneWords(detail) {"), false);
check("Long upload imports it too",
      JSX.includes('from "../zones"'));

console.log();
if (failed.length) {
  console.log(`FAILED (${failed.length}):`);
  failed.forEach((f) => console.log("  -", f));
  process.exit(1);
}
console.log("all ok");
