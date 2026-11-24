#!/usr/bin/env node
/*
 * The raw video is reachable from the Test Cut list.
 *
 * ASKED FOR: "on the long-upload test cutter, have a link to the raw
 * video too."
 *
 * The filename has been printed on every row all along with no way to
 * open the thing it names. "Show source" draws a muted, metadata-only
 * preview inline — that answers "is the tee/green labelling right",
 * not "let me watch the three minutes this was cut out of".
 *
 * THE CASE THIS HAS TO GET RIGHT: `tee_url` is null for TWO different
 * reasons. Either the file is gone (`tee_missing`, already shouted
 * about in red), or it is in the bucket rather than on this box's
 * disk — perfectly healthy, fetched on demand by whatever next needs
 * it. A bare filename for the second case makes a fine upload look
 * like the broken one.
 *
 *   node tools/raw_video_link_test.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JSX = readFileSync(
  join(ROOT, "frontend/src/pages/AdminLongUpload.jsx"), "utf8");

const failed = [];
const check = (label, got, want = true) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(60)} ${ok ? "ok" : `FAIL got ${JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
};

// THE REAL COMPONENT, not a re-implementation of it. The function is
// lifted out of the page, run through esbuild's JSX transform with a
// factory that returns plain objects, and imported. A test that
// rewrites the thing it is checking checks nothing.
const i = JSX.indexOf("function RawFile(");
if (i < 0) throw new Error("RawFile is gone from AdminLongUpload.jsx");
// TO THE BRACE IN COLUMN ZERO, not by counting them. The component
// is full of `{" "}` and braces inside JSX attributes, so a naive
// depth counter closes it two lines in and hands esbuild a fragment.
// It is a top-level function, so its own closing brace is the first
// one at the start of a line.
const body = (() => {
  const end = JSX.indexOf("\n}\n", i);
  if (end < 0) throw new Error("RawFile did not close");
  return JSX.slice(i, end + 2);
})();

const { transform } = await import(
  join(ROOT, "frontend/node_modules/esbuild/lib/main.js"));
const out = await transform(`${body}\nexport { RawFile };`, {
  loader: "jsx",
  jsx: "transform",
  jsxFactory: "h",
  jsxFragment: "F",
});
// `h` collects what the component actually built: the tag, its
// props, and the text in it, flattened so a nested <code> inside an
// <a> still shows up as that anchor's text.
const shim = `
  const text = (k) => (Array.isArray(k) ? k.map(text).join("")
    : (k && k.props ? text(k.kids) : (k == null || k === false ? "" : String(k))));
  const h = (tag, props, ...kids) => ({ tag, props: props || {}, kids,
    get text() { return text(kids); } });
  const F = "fragment";
`;
const mod = await import(
  "data:text/javascript," + encodeURIComponent(shim + out.code));
const { RawFile } = mod;

console.log("\n1. a file on disk is a link to itself");
const onDisk = RawFile({ name: "camera-event-2280-tee.mp4",
                         url: "/uploads/clips/camera-event-2280-tee.mp4",
                         missing: false });
check("it is an anchor", onDisk.tag, "a");
check("pointing at the raw file", onDisk.props.href,
      "/uploads/clips/camera-event-2280-tee.mp4");
check("still showing the filename", onDisk.text,
      "camera-event-2280-tee.mp4");
check("and it says what it opens",
      /raw video/.test(onDisk.props.title || ""), true);

console.log("\n2. a file in the bucket is NOT a dead link");
const cold = RawFile({ name: "camera-event-2279-tee.mp4", url: null,
                       missing: false });
check("no anchor", cold.tag === "a", false);
check("the filename is still printed",
      cold.text.includes("camera-event-2279-tee.mp4"), true);
check("but it says why there is none",
      cold.text.includes("in storage"), true);

console.log("\n3. a file that is gone says nothing extra");
const gone = RawFile({ name: "camera-event-1.mp4", url: null,
                       missing: true });
check("no anchor", gone.tag === "a", false);
check("and no 'in storage' — the red 'missing on disk' owns that row",
      gone.text.includes("in storage"), false);

console.log("\n4. no filename at all does not render a link to nowhere");
check("a dash, and no anchor even though a url was passed",
      [RawFile({ name: null, url: "/x.mp4" }).tag,
       RawFile({ name: null, url: "/x.mp4" }).text], ["code", "—"]);

console.log("\n5. and it is used on both rows of both places");
check("the tee line on the list", JSX.includes("url={u.tee_url} missing={u.tee_missing} />"));
check("the green line on the list",
      JSX.includes("url={u.green_url} missing={u.green_missing} />"));
check("used twice per camera — list row and source preview",
      (JSX.match(/<RawFile/g) || []).length, 4);
check("links open away from the page, safely",
      JSX.includes('target="_blank"') && JSX.includes('rel="noreferrer"'));
check("the preview blurb points at the filename",
      JSX.includes("The filename"));

console.log();
if (failed.length) {
  console.log(`FAILED (${failed.length}):`);
  failed.forEach((f) => console.log("  -", f));
  process.exit(1);
}
console.log("all ok");
