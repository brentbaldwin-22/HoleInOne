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

// ---------------------------------------------------------------------
// The card has to show BOTH halves of the pipeline.
//
// A tee set to 1920x1080 read 1920x1080 on this card, in its config,
// and in the camera's own web UI — and delivered 1280x720 clips,
// because upload_scale_height was still 720 from when the link was the
// thing being optimised. Every number on the page agreed with every
// other one and all of them were about what the camera SENDS, never
// about what the clip is cut down to before it crosses the link.
tests.push({
  name: "a camera that downscales before upload says so",
  async fn(ctx) {
    const { page, errors } = await openApp(ctx.browser, {
      baseUrl: ctx.baseUrl,
      path: "/admin/cameras",
      routes: ADMIN_ROUTES,
      storage: ADMIN_STORAGE,
      viewport: { width: 1400, height: 1400 },
    });
    assert.deepEqual(errors, [], "the page threw while rendering");
    const byId = await page.$$eval(".card", (roots) => {
      const out = {};
      for (const root of roots) {
        const text = (root.textContent || "").replace(/\s+/g, " ").trim();
        const m = text.match(/^#(\d+)\b/);
        if (m) out[m[1]] = text;
      }
      return out;
    });
    await page.close();

    assert.match(
      byId["1"], /Uploads as:? ?720p/,
      "the card never says what the clip is reduced to",
    );
    assert.match(
      byId["1"], /scaled to 720p before upload/,
      "a camera sending 1080 and uploading 720 does not flag it",
    );
    // And the control: a camera keeping what it sends must NOT warn, or
    // the warning means nothing. It has to be #2 — another IP camera —
    // because the STREAM block only renders for kind "ip" and asserting
    // against a Pi card means asserting against a row that was never
    // going to be there.
    assert.match(byId["2"], /Stream/, "the control card has no stream block");
    assert.doesNotMatch(
      byId["2"], /scaled to .* before upload/,
      "a camera that keeps full size is being warned about anyway",
    );
  },
});
