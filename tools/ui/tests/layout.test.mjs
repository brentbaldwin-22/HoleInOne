/**
 * NOTHING SCROLLS SIDEWAYS, AND NOTHING THROWS.
 *
 * The home page once scrolled to 2192px at every viewport, phone
 * included, because a class meant for a numeral was being worn by a
 * whole block and carried flex-shrink: 0 with it. Five unshrinkable
 * columns pushed the row past the page and the page past the screen.
 * Nobody noticed on a desktop, where you only see it if you happen to
 * scroll right, and it made the site unusable on the device most
 * golfers would open it on.
 *
 * The check is cheap and the failure mode is silent, which is exactly
 * the trade a test suite is for. The offending element is named in the
 * message, because "the page is 2192px wide" on its own sends you
 * hunting through a stylesheet.
 */
import assert from "node:assert/strict";

import { ADMIN_ROUTES, ADMIN_STORAGE } from "../fixtures/cameras.mjs";
import { openApp } from "../harness.mjs";

export const name = "no horizontal overflow";

const VIEWPORTS = [
  { width: 390, height: 844, label: "phone" },
  { width: 1180, height: 900, label: "desktop" },
];

const ROUTES = [
  { path: "/", label: "home" },
  { path: "/signup", label: "signup" },
  { path: "/admin/cameras", label: "cameras", admin: true },
];

export const tests = VIEWPORTS.flatMap((vp) => ROUTES.map((r) => ({
  name: `${r.label} fits ${vp.label} (${vp.width}px)`,
  async fn(ctx) {
    const { page, errors } = await openApp(ctx.browser, {
      baseUrl: ctx.baseUrl,
      path: r.path,
      viewport: { width: vp.width, height: vp.height },
      routes: r.admin ? ADMIN_ROUTES : {},
      storage: r.admin ? ADMIN_STORAGE : {},
    });
    const report = await page.evaluate((vw) => {
      const doc = document.documentElement;
      const over = [];
      for (const el of doc.querySelectorAll("*")) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.right > vw + 1) {
          over.push(
            `<${el.tagName.toLowerCase()} class="${el.className}">`
            + ` reaches ${Math.round(rect.right)}px`,
          );
        }
      }
      return { scrollWidth: doc.scrollWidth, over: over.slice(0, 5) };
    }, vp.width);
    await page.close();

    assert.deepEqual(errors, [], `${r.path} threw while rendering`);
    assert.ok(
      report.scrollWidth <= vp.width,
      `${r.path} scrolls to ${report.scrollWidth}px in a ${vp.width}px window.\n`
      + "  past the right edge: " + (report.over.join("\n  ") || "(nothing — "
      + "a margin or a negative offset rather than an element)"),
    );
  },
})));
