// WHICH COURSE'S CLOSEST-TO-PIN CARD DOES A VISITOR SEE FIRST?
//
// Five courses meant five identical cards reading "No entries yet",
// which is five cards of nothing, and the list grows with every course
// signed. One card and a picker -- but the opening pick decides
// whether the page looks alive or dead, so it is worth being careful
// about.
//
// JavaScriptCore via osascript is the only JS runtime here (no node),
// so the module is read off disk and its exports stripped.
//
//     osascript -l JavaScript tools/ctp_picker_test.js

ObjC.import("Foundation");

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(
    path, $.NSUTF8StringEncoding, null);
  if (!s) throw new Error("could not read " + path);
  return ObjC.unwrap(s);
}

const here = ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
const src = readFile(here + "/frontend/src/pages/Contests.jsx");

// Only the two pure helpers; the rest of the file is React.
const a = src.indexOf("const COURSE_STORAGE");
const b = src.indexOf("const CADENCES");
eval(src.slice(a, b).replace(/\bexport\s+/g, ""));

const fails = [];
function ok(cond, what) { if (!cond) fails.push(what); }

const course = (id, name, rows) => ({
  id: `daily_ctp_${id}`, course_id: id, course_name: name, rows: rows || [],
});

const EMPTY_FIRST = [
  course(1, "Baldwin Links"),
  course(2, "Maridoe"),
  course(3, "RIvertowne", [{ golfer: "Ben", value: 12 }]),
  course(4, "Snee Farm"),
];

// ---- the opening pick -------------------------------------------
// A course with no entries is a dead end: the card says "be the first"
// and there is nothing to read. Prefer one that has a leaderboard.
ok(pickOpeningCourse(EMPTY_FIRST, null) === "3",
   "opened on an empty course when one had entries");

// Nobody has played anywhere yet: the first is as good as any.
const ALL_EMPTY = [course(1, "Baldwin"), course(2, "Maridoe")];
ok(pickOpeningCourse(ALL_EMPTY, null) === "1", "all empty -> the first");

// THE VIEWER'S OWN CHOICE OUTRANKS BOTH. Somebody checking their own
// club should not have to re-pick it every visit, even if their club
// is quiet today.
ok(pickOpeningCourse(EMPTY_FIRST, "4") === "4", "ignored a saved choice");
ok(pickOpeningCourse(EMPTY_FIRST, 4) === "4", "saved choice as a number");

// A saved course that is no longer running falls back rather than
// selecting nothing.
ok(pickOpeningCourse(EMPTY_FIRST, "99") === "3",
   "a stale saved course should fall back to one with entries");

// Degenerate input.
ok(pickOpeningCourse([], null) === null, "no contests -> null");
ok(pickOpeningCourse(null, null) === null, "null contests -> null");

// ---- what actually renders --------------------------------------
ok(visibleContests(EMPTY_FIRST, "3").length === 1, "should show one card");
ok(visibleContests(EMPTY_FIRST, "3")[0].course_name === "RIvertowne",
   "showed the wrong course");

// NOTHING MATCHED -> SHOW EVERYTHING. A stale selection, or a course
// that stopped running mid-day, must not leave an empty section.
ok(visibleContests(EMPTY_FIRST, "99").length === EMPTY_FIRST.length,
   "an unmatched selection emptied the section");
ok(visibleContests(EMPTY_FIRST, null).length === EMPTY_FIRST.length,
   "no selection should show everything, not nothing");

// One course needs no picker and no filtering.
const ONE = [course(1, "Baldwin")];
ok(visibleContests(ONE, "99").length === 1,
   "a single course must show regardless of the selection");

// A contest with no course_id falls back to its own id, so an older
// payload still picks and filters rather than collapsing.
const LEGACY = [{ id: "daily_ctp_7", rows: [] }];
ok(pickOpeningCourse(LEGACY, null) === "daily_ctp_7", "legacy payload");
ok(visibleContests(LEGACY, "daily_ctp_7").length === 1, "legacy filter");

if (fails.length) {
  throw new Error("FAILED:\n  " + fails.join("\n  "));
}
"OK — " + 13 + " checks";
