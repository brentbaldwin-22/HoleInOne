// The produced column keeps a clip after it is approved, once.
//
//   osascript -l JavaScript tools/produced_tiles_test.js
//
// Reads the shipping module off disk and strips `export`, because
// this machine has JavaScriptCore and no Node, and a test that
// retypes the function tests the retyping.
function run() {
  const fm = $.NSFileManager.defaultManager;
  const root = $.NSString.alloc.initWithUTF8String(
    $.NSProcessInfo.processInfo.environment.objectForKey("PWD").js).js;
  const path = root + "/frontend/src/pages/producedTiles.js";
  if (!fm.fileExistsAtPath($(path))) {
    console.log("FAIL cannot find " + path);
    return 1;
  }
  const src = $.NSString.stringWithContentsOfFileEncodingError(
    $(path), $.NSUTF8StringEncoding, null).js;
  eval(src.replace(/export\s+function/g, "function"));

  let failed = 0;
  const check = (label, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    console.log("  " + (ok ? "ok   " : "FAIL ") + label
                + (ok ? "" : "  got " + JSON.stringify(got)
                           + " want " + JSON.stringify(want)));
    if (!ok) failed += 1;
  };
  const ids = (rows) => rows.map((r) => r.id);

  const d1 = { id: 1, stage: "draft" };
  const d2 = { id: 2, stage: "draft" };
  const f3 = { id: 3, stage: "final" };

  console.log("1. the ordinary cases");
  check("drafts alone come back as they are",
        ids(producedTiles([d1, d2], [])), [1, 2]);
  check("an approved clip is kept, after the drafts",
        ids(producedTiles([d1], [f3])), [1, 3]);
  check("approved only, nothing in draft",
        ids(producedTiles([], [f3])), [3]);
  check("nothing at all", producedTiles([], []), []);

  console.log("\n2. THE REASON IT IS A FUNCTION");
  // The two fetches are not atomic. A clip approved between them is
  // a draft to the first query and a final to the second, so it
  // arrives twice -- and the cell shows the same swing twice, which
  // reads as a data bug rather than a display one.
  const both = { id: 2, stage: "final" };
  check("a clip in both lists appears once",
        ids(producedTiles([d1, d2], [both])), [1, 2]);
  check("...and it is the draft-query copy that is kept",
        producedTiles([d1, d2], [both])[1].stage, "draft");
  check("a list that repeats itself is still one tile per clip",
        ids(producedTiles([d1, d1], [])), [1]);

  console.log("\n3. it survives what the page can actually hand it");
  // `drafts` is null until the first fetch lands, and the live-draft
  // source can be null too. A throw here blanks the whole page.
  check("null drafts", producedTiles(null, [f3]).length, 1);
  check("null finals", producedTiles([d1], null).length, 1);
  check("both null", producedTiles(null, null), []);
  check("undefined", producedTiles(undefined, undefined), []);
  check("a hole in the list is skipped, not rendered",
        ids(producedTiles([d1, null, d2], [])), [1, 2]);

  console.log("\n4. it does not mutate what it was given");
  const a = [d1], b = [f3];
  producedTiles(a, b);
  check("the drafts array is untouched", a.length, 1);
  check("the finals array is untouched", b.length, 1);

  console.log(failed ? "\n" + failed + " FAILED" : "\nall passed");
  return failed ? 1 : 0;
}
run();
