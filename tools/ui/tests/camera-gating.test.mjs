/**
 * A camera whose agent is down must not offer controls that have to
 * reach it, and must still offer the ones the backend owns.
 *
 * The split under test is NOT "camera controls vs the rest". It is
 * whether the press has to reach the Pi right now. Capture, Watch live,
 * Trigger zones, Focus mode, Read profile and the green Measure all do.
 * Aim and Today's pin look like they do and do not: both work off frames
 * from an already-uploaded clip, so they keep working with the rig off
 * the air — which is exactly when you want to be doing that work. If
 * someone later wires either of those to a live frame, this file should
 * start failing.
 */
import assert from "node:assert/strict";

import { ADMIN_ROUTES, ADMIN_STORAGE } from "../fixtures/cameras.mjs";
import { openApp, rgb } from "../harness.mjs";

export const name = "camera controls gate on the heartbeat";

const DISABLED_FILL = rgb("#cbd4e8");

/** label -> state, for one camera card, keyed by its leading #id. */
async function cards(ctx) {
  const { page, errors } = await openApp(ctx.browser, {
    baseUrl: ctx.baseUrl,
    path: "/admin/cameras",
    routes: ADMIN_ROUTES,
    storage: ADMIN_STORAGE,
    viewport: { width: 1400, height: 1100 },
  });
  assert.deepEqual(errors, [], "the page threw while rendering");
  const byId = await page.$$eval(".card", (roots) => {
    const out = {};
    for (const root of roots) {
      const m = (root.textContent || "").trim().match(/^#(\d+)\b/);
      if (!m) continue;
      const btns = {};
      for (const btn of root.querySelectorAll("button")) {
        const label = (btn.textContent || "").trim().replace(/\s+/g, " ");
        const cs = getComputedStyle(btn);
        btns[label] = { disabled: btn.disabled, background: cs.backgroundColor };
      }
      out[m[1]] = btns;
    }
    return out;
  });
  await page.close();
  return byId;
}

/** Find one button by a substring of its label. */
function find(btns, needle) {
  const key = Object.keys(btns).find((k) => k.includes(needle));
  assert.ok(
    key,
    `no button matching "${needle}" — the card had: ${Object.keys(btns).join(" | ")}`,
  );
  return { label: key, ...btns[key] };
}

function assertOff(btns, needle) {
  const b = find(btns, needle);
  assert.equal(b.disabled, true, `"${b.label}" should be disabled`);
  // AND the pixel, not just the attribute. The regression this suite
  // was written for had the attribute right and the fill wrong: a
  // disabled .secondary kept its live blue outline because
  // `button.secondary` and `button:disabled` tie on specificity and the
  // variant was written later. It looked pressable and was not.
  assert.equal(
    b.background, DISABLED_FILL,
    `"${b.label}" is disabled but still painted ${b.background}`,
  );
}

function assertOn(btns, needle) {
  const b = find(btns, needle);
  assert.equal(b.disabled, false, `"${b.label}" should still be usable`);
  assert.notEqual(
    b.background, DISABLED_FILL,
    `"${b.label}" is enabled but painted with the disabled fill`,
  );
}

export const tests = [
  {
    name: "a down camera loses everything that has to reach the Pi",
    async fn(ctx) {
      const byId = await cards(ctx);
      assertOff(byId["1"], "Capture");
      assertOff(byId["1"], "Trigger zones");
      assertOff(byId["1"], "Watch live");
      assertOff(byId["1"], "Read profile");
      assertOff(byId["2"], "Measure");      // green-only
      assertOff(byId["3"], "Focus mode");   // Pi-only
    },
  },
  {
    name: "a down camera keeps everything the backend owns",
    async fn(ctx) {
      const byId = await cards(ctx);
      for (const label of [
        "Pause triggering", "Disable", "Edit", "Rotate token", "Delete",
      ]) assertOn(byId["1"], label);
      // Frames come from an uploaded clip, not the device.
      assertOn(byId["1"], "Aim: calibrate");
      assertOn(byId["1"], "pin & ball area");
    },
  },
  {
    name: "a live camera has nothing gated",
    async fn(ctx) {
      const byId = await cards(ctx);
      for (const label of [
        "Watch live", "Focus mode", "Measure", "Aim: calibrate",
        "pin & ball area", "Disable", "Edit", "Rotate token",
      ]) assertOn(byId["4"], label);
    },
  },
  {
    name: "a gated control says why in its tooltip",
    async fn(ctx) {
      const { page } = await openApp(ctx.browser, {
        baseUrl: ctx.baseUrl,
        path: "/admin/cameras",
        routes: ADMIN_ROUTES,
        storage: ADMIN_STORAGE,
        viewport: { width: 1400, height: 1100 },
      });
      const titles = await page.$$eval("button", (btns) => btns
        .filter((b) => b.disabled && /Capture|Trigger zones|Read profile/.test(b.textContent))
        .map((b) => b.title));
      await page.close();
      assert.ok(titles.length >= 3, `expected 3+ gated controls, saw ${titles.length}`);
      for (const t of titles) {
        assert.match(
          t, /last called in|down/,
          "a gated control went dead without saying why",
        );
      }
    },
  },
];
