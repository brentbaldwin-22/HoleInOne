/**
 * A signed-in admin stays signed in unless the SERVER says otherwise.
 *
 * `authed` started false on every page load, and only the first
 * successful fetch let you in. So the backend being slow to wake — a
 * timeout, a 502, a cold start — put you back at the password box with
 * a perfectly good password still in localStorage. That is what "it
 * keeps logging me out" was: not a session expiring, a transient error
 * being read as a rejection.
 *
 * The distinction under test is narrow and worth stating: 401 means the
 * password is wrong and must sign you out AND clear it, so a bad one
 * cannot loop. Every other failure leaves you where you were.
 */
import assert from "node:assert/strict";

import { openApp } from "../harness.mjs";

export const name = "the admin session survives a bad request";

const PW = { "golfreelz.adminPassword": "test-password" };

const STATS = {
  participants: { total: 0, day: 0, week: 0, month: 0 },
  revenue_cents: { total: 0, day: 0, week: 0, month: 0 },
  clips_by_status: {}, by_course: [],
};

/** Open /admin with every call answered by `status`. */
async function openAdmin(ctx, { status, storage = PW }) {
  const { page, errors } = await openApp(ctx.browser, {
    baseUrl: ctx.baseUrl,
    path: "/admin",
    storage,
    viewport: { width: 1180, height: 1000 },
    respond: (url) => {
      if (status !== 200) return { status, body: { detail: "nope" } };
      if (url.includes("/stats")) return { body: STATS };
      return null;                       // fall through to the default []
    },
  });
  assert.deepEqual(errors, [], "the page threw while rendering");
  const state = await page.evaluate(() => ({
    text: document.body.innerText,
    stored: localStorage.getItem("golfreelz.adminPassword"),
  }));
  await page.close();
  return state;
}

// Case-insensitive throughout: the nav and the buttons are uppercased
// by CSS and innerText reports what is rendered, not what is in the JSX.
const signedOut = (s) => /sign in/i.test(s.text) && /admin password/i.test(s.text);

export const tests = [
  {
    name: "a stored password lands you straight in the console",
    async fn(ctx) {
      const s = await openAdmin(ctx, { status: 200 });
      assert.ok(!signedOut(s), "a healthy load showed the password box");
      assert.match(s.text, /dashboard/i, "the console did not render");
    },
  },
  {
    name: "a 500 keeps you signed in and offers a retry",
    async fn(ctx) {
      const s = await openAdmin(ctx, { status: 500 });
      assert.ok(
        !signedOut(s),
        "a backend error signed the admin out — this is the bug: the "
        + "password is fine, the server is not",
      );
      assert.match(s.text, /could not refresh the dashboard/i,
        "the failure was swallowed instead of reported");
      assert.match(s.text, /retry/i, "no way to try again without a reload");
      assert.equal(s.stored, "test-password",
        "a server error threw away a working password");
    },
  },
  {
    name: "a timeout keeps you signed in too",
    async fn(ctx) {
      // 502 stands in for the cold-start case, which is the one that
      // actually bit: Render waking up is not a rejection.
      const s = await openAdmin(ctx, { status: 502 });
      assert.ok(!signedOut(s), "a gateway error signed the admin out");
      assert.equal(s.stored, "test-password");
    },
  },
  {
    name: "a 401 DOES sign you out, and clears the bad password",
    async fn(ctx) {
      const s = await openAdmin(ctx, { status: 401 });
      assert.ok(
        signedOut(s),
        "a rejected password left the console open — only the server can "
        + "say the password is good, and it said no",
      );
      assert.equal(
        s.stored, null,
        "the rejected password is still stored, so every reload will "
        + "fail the same way",
      );
    },
  },
  {
    name: "no stored password shows the sign-in box",
    async fn(ctx) {
      const s = await openAdmin(ctx, { status: 200, storage: {} });
      assert.ok(signedOut(s), "the console rendered with no password at all");
    },
  },
];
