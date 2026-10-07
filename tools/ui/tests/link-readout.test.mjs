/**
 * The uplink panel is the one that replaces an SSH session, so the
 * thing it must never do is look the same in all three states.
 *
 * Weak signal, a data cap and a failing power chain all present as
 * "uploads are slow". The runbook's causal chain is why:
 *
 *     weak signal -> modem transmits harder -> 5V sags -> USB resets
 *
 * A location problem PRESENTS as a power problem. So the card earns
 * its place only if a bad mount and a busy tower are visibly different
 * at a glance — the pill colour — and if the sentence under the
 * numbers names which one it is.
 *
 * The backend computes level/summary/verdict (tools/link_status_test.py
 * covers that); this file is about whether an operator can tell them
 * apart on the page.
 */
import assert from "node:assert/strict";

import { ADMIN_STORAGE } from "../fixtures/cameras.mjs";
import { openApp, rgb } from "../harness.mjs";

export const name = "the uplink panel separates the three faults";

const naive = (msAgo) =>
  new Date(Date.now() - msAgo).toISOString().replace("Z", "");

const BAD_FILL = rgb("#dc2626");
const WARN_FILL = rgb("#f59e0b");

function camera(id, link) {
  return {
    id,
    kind: "ip",
    assigned_role: "tee",
    assigned_hole: 8,
    course_id: 1,
    course_name: "Rivertowne Country Club",
    name: `GR Cam ${id}`,
    enabled: true,
    triggering_enabled: true,
    last_seen_at: naive(30e3),
    firmware_version: "tee-0.1.0+b5c6165",
    stream_model: "XNV-6080R",
    tee_zones: { boxes: [] },
    link,
  };
}

/** Everything the uplink pill and panel say, per card. */
async function read(ctx, cameras) {
  const { page, errors } = await openApp(ctx.browser, {
    baseUrl: ctx.baseUrl,
    path: "/admin/cameras",
    routes: {
      "/api/admin/cameras": cameras,
      "/api/admin/courses": [{ id: 1, name: "Rivertowne Country Club" }],
    },
    storage: ADMIN_STORAGE,
    viewport: { width: 1400, height: 1200 },
  });
  assert.deepEqual(errors, [], "the page threw while rendering");
  const out = await page.$$eval(".card", (roots) => {
    const res = {};
    for (const root of roots) {
      const m = (root.textContent || "").trim().match(/^#(\d+)\b/);
      if (!m) continue;
      const pill = [...root.querySelectorAll("span")].find(
        (s) => (s.textContent || "").includes("\u{1F4F6}"),
      );
      res[m[1]] = {
        pill: pill ? (pill.textContent || "").trim() : null,
        // THE COMPUTED FILL, not the class. A pill can carry the right
        // words in the wrong colour and read as healthy at a glance,
        // which is the only way this card is ever actually read.
        fill: pill ? getComputedStyle(pill).backgroundColor : null,
        title: pill ? pill.getAttribute("title") : null,
        text: (root.innerText || "").replace(/\s+/g, " "),
      };
    }
    return res;
  });
  await page.close();
  return out;
}

const GOOD = {
  level: "ok", summary: "signal good",
  verdict: "Signal is fine, so slow uploads here are a cap, a carrier "
    + "throttle, or congestion — not the mount.",
  rsrp: -78, rsrq: -9, sinr: 22, rssi: -60, band: "B4", carrier: "Telus",
  usage: "1.2 GB", usb_resets: 0, uptime_seconds: 7200,
  gateway: "192.168.5.1", updated_at: naive(60e3),
};

const WEAK = {
  level: "bad", summary: "signal poor",
  verdict: "Weak signal. An antenna or a different mount position, not "
    + "another modem.",
  rsrp: -115, rsrq: -18, sinr: -3, rssi: -99, band: "B12", carrier: "Telus",
  usage: "0.4 GB", usb_resets: 0, uptime_seconds: 3600,
  gateway: "192.168.5.1", updated_at: naive(60e3),
};

const RESETTING = {
  level: "warn", summary: "signal good",
  verdict: "Signal is fine... The USB bus has reset 14 time(s) since boot "
    + "— if that climbs during an upload, the modem really is dropping off "
    + "and the power chain is next.",
  rsrp: -79, rsrq: -9, sinr: 21, rssi: -61, band: "B4", carrier: "Telus",
  usage: "1.1 GB", usb_resets: 14, uptime_seconds: 5400,
  gateway: "192.168.5.1", updated_at: naive(60e3),
};

export const tests = [
  {
    name: "the three faults do not look alike",
    async fn(ctx) {
      const got = await read(ctx, [
        camera(1, GOOD), camera(2, WEAK), camera(3, RESETTING),
      ]);
      assert.notEqual(got["2"].fill, got["1"].fill,
        "a weak-signal rig is the same colour as a healthy one");
      assert.equal(got["2"].fill, BAD_FILL,
        `a poor signal should be red, got ${got["2"].fill}`);
      assert.equal(got["3"].fill, WARN_FILL,
        `a re-enumerating modem should be amber, got ${got["3"].fill}`);
      assert.notEqual(got["3"].fill, BAD_FILL,
        "amber and red must stay distinguishable");
    },
  },
  {
    name: "the verdict reaches the page, not just the payload",
    async fn(ctx) {
      const got = await read(ctx, [camera(1, GOOD), camera(2, WEAK)]);
      // The sentence is the whole feature: the numbers alone send
      // people to swap the modem whichever way they read.
      assert.match(got["1"].text, /not the mount/i,
        "a healthy rig does not say that the mount is ruled out");
      assert.match(got["2"].text, /antenna/i,
        "a weak rig does not send anyone to the antenna");
      assert.doesNotMatch(got["1"].text, /antenna/i,
        "a healthy rig is telling people to fit an antenna");
    },
  },
  {
    name: "the numbers an operator would read are all there",
    async fn(ctx) {
      const got = await read(ctx, [camera(1, GOOD)]);
      for (const want of [/RSRP/, /-78/, /SINR/, /22/, /B4/, /Telus/,
                          /1\.2 GB/, /USB resets/]) {
        assert.match(got["1"].text, want,
          `the uplink panel is missing ${want}`);
      }
    },
  },
  {
    name: "a rig that reported nothing shows nothing",
    async fn(ctx) {
      // THE CONTROL. Every assertion above is about a pill being
      // present and coloured; without this, a card that drew the pill
      // unconditionally would pass all of them. And a permanent empty
      // panel on every Pi without a modem is how a diagnostic card
      // stops being read.
      const got = await read(ctx, [camera(1, GOOD), camera(2, null)]);
      assert.ok(got["1"].pill, "the control is broken: no pill on a rig "
        + "that did report an uplink");
      assert.equal(got["2"].pill, null,
        "a rig with no uplink reading still drew an uplink pill");
      assert.doesNotMatch(got["2"].text, /RSRP/,
        "a rig with no uplink reading still drew the panel");
    },
  },
];
