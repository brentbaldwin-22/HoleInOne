/*
 * Which camera Flip opens.
 *
 * Run:  osascript -l JavaScript tools/camera_pick_test.js
 *
 * There is no Node on the build machine, but macOS ships JavaScriptCore
 * behind osascript, and these are pure string decisions -- no DOM, no
 * getUserMedia -- so they can be exercised for real rather than reasoned
 * about. It reads frontend/src/components/cameraPick.js off disk and
 * strips the `export` keywords, so it tests the shipping source and
 * cannot drift from it.
 *
 * The labels below are what iOS Safari and Android Chrome actually
 * report. This is the logic that was wrong when Flip mirrored the
 * preview while leaving the front lens live.
 */
ObjC.import("Foundation");

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
  if (!s) throw new Error("could not read " + path);
  return ObjC.unwrap(s);
}

const here = $.NSFileManager.defaultManager.currentDirectoryPath;
const src = readFile(ObjC.unwrap(here) + "/frontend/src/components/cameraPick.js");
eval(src.replace(/\bexport\s+/g, ""));

const fails = [];
function check(label, cond, detail) {
  console.log((cond ? "  PASS  " : "  FAIL  ") + label + (detail ? "   " + detail : ""));
  if (!cond) fails.push(label);
}
function cams(labels) {
  return labels.map((l, i) => ({ deviceId: "d" + i, label: l, side: sideOf(l) }));
}

console.log("an iPhone's own labels");
const iphone = cams(["Front Camera", "Back Camera", "Back Ultra Wide Camera", "Back Dual Wide Camera"]);
check("front is recognised", sideOf("Front Camera") === "front");
check("back is recognised", sideOf("Back Ultra Wide Camera") === "back");
check("flip to back takes the ultrawide, for head to toe",
  pickCamera(iphone, "back").label === "Back Ultra Wide Camera",
  pickCamera(iphone, "back").label);
check("flip to front takes the front",
  pickCamera(iphone, "front").label === "Front Camera",
  pickCamera(iphone, "front").label);
check("back is never the front lens",
  pickCamera(iphone, "back").side === "back");
check("front is never a back lens",
  pickCamera(iphone, "front").side === "front");

console.log("\nan Android, which words it differently");
const android = cams(["camera2 1, facing front", "camera2 0, facing back"]);
check("front", pickCamera(android, "front").side === "front");
check("back", pickCamera(android, "back").side === "back");

console.log("\na plain two-camera phone with no ultrawide");
const plain = cams(["Front Camera", "Back Camera"]);
check("back is still found", pickCamera(plain, "back").label === "Back Camera");
check("and front", pickCamera(plain, "front").label === "Front Camera");

console.log("\nwhen the browser withholds labels");
const blank = cams(["", "", ""]);
check("every side reads unknown", blank.every((c) => c.side === "unknown"));
check("front falls back to the first device", pickCamera(blank, "front").deviceId === "d0");
check("back falls back to the last", pickCamera(blank, "back").deviceId === "d2");
check("so the two are never the same camera",
  pickCamera(blank, "front").deviceId !== pickCamera(blank, "back").deviceId);

console.log("\nwhen there is nothing to choose from");
check("no cameras yields null, so flip uses facingMode", pickCamera([], "back") === null);
check("and for front too", pickCamera([], "front") === null);

console.log("\nlabels from a laptop");
check("FaceTime HD reads as front", sideOf("FaceTime HD Camera") === "front");
check("an unknown gadget is not guessed at", sideOf("USB Video Device") === "unknown");

console.log("");
console.log(fails.length ? "FAILURES: " + fails.join(", ") : "ALL PASS");
