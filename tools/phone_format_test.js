/*
 * Mobile number punctuation.
 *
 * Run:  osascript -l JavaScript tools/phone_format_test.js
 *
 * No Node on this machine, but macOS ships JavaScriptCore behind
 * osascript and this is pure string work. Reads the shipping module off
 * disk and strips `export`, so it cannot drift from what runs.
 *
 * The case worth pinning is backspace. A formatter that patches the
 * string instead of rebuilding from the digits will re-add the dash the
 * user just deleted, and the field becomes impossible to edit.
 */
ObjC.import("Foundation");

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
  if (!s) throw new Error("could not read " + path);
  return ObjC.unwrap(s);
}
const here = ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
eval(readFile(here + "/frontend/src/phone.js").replace(/\bexport\s+/g, ""));

const fails = [];
function check(label, cond, detail) {
  console.log((cond ? "  PASS  " : "  FAIL  ") + label + (detail ? "   " + detail : ""));
  if (!cond) fails.push(label);
}
function eq(label, got, want) { check(label, got === want, JSON.stringify(got) + " want " + JSON.stringify(want)); }

console.log("typing a number, digit by digit");
eq("4", formatUsPhone("4"), "4");
eq("469", formatUsPhone("469"), "469");
eq("4698 -> dash after the area code", formatUsPhone("4698"), "469-8");
eq("469847", formatUsPhone("469847"), "469-847");
eq("4698479 -> second dash", formatUsPhone("4698479"), "469-847-9");
eq("the whole number", formatUsPhone("4698479270"), "469-847-9270");

console.log("\nbackspace, which is where these go wrong");
// The field holds "469-847-9270"; a backspace hands us it minus one char.
eq("deleting a digit", formatUsPhone("469-847-927"), "469-847-927");
eq("deleting back onto a dash does not re-add it", formatUsPhone("469-847-"), "469-847");
eq("and again", formatUsPhone("469-847"), "469-847");
eq("back past the second dash", formatUsPhone("469-84"), "469-84");
eq("back to the first dash", formatUsPhone("469-"), "469");
eq("emptied", formatUsPhone(""), "");

console.log("\npasting");
eq("plain digits", formatUsPhone("4698479270"), "469-847-9270");
eq("already punctuated", formatUsPhone("469-847-9270"), "469-847-9270");
eq("in brackets", formatUsPhone("(469) 847-9270"), "469-847-9270");
eq("with spaces and dots", formatUsPhone("469.847 9270"), "469-847-9270");

console.log("\nnumbers this format does not describe are left alone");
eq("international", formatUsPhone("+44 7700 900123"), "+44 7700 900123");
eq("leading +1", formatUsPhone("+1 469 847 9270"), "+1 469 847 9270");
eq("eleven digits", formatUsPhone("14698479270"), "14698479270");
eq("someone's extension", formatUsPhone("4698479270x12"), "4698479270x12");

console.log("\nnothing");
eq("null", formatUsPhone(null), "");
eq("undefined", formatUsPhone(undefined), "");
eq("letters only", formatUsPhone("abc"), "");

console.log("");
console.log(fails.length ? "FAILURES: " + fails.join(", ") : "ALL PASS");
