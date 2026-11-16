/* RENDER THE ADMIN COMPONENTS FOR REAL.
 *
 * `vite build` compiles a temporal-dead-zone reference happily -- it
 * is a RUNTIME fault -- so a component reading a `const` declared
 * further down builds clean and then throws on every render, which
 * blanks the whole app. That shipped once: the gate editor read
 * `CONE` eleven lines before it was declared, and the operator got a
 * white page on Find ascents.
 *
 * So each component is bundled and rendered to a string with props
 * shaped like the real ones. Anything that throws at render time --
 * TDZ, a bad destructure, reading a field off undefined -- fails here
 * instead of in front of the operator.
 */
import { createRequire } from "node:module";
import path from "node:path";

// RESOLVE FROM THE FRONTEND, not from this file: node_modules lives
// beside the app, and this script deliberately does not.
const ROOT = path.resolve(process.argv[2] || ".");
const require = createRequire(path.join(ROOT, "package.json"));
const { build } = require("esbuild");
const React = require("react");
const { renderToString } = require("react-dom/server");

const CASES = [
  {
    file: "src/components/GateTuning.jsx",
    name: "GateTuning",
    // The editor reads its data from the API, so the test stands in
    // for a loaded response. The gate names are the real ones.
    props: { adminPassword: "x", uploadId: 1 },
    seed: {
      spec: {
        ascent_min_points: { default: 6, lo: 3, hi: 40, int: true },
        ascent_cone_from_deg: { default: -90, lo: -180, hi: 180 },
        ascent_cone_to_deg: { default: 90, lo: -180, hi: 180 },
        descent_min_points: { default: 4, lo: 2, hi: 40, int: true },
        audio_min_ratio: { default: 4 },
        audio_lag_sec: { default: 0 },
        ball_min_contrast: { default: 30 },
      },
      values: {}, defaults: {}, tuned: { audio_min_ratio: 8 },
      zones: [{ index: 0, screen: 1, label: "Back" },
              { index: 2, screen: 3, label: "" }],
      key: "cam:1-2", cutter: "audio",
    },
  },
  {
    file: "src/components/CutVerdicts.jsx",
    name: "CutVerdicts",
    props: {
      cuts: [{
        index: 0, peak_time_sec: 12.3, ratio: 9.1, confidence: "high",
        start_sec: 10.1, end_sec: 18.4, duration_sec: 8.3,
        is_swing: true, heat_is_swing: true, frames_is_swing: true,
        frames_reason: "a swing", frames_handed: "right",
        frames_ball_seen: true, frames_ball_xy: [692, 693],
        ball_left: true, ball_state: "BALL_PRESENT_DISAPPEARED",
        ball_why: "a ball sat at (684, 738)", ball_spot: [684, 738],
        ball_region: [699, 713, 738, 757], ball_found_by: "the stance box",
        ball_lag_implied: 0.4, ball_lag_used: 0.6,
        ball_box_url: "/x.jpg", ball_box_zoom: 10.7,
        ball_box_seen: true, ball_box_gone: true,
        ball_frames: [{ at: -1.2, role: "before", read: true, kept: 1,
                        in_rule: true, spots: [[1, 2]] }],
        decided_by: "majority", frames_image_url: "/f.jpg",
      }],
    },
  },
  // THE EMPTY AND THE MISSING, which is how these actually arrive
  // before a run has finished.
  { file: "src/components/CutVerdicts.jsx", name: "CutVerdicts (no cuts)",
    props: { cuts: [] } },
  { file: "src/components/CutVerdicts.jsx", name: "CutVerdicts (bare cut)",
    props: { cuts: [{ index: 0 }] } },
];

let failed = 0;
for (const c of CASES) {
  const entry = path.join(ROOT, c.file);
  const r = await build({
    entryPoints: [entry], bundle: true, write: false, format: "cjs",
    platform: "node", jsx: "automatic", logLevel: "silent",
    external: ["react", "react-dom", "react-router-dom"],
    // The API module reaches for the network at import time in some
    // builds; stub it so the render is about the component.
    plugins: [{
      name: "stub", setup(b) {
        b.onResolve({ filter: /\/api\.js$/ }, () => ({
          path: "api-stub", namespace: "stub" }));
        b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({
          contents: "export const api = new Proxy({}, { get: () => "
            + "async () => (globalThis.__SEED__ || {}) });",
          loader: "js" }));
      },
    }],
  });
  globalThis.__SEED__ = c.seed || {};
  // ── GET PAST THE LOADING GUARD ──────────────────────────────────
  //
  // An editor that fetches its data bails on the first render --
  // "Loading this hole's gates" -- and `renderToString` never runs
  // effects, so the body below that early return is never reached.
  // Which is exactly where the use-before-declare lived: the
  // component rendered 61 characters and reported no error while
  // throwing on every real render in the browser.
  //
  // So the first state initialised to `null` is handed the seed.
  // That is `data` in every one of these editors, by the convention
  // that the fetched thing is declared first; `err` is next and
  // stays null, which is what the component wants.
  let nulls = 0;
  const realUseState = React.useState;
  if (c.seed) {
    React.useState = (init) => {
      if (init === null && nulls++ === 0) {
        return realUseState(c.seed);
      }
      return realUseState(init);
    };
  }
  const mod = { exports: {} };
  const fn = new Function("module", "exports", "require",
                          r.outputFiles[0].text);
  try {
    fn(mod, mod.exports, require);
    const Comp = mod.exports[c.name.split(" ")[0]]
      || mod.exports.default;
    if (typeof Comp !== "function") {
      throw new Error("no component exported");
    }
    const html = renderToString(React.createElement(Comp, c.props));
    // A COMPONENT THAT RENDERED ALMOST NOTHING did not really get
    // tested. Say so rather than passing on a loading spinner.
    if (c.seed && html.length < 400) {
      throw new Error(`only ${html.length} chars — still on the `
        + "loading branch, so the body was never rendered");
    }
    console.log(`  ok    ${c.name} — rendered ${html.length} chars`);
  } catch (e) {
    failed += 1;
    console.log(`  FAIL  ${c.name} — ${e.message}`);
  } finally {
    React.useState = realUseState;
  }
}
process.exit(failed ? 1 : 0);
