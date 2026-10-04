/**
 * THE BROWSER THE TESTS RUN IN, AND THE APP THEY POINT AT.
 *
 * WHY A REAL BROWSER AND NOT JSDOM. The bug this suite exists for was a
 * CSS specificity tie: `button.secondary` and `button:disabled` are both
 * (0,1,1), so a disabled secondary button kept its live blue outline
 * while genuinely being unpressable. Every assertion about the DOM
 * passed — the `disabled` attribute was correct the whole time. Only
 * getComputedStyle caught it, and jsdom does not implement the cascade,
 * so in jsdom that bug is invisible by construction.
 *
 * So: Chromium, the real stylesheet, the real bundle. The cost is a
 * browser download on first install; the alternative is a suite that
 * cannot see the class of bug it was written for.
 */
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
};

/**
 * Serve a built SPA with history fallback on an ephemeral port.
 *
 * The fallback is the point: the app is a single bundle behind
 * react-router, so /admin/cameras is not a file and a plain static
 * server answers it with 404. Tests that navigate to a route would then
 * assert against an error page and pass or fail for the wrong reason.
 */
export async function serveDist(root) {
  const server = createServer(async (req, res) => {
    const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
    let file = join(root, normalize(urlPath));
    if (!file.startsWith(root)) file = join(root, "index.html");
    try {
      const s = await stat(file);
      if (s.isDirectory()) file = join(root, "index.html");
    } catch {
      file = join(root, "index.html");
    }
    try {
      const body = await readFile(file);
      res.writeHead(200, {
        "content-type": TYPES[extname(file)] || "application/octet-stream",
      });
      res.end(body);
    } catch (err) {
      res.writeHead(500);
      res.end(String(err));
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise((r) => server.close(r)),
  };
}

/**
 * Launch Chromium, preferring whatever this machine already has.
 *
 * On a normal checkout `npm install` fetches a browser matched to the
 * pinned Playwright version and the plain launch works. In a container
 * that ships its own (PLAYWRIGHT_BROWSERS_PATH, a cloud agent sandbox,
 * CI images) the versioned directory Playwright looks for may not be
 * the one that exists, so fall back to an explicit binary rather than
 * failing with "Executable doesn't exist" on a machine that plainly has
 * a browser. GOLFREELZ_CHROMIUM overrides both.
 */
export async function launchBrowser() {
  const { chromium } = await import("playwright");
  const candidates = [
    process.env.GOLFREELZ_CHROMIUM,
    "/opt/pw-browsers/chromium",
  ].filter((p) => p && existsSync(p));
  try {
    return await chromium.launch();
  } catch (err) {
    for (const executablePath of candidates) {
      try {
        return await chromium.launch({ executablePath });
      } catch { /* try the next one */ }
    }
    throw new Error(
      `could not launch Chromium (${err.message.split("\n")[0]}).\n`
      + "Run `npm install` in tools/ui, or `npx playwright install chromium`, "
      + "or point GOLFREELZ_CHROMIUM at a binary.",
    );
  }
}

/**
 * Open a route with the API stubbed out.
 *
 * EVERY /api/ CALL IS ANSWERED, including ones a test did not think
 * about: an unstubbed fetch would either hang the `networkidle` wait or
 * throw inside a component and leave a half-rendered page that the
 * assertions then describe. `routes` matches on a URL substring; the
 * default for anything unmatched is `[]`, because the pages map over
 * these lists and `{}` crashes them.
 *
 * Page errors are collected rather than ignored. A React render that
 * throws still leaves a DOM, so a suite that does not check for them
 * will happily assert against the wreckage.
 */
export async function openApp(browser, {
  path = "/",
  routes = {},
  storage = {},
  viewport = { width: 1280, height: 1000 },
  baseUrl,
} = {}) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.stack || String(e)));
  if (Object.keys(storage).length) {
    await page.addInitScript((kv) => {
      for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v);
    }, storage);
  }
  await page.route("**/api/**", (route) => {
    const url = route.request().url();
    for (const [needle, value] of Object.entries(routes)) {
      if (url.includes(needle)) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(value),
        });
      }
    }
    return route.fulfill({
      status: 200, contentType: "application/json", body: "[]",
    });
  });
  await page.goto(baseUrl + path, { waitUntil: "networkidle" });
  try { await page.evaluate(() => document.fonts.ready); } catch { /* no-op */ }
  await page.waitForTimeout(250);
  return { page, errors };
}

/** Every button under a selector, as label -> state. */
export async function buttonStates(page, selector) {
  return page.$$eval(selector, (roots) => {
    const out = {};
    for (const root of roots) {
      for (const btn of root.querySelectorAll("button")) {
        const label = (btn.textContent || "").trim().replace(/\s+/g, " ");
        const cs = getComputedStyle(btn);
        out[label] = {
          disabled: btn.disabled,
          background: cs.backgroundColor,
          color: cs.color,
        };
      }
    }
    return out;
  });
}

/** An rgb() string for a hex, to compare against getComputedStyle. */
export function rgb(hex) {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}
