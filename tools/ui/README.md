# UI tests

Browser tests for the SPA. They drive a real Chromium against a real
build and assert on what the page actually *paints*, not just on what
the DOM says.

```bash
cd tools/ui
npm install          # once — downloads a Chromium
npm test
```

```
npm test -- --no-build     # reuse frontend/dist as it stands
npm test -- camera         # only files whose name contains "camera"
```

`npm test` rebuilds `frontend/dist` first. That is deliberate: a stale
build makes the suite describe a bundle nobody is shipping, and it does
so silently — green on code that was never compiled. `--no-build` is for
iterating on a test, not for CI.

## Why a browser

The bug this suite was written for was a CSS specificity tie.
`button.secondary` and `button:disabled` are both `(0,1,1)`, so on a tie
the later rule wins — and a disabled secondary button kept its live blue
outline while genuinely being unpressable. It looked like a button you
could press and did nothing when you did.

Every DOM assertion passed. The `disabled` attribute was correct the
whole time. Only `getComputedStyle` could see it, and jsdom does not
implement the cascade, so in jsdom that bug is invisible by
construction.

So the rule here: **if a test is about how something looks, assert the
computed value, not the attribute that is supposed to produce it.**

## Why it is not part of `frontend/`

The Dockerfile runs `npm ci` in `frontend/` *with* devDependencies —
Vite is one, so `--omit=dev` is not available. Anything added to
`frontend/package.json` is therefore downloaded on every Render deploy,
and Playwright's postinstall pulls a browser. This is a separate package
with its own `node_modules`, and the image never copies `tools/`.

## What is covered

| File | Guards against |
|---|---|
| `controls.test.mjs` | A button variant out-ranking `:disabled`, so a dead control looks live |
| `camera-gating.test.mjs` | Offering Capture / Watch live / Read profile on a camera whose agent is down |
| `layout.test.mjs` | Horizontal page scroll — the home page once reached 2192px on a phone |
| `typography.test.mjs` | `styles.css` spending a family or weight that `index.html` never fetches |

Each of those is a bug that actually shipped. That is the bar for adding
a case here: a test is worth its runtime if it would have caught
something real.

## Writing a test

A test file exports a `name` and an array of `{ name, fn }`. `fn` gets
`{ browser, baseUrl }`.

```js
import assert from "node:assert/strict";
import { openApp, rgb } from "../harness.mjs";

export const name = "what this file is about";

export const tests = [{
  name: "one specific claim",
  async fn(ctx) {
    const { page, errors } = await openApp(ctx.browser, {
      baseUrl: ctx.baseUrl,
      path: "/admin/cameras",
      routes: { "/api/admin/cameras": [/* … */] },
      storage: { "golfreelz.adminPassword": "test-password" },
    });
    assert.deepEqual(errors, [], "the page threw while rendering");
    // …
    await page.close();
  },
}];
```

`openApp` stubs **every** `/api/` call — unmatched ones get `[]`, because
the pages map over these lists and `{}` crashes them. It also collects
page errors: a React render that throws still leaves a DOM, so a test
that does not check `errors` will happily assert against the wreckage.

## Verifying a test is not vacuous

A test that cannot fail is worse than no test, because it reports
safety. Before adding one, break the thing it guards and watch it go
red. Two in this suite were vacuous when first written and only the
mutation step caught it:

- `document.fonts.check("300 16px Fraunces")` returns `true` for a
  family that does not exist at all — `check("16px 'Definitely Not A
  Font'")` is also `true`. The whole font check passed no matter what
  either file said. It is a static check against the two files now.
- The Google Fonts URL parser split the href on `&`, which drops the
  first `family=` (it rides on the `?`). The test reported that
  `index.html` does not fetch Fraunces, and that false failure was the
  only reason anyone looked.

## Chromium

`npm install` fetches a browser matched to the pinned Playwright
version. On a machine that already ships one — a CI image, a cloud agent
sandbox — the harness falls back to `/opt/pw-browsers/chromium`, and
`GOLFREELZ_CHROMIUM` overrides both.
