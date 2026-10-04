/**
 * THE SPECIFICITY GUARD.
 *
 * Disabling a button has to win over every variant class, and in this
 * stylesheet that is not automatic. `button.secondary` and
 * `button:disabled` are both (0,1,1); `.btn.secondary` and
 * `.btn:disabled` are both (0,2,0). On a tie the later rule wins, so
 * the moment someone adds a variant below the disabled block, disabled
 * controls in that variant quietly go back to looking pressable while
 * being unpressable — which is worse than either state alone, and
 * invisible to any assertion about the DOM.
 *
 * So this walks the real stylesheet with real elements and checks the
 * paint. It also checks the enabled side, because a rule broad enough
 * to catch every variant is also broad enough to grey out live buttons.
 */
import assert from "node:assert/strict";

import { openApp, rgb } from "../harness.mjs";

export const name = "disabled beats every button variant";

const DISABLED_FILL = rgb("#cbd4e8");
const DISABLED_INK = rgb("#ffffff");

// Everything a <button> is spelled as in this app. `small` and `tiny`
// are sizes rather than variants, but they carry their own padding
// rules and have tripped over the ghost reset before, so they are here.
const VARIANTS = [
  "",
  "btn",
  "secondary",
  "btn secondary",
  "accent",
  "btn accent",
  "danger",
  "btn danger",
  "ghost",
  "btn ghost",
  "ghost danger",
  "chip",
  "small",
  "btn small",
  "small ghost",
  "small secondary",
  "tiny",
];

/** Render one button per variant, in the given state, and read the paint. */
async function paint(ctx, { disabled }) {
  const { page, errors } = await openApp(ctx.browser, { baseUrl: ctx.baseUrl });
  assert.deepEqual(errors, [], "the page threw while rendering");
  const out = await page.evaluate(({ variants, off }) => {
    const host = document.createElement("div");
    host.id = "variant-probe";
    document.body.appendChild(host);
    const res = {};
    for (const v of variants) {
      const b = document.createElement("button");
      b.className = v;
      b.textContent = v || "(bare)";
      if (off) b.disabled = true;
      host.appendChild(b);
      const cs = getComputedStyle(b);
      res[v || "(bare)"] = {
        background: cs.backgroundColor,
        color: cs.color,
        cursor: cs.cursor,
      };
    }
    host.remove();
    return res;
  }, { variants: VARIANTS, off: disabled });
  await page.close();
  return out;
}

export const tests = [
  {
    name: "every variant takes the disabled fill when disabled",
    async fn(ctx) {
      const got = await paint(ctx, { disabled: true });
      for (const [variant, cs] of Object.entries(got)) {
        assert.equal(
          cs.background, DISABLED_FILL,
          `disabled "${variant}" painted ${cs.background}, not the disabled fill`,
        );
        assert.equal(
          cs.color, DISABLED_INK,
          `disabled "${variant}" lettered ${cs.color}, not white`,
        );
        assert.equal(
          cs.cursor, "not-allowed",
          `disabled "${variant}" still offers the pointer cursor`,
        );
      }
    },
  },
  {
    name: "no enabled variant is wearing the disabled fill",
    async fn(ctx) {
      const got = await paint(ctx, { disabled: false });
      for (const [variant, cs] of Object.entries(got)) {
        assert.notEqual(
          cs.background, DISABLED_FILL,
          `enabled "${variant}" is painted with the disabled fill`,
        );
      }
    },
  },
  {
    name: "a disabled ghost gets its padding back",
    async fn(ctx) {
      // A ghost is flush-left with no horizontal padding, which is right
      // until it is disabled: its label goes white like every other
      // disabled control, and white on linen is nothing at all. It needs
      // the capsule behind it, and a capsule needs padding.
      const { page } = await openApp(ctx.browser, { baseUrl: ctx.baseUrl });
      const pad = await page.evaluate(() => {
        const b = document.createElement("button");
        b.className = "small ghost";
        b.textContent = "Delete";
        b.disabled = true;
        document.body.appendChild(b);
        const cs = getComputedStyle(b);
        const v = [cs.paddingLeft, cs.paddingRight];
        b.remove();
        return v;
      });
      await page.close();
      for (const p of pad) {
        assert.ok(
          parseFloat(p) > 0,
          `a disabled ghost has ${p} side padding, so its white label sits on the page`,
        );
      }
    },
  },
];
