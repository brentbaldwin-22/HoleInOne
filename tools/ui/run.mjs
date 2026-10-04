/**
 * Run every tools/ui/tests/*.test.mjs against a freshly built SPA.
 *
 *   npm install      # once, in this directory
 *   npm test
 *   npm test -- --no-build        # reuse frontend/dist as it stands
 *   npm test -- camera            # only files whose name contains "camera"
 *
 * REBUILDS BY DEFAULT, because the alternative is worse than three
 * seconds: a stale frontend/dist makes the suite describe a bundle
 * nobody is shipping, and it does so silently — green on code that was
 * never compiled. --no-build is for iterating on a test, not for CI.
 */
import { spawnSync } from "node:child_process";
import { readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { launchBrowser, serveDist } from "./harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..");
const frontend = join(repo, "frontend");
const dist = join(frontend, "dist");

const args = process.argv.slice(2);
const noBuild = args.includes("--no-build");
const filter = args.find((a) => !a.startsWith("--"));

if (!noBuild) {
  console.log("building frontend…");
  const r = spawnSync("npm", ["run", "build"], {
    cwd: frontend, stdio: ["ignore", "ignore", "inherit"],
  });
  if (r.status !== 0) {
    console.error("frontend build failed — not running the UI tests.");
    process.exit(1);
  }
}
if (!existsSync(join(dist, "index.html"))) {
  console.error(`no build at ${dist}. Drop --no-build, or run npm run build in frontend/.`);
  process.exit(1);
}

const files = (await readdir(join(here, "tests")))
  .filter((f) => f.endsWith(".test.mjs"))
  .filter((f) => !filter || f.includes(filter))
  .sort();

if (files.length === 0) {
  console.error(filter ? `no test files match "${filter}".` : "no test files.");
  process.exit(1);
}

const server = await serveDist(dist);
const browser = await launchBrowser();
const ctx = { browser, baseUrl: server.url };

let passed = 0;
const failures = [];

for (const file of files) {
  const mod = await import(pathToFileURL(join(here, "tests", file)).href);
  const suite = mod.name || file.replace(/\.test\.mjs$/, "");
  console.log(`\n${suite}`);
  for (const t of mod.tests) {
    try {
      await t.fn(ctx);
      passed += 1;
      console.log(`  ok    ${t.name}`);
    } catch (err) {
      failures.push({ suite, name: t.name, err });
      console.log(`  FAIL  ${t.name}`);
    }
  }
}

await browser.close();
await server.close();

if (failures.length) {
  console.log(`\n${failures.length} failed, ${passed} passed\n`);
  for (const f of failures) {
    console.log(`--- ${f.suite} / ${f.name}`);
    console.log(String(f.err.message || f.err).split("\n").slice(0, 14).join("\n"));
    console.log();
  }
  process.exit(1);
}
console.log(`\n${passed} passed\n`);
