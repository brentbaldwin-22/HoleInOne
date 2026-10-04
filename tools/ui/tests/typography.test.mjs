/**
 * THE FONTS THE CODE SPENDS HAVE TO BE THE FONTS THE PAGE FETCHES.
 *
 * These drifted apart once and nothing said so. styles.css set --text to
 * "Work Sans" while index.html was still fetching Inter from the
 * previous look, so not one line of body copy on the site was ever set
 * in Work Sans — it all fell through to system-ui, and the only symptom
 * was that the design "looked a bit off". The display weights went the
 * same way: the sheet spends 300 for a large heading and 400 for a
 * small one, the link fetched 500/600/700, and the browser synthesised
 * the rest, so every heading came out heavier and wider than drawn.
 *
 * WHY THIS IS A STATIC CHECK AND NOT A BROWSER ONE. The obvious test is
 * document.fonts.check("300 16px Fraunces") in the page. It is useless:
 * it returns true for a family that does not exist at all (verified —
 * check("16px 'Definitely Not A Font'") is true), because the font set
 * answers "can this be rendered", and with system fallback the answer is
 * always yes. document.fonts is also empty here, since @font-face rules
 * from a cross-origin stylesheet never enter it. A test built on that
 * API passes no matter what either file says.
 *
 * The real invariant is a mismatch between two files on disk, so it is
 * checked on disk: deterministic, and it runs with no network.
 */
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const name = "declared fonts are the fetched fonts";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "..", "..", "..", "frontend");

/** The first real family named by a custom property in styles.css. */
async function declaredFamilies() {
  const css = await readFile(join(frontend, "src", "styles.css"), "utf8");
  const out = {};
  for (const prop of ["--display", "--text"]) {
    const m = css.match(new RegExp(`${prop}\\s*:\\s*([^;]+);`));
    assert.ok(m, `styles.css no longer declares ${prop}`);
    out[prop] = m[1].split(",")[0].trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

/**
 * What index.html actually asks Google Fonts for: family -> weights.
 *
 * The URL shape is `family=Name:axes@tuple;tuple`, with the axis values
 * in each tuple ordered as the axis list names them — so in
 * `opsz,wght@9..144,300` the weight is the LAST value. A family with no
 * axis spec at all gets regular only, which is what Google serves.
 */
async function requestedWeights() {
  const html = await readFile(join(frontend, "index.html"), "utf8");
  const m = html.match(/href="(https:\/\/fonts\.googleapis\.com\/css2[^"]+)"/);
  assert.ok(m, "index.html no longer links a Google Fonts stylesheet");
  const href = decodeURIComponent(m[1].replace(/&amp;/g, "&"));
  // Split the QUERY, not the URL: the first family= rides on the "?" and
  // splitting the whole href on "&" drops it. (It did, and the test then
  // reported that index.html does not fetch Fraunces at all.)
  const query = href.slice(href.indexOf("?") + 1);
  const out = {};
  for (const spec of query.split("&")) {
    if (!spec.startsWith("family=")) continue;
    const [name, axes] = spec.slice("family=".length).split(":");
    const family = name.replace(/\+/g, " ");
    const weights = new Set();
    if (!axes || !axes.includes("@")) {
      weights.add(400);
    } else {
      for (const tuple of axes.split("@")[1].split(";")) {
        const last = tuple.split(",").pop();
        const n = parseInt(last, 10);
        if (Number.isFinite(n)) weights.add(n);
      }
    }
    out[family] = weights;
  }
  return out;
}

/** Every font weight the app actually sets, and where it set it. */
async function spentWeights() {
  const found = new Map();                       // weight -> example source
  const note = (w, where) => {
    const n = parseInt(w, 10);
    if (Number.isFinite(n) && !found.has(n)) found.set(n, where);
  };

  const css = await readFile(join(frontend, "src", "styles.css"), "utf8");
  for (const m of css.matchAll(/font-weight:\s*(\d+)/g)) note(m[1], "styles.css");

  // Inline styles count. They are how 700 got spent without anyone
  // noticing it was never fetched.
  const srcDir = join(frontend, "src");
  const walk = async (dir) => {
    for (const ent of await readdir(dir, { withFileTypes: true })) {
      const p = join(dir, ent.name);
      if (ent.isDirectory()) await walk(p);
      else if (p.endsWith(".jsx") || p.endsWith(".js")) {
        const text = await readFile(p, "utf8");
        for (const m of text.matchAll(/fontWeight:\s*"?(\d+)"?/g)) {
          note(m[1], p.slice(frontend.length + 1));
        }
      }
    }
  };
  await walk(srcDir);

  // <b> and <strong> are bold by the UA stylesheet and this app uses
  // both heavily, so 700 is spent whether or not anything declares it.
  note(700, "<b> / <strong> (UA default)");
  return found;
}

export const tests = [
  {
    name: "index.html fetches the families styles.css spends",
    async fn() {
      const families = await declaredFamilies();
      const requested = await requestedWeights();
      for (const [prop, family] of Object.entries(families)) {
        assert.ok(
          Object.keys(requested).includes(family),
          `styles.css sets ${prop} to "${family}", but index.html fetches `
          + `${Object.keys(requested).join(", ") || "nothing"} — every element `
          + `using ${prop} falls back to a system font.`,
        );
      }
    },
  },
  {
    name: "every weight the app spends is fetched by some family",
    async fn() {
      // DELIBERATELY A UNION, not per-family: nothing in the CSS ties a
      // `font-weight` to a family, so the honest claim is "no weight is
      // spent that no family was asked for". That catches both ways this
      // has actually broken — a weight added to the sheet, and a weight
      // dropped from the link — and stops short of a per-family mapping
      // it would have to invent.
      const requested = await requestedWeights();
      const available = new Set(
        Object.values(requested).flatMap((s) => [...s]),
      );
      const spent = await spentWeights();
      const missing = [...spent.entries()]
        .filter(([w]) => !available.has(w))
        .map(([w, where]) => `${w} (${where})`);
      assert.deepEqual(
        missing, [],
        "weights are spent that index.html never fetches, so the browser "
        + "synthesises them: " + missing.join(", ")
        + `\n  fetched: ${[...available].sort((a, b) => a - b).join(", ")}`,
      );
    },
  },
];
