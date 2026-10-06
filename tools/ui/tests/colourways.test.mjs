/**
 * Picking a lockup moves the BANDS and nothing else.
 *
 * The whole design is in the second half of that sentence. `--primary`
 * used to be `var(--band-1)` — true while there was one logo, and a bug
 * the moment there were four, because picking the green lockup would
 * have turned every button on the site green. `--warn` rode on band 5
 * the same way, so a status would have changed colour because somebody
 * liked a different picture.
 *
 * Both are pinned in styles.css now, and this is what keeps them
 * pinned: the next person to add a colourway will copy an existing
 * block, and nothing else in the codebase would notice if they put
 * `--primary` in it.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { openApp } from "../harness.mjs";

export const name = "colourways move the bands only";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "..", "..", "..", "frontend");

const WAYS = ["sunset", "fairway", "sky", "ember"];

// What must be identical on every colourway. These are furniture: a
// button, a warning, the paper, the ink, the text.
const PINNED = [
  "--primary", "--accent", "--warn", "--danger", "--ok",
  "--ink", "--linen", "--paper", "--rule", "--disabled-bg",
];

/** Read the six bands and the pinned tokens under one direction. */
async function tokensFor(ctx, direction) {
  const { page, errors } = await openApp(ctx.browser, { baseUrl: ctx.baseUrl });
  assert.deepEqual(errors, [], "the page threw while rendering");
  const out = await page.evaluate(({ dir, pinned }) => {
    document.documentElement.setAttribute("data-direction", dir);
    const cs = getComputedStyle(document.documentElement);
    const read = (n) => cs.getPropertyValue(n).trim();
    const bands = {};
    for (let i = 1; i <= 6; i += 1) bands[`--band-${i}`] = read(`--band-${i}`);
    const fixed = {};
    for (const n of pinned) fixed[n] = read(n);
    return { bands, fixed };
  }, { dir: direction, pinned: PINNED });
  await page.close();
  return out;
}

export const tests = [
  {
    name: "every colourway defines all six bands, and they differ",
    async fn(ctx) {
      const seen = new Map();
      for (const way of WAYS) {
        const { bands } = await tokensFor(ctx, way);
        for (const [name, v] of Object.entries(bands)) {
          assert.match(
            v, /^#|^rgb/,
            `${way} leaves ${name} unset — the ramp has a hole in it`,
          );
        }
        const sig = Object.values(bands).join(",");
        const clash = [...seen.entries()].find(([, s]) => s === sig);
        assert.ok(
          !clash,
          `${way} has the same six bands as ${clash?.[0]} — one of the `
          + "[data-direction] blocks is missing or duplicated",
        );
        seen.set(way, sig);
      }
    },
  },
  {
    name: "the furniture is identical on all four",
    async fn(ctx) {
      const base = await tokensFor(ctx, WAYS[0]);
      for (const way of WAYS.slice(1)) {
        const got = await tokensFor(ctx, way);
        assert.deepEqual(
          got.fixed, base.fixed,
          `${way} moved a token that is not a band. Buttons, warnings and `
          + "the paper belong to the site, not to the logo.",
        );
      }
    },
  },
  {
    name: "the primary stays the blue buttons are drawn in",
    async fn(ctx) {
      // Named explicitly rather than only compared, so a change that
      // moved all four together still fails here.
      for (const way of WAYS) {
        const { fixed } = await tokensFor(ctx, way);
        // A custom property computes to its literal text, not to an
        // rgb() triple — getPropertyValue on --primary gives "#0047e7".
        assert.equal(
          fixed["--primary"], "#0047e7",
          `--primary is ${fixed["--primary"]} under ${way}`,
        );
      }
    },
  },
  {
    name: "no colourway block declares anything but bands",
    async fn() {
      // The browser cannot see this: a block that sets --primary to the
      // value it already has is invisible to a computed-style check and
      // a live grenade for whoever edits it next.
      const css = await readFile(join(frontend, "src", "styles.css"), "utf8");
      for (const way of WAYS.slice(1)) {          // sunset lives in :root
        const m = css.match(
          new RegExp(`\\[data-direction="${way}"\\]\\s*\\{([^}]*)\\}`),
        );
        assert.ok(m, `styles.css has no block for ${way}`);
        const declared = [...m[1].matchAll(/(--[\w-]+)\s*:/g)].map((x) => x[1]);
        const stray = declared.filter((n) => !/^--band-[1-6]$/.test(n));
        assert.deepEqual(
          stray, [],
          `the ${way} block declares ${stray.join(", ")} — a colourway is `
          + "six band tokens and nothing else",
        );
        assert.equal(declared.length, 6, `${way} declares ${declared.length}`);
      }
    },
  },
  {
    name: "every colourway has both its PNGs",
    async fn(ctx) {
      const { page } = await openApp(ctx.browser, { baseUrl: ctx.baseUrl });
      const missing = [];
      for (const way of WAYS) {
        for (const f of [`${way}.png`, `${way}-mark.png`]) {
          // eslint-disable-next-line no-await-in-loop
          const ok = await page.evaluate(
            (u) => fetch(u, { method: "HEAD" }).then((r) => r.ok, () => false),
            `/logos/${f}`,
          );
          if (!ok) missing.push(f);
        }
      }
      await page.close();
      assert.deepEqual(
        missing, [],
        "a colourway is listed with no artwork behind it: " + missing.join(", "),
      );
    },
  },
];
