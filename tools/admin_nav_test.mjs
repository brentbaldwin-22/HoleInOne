#!/usr/bin/env node
/*
 * Every admin page is reachable FROM every other one, and offers a way
 * to sign in.
 *
 * THE ONE-WAY DOOR. The Production Flow link was added to fifteen
 * admin pages and the Production Flow page itself was built without
 * any nav at all -- so an operator could get in and not out, and a
 * signed-out one landing on its URL got a sentence telling them to
 * "sign in on an admin page first" with no link to one. Every other
 * page in the console answers that with a Sign in button.
 *
 * Checked by reading the pages, because this is exactly the kind of
 * thing that is obvious in the browser and invisible in a diff.
 *
 *   node tools/admin_nav_test.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PAGES = join(ROOT, "frontend/src/pages");
const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(62)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

// Derived rather than listed, so a page added later is covered.
//
// TWO FAMILIES, NOT ONE. Most admin pages carry the production
// console's nav -- Dashboard, Players, Courses, Upload, Production,
// Clips, Broadcast, Cameras. Claims and Reviews carry a deliberately
// short one and reach the rest through Dashboard. Requiring every nav
// to carry every link would be a test demanding the two be merged,
// which is a design opinion, not a defect. So a console page is one
// whose nav reaches /admin/production, and those are the ones that
// must also reach the flow built beside it.
const files = readdirSync(PAGES).filter((f) => /^Admin.*\.jsx$/.test(f));
const src = Object.fromEntries(
  files.map((f) => [f, readFileSync(join(PAGES, f), "utf8")]));
const withNav = files.filter((f) => src[f].includes('className="nav"'));
const console_ = withNav.filter(
  (f) => src[f].includes('to="/admin/production"'));

console.log("\n1. the console's pages carry its nav");
check("more than a handful of them do", console_.length >= 10);
check("Production Flow is one of them",
      console_.includes("AdminProductionFlow.jsx"));
// `Make clips by hand` is the third of them: Dashboard, back to
// Production Flow, and the page itself. It is opened FROM the flow to
// do one job on one recording, so a full console nav on it would be a
// row of links away from the thing it was opened to do.
check("and the short-nav pages are not counted as console pages",
      withNav.filter((f) => !console_.includes(f)).sort(),
      ["AdminClaims.jsx", "AdminFlowManual.jsx", "AdminReviews.jsx"]);
// AND EACH ONE REACHES THE REST THROUGH DASHBOARD, which is the
// bargain that makes a short nav acceptable rather than a dead end.
for (const f of withNav.filter((x) => !console_.includes(x))) {
  check(`  ${f} still reaches Dashboard`,
        src[f].includes('to="/admin"'));
}

console.log("\n2. every console page links to Production Flow");
for (const f of console_) {
  check(`  ${f}`, src[f].includes('to="/admin/production-flow"'));
}

console.log("\n3. ...and Production Flow links back out");
const flow = src["AdminProductionFlow.jsx"];
for (const to of ["/admin", "/admin/production", "/admin/produced-clips",
                  "/admin/broadcast-clips", "/admin/cameras"]) {
  check(`  ${to}`, flow.includes(`to="${to}"`));
}
check("and marks itself as the page you are on",
      /production-flow"\s*className="active"|production-flow"\n\s*className="active"/
        .test(flow));

console.log("\n4. a signed-out operator is given a way in, not prose");
for (const f of withNav) {
  // THE RENDER GUARD, not the first mention. Every one of these pages
  // also has `if (!adminPassword) return;` inside a loader, and a
  // window anchored on that finds no sign-in and reports a page that
  // is perfectly fine as broken.
  const m = /if\s*\(!adminPassword\)\s*\{\s*\n?\s*return\s*\(/
    .exec(src[f]);
  if (!m) continue;
  const block = src[f].slice(m.index, m.index + 900);
  // Either answer is a way in: a link to the sign-in page, or the
  // password field itself rendered in place.
  check(`  ${f}`,
        block.includes('to="/admin"') || /type="password"/.test(block));
}

console.log("\n5. and every page reads the password from the same place");
{
  // THE BUG THIS CAUGHT. Production Flow read
  // localStorage["adminPassword"] -- a key nothing writes -- so it was
  // signed out forever while every page beside it was signed in, and
  // its Sign in button led to the Dashboard the operator was already
  // signed into. A loop with no error in it anywhere.
  const KNOWN = ["golfreelz.adminPassword", "parone.adminPassword"];
  const re = /localStorage\.getItem\(\s*(?:"([^"]*)"|'([^']*)')\s*\)/g;
  for (const f of files) {
    let m;
    const bad = [];
    while ((m = re.exec(src[f])) !== null) {
      const k = m[1] ?? m[2];
      if (/admin|password|pw/i.test(k) && !KNOWN.includes(k)) bad.push(k);
    }
    if (!/admin/i.test(src[f])) continue;
    check(`  ${f} invents no key of its own`, bad, []);
  }
  const helper = readFileSync(
    join(ROOT, "frontend/src/adminAuth.js"), "utf8");
  check("the shared reader knows both spellings",
        KNOWN.every((k) => helper.includes(k)));
  check("...and survives storage that throws", /catch/.test(helper));
}

console.log(`\n${failed.length
  ? `${failed.length} FAILED:\n  ${failed.join("\n  ")}` : "all ok"}`);
process.exit(failed.length ? 1 : 0);
