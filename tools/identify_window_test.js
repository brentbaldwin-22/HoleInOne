// DOES NARROWING BY TEE TIME EVER LOSE A SHOT?
//
// The point of the window is fewer tiles to scroll. The point of this
// test is the opposite: that it gives up cleanly every time it might
// hide the shot being looked for.
//
// JavaScriptCore via osascript is the only JS runtime on this machine
// (no node), so the module is read off disk and its `export` keywords
// stripped -- the same trick tools/camera_pick_test.js uses.
//
//     osascript -l JavaScript tools/identify_window_test.js

ObjC.import("Foundation");

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(
    path, $.NSUTF8StringEncoding, null);
  if (!s) throw new Error("could not read " + path);
  return ObjC.unwrap(s);
}

// Run from the repo root, as tools/camera_pick_test.js does.
const here = ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
const src = readFile(here + "/frontend/src/pages/identifyWindow.js");
eval(src.replace(/\bexport\s+/g, ""));

function run() {

  let checks = 0;
  function ok(cond, what) {
    checks += 1;
    if (!cond) throw new Error("FAILED: " + what);
  }

  // Noon tee time, hole 2: the backend's window is 11:45-12:43 UTC.
  const WIN = ["2026-11-18T11:45:00", "2026-11-18T12:43:00"];
  const clip = (id, hhmm, pid) => ({
    id, captured_at: `2026-11-18T${hhmm}:00`, participant_id: pid ?? null,
  });

  const day = [
    clip(1, "09:00"),            // morning group
    clip(2, "11:50"),            // in window
    clip(3, "12:10"),            // in window
    clip(4, "12:30"),            // in window
    clip(5, "15:00"),            // afternoon group
  ];

  // ---- it narrows -------------------------------------------------
  let r = visibleClips(day, WIN, 7, false);
  ok(r.windowed === true, "should have narrowed");
  ok(r.shown.length === 3, "kept the three in-window shots");
  ok(r.hidden === 2, "said two were hidden, got " + r.hidden);
  ok(!r.shown.some((c) => c.id === 1 || c.id === 5), "9am/3pm are gone");

  // ---- NOTHING IS EVER LOST ---------------------------------------
  // shown + hidden must always account for every clip, in every mode.
  for (const [w, sa, label] of [
    [WIN, false, "narrowed"], [WIN, true, "show all"],
    [null, false, "no window"],
  ]) {
    const x = visibleClips(day, w, 7, sa);
    ok(x.shown.length + x.hidden === day.length,
       "clips went missing in mode: " + label);
  }

  // ---- the three give-ups ----------------------------------------
  r = visibleClips(day, null, 7, false);
  ok(r.windowed === false && r.shown.length === 5, "no window -> show all");

  r = visibleClips(day, WIN, 7, true);
  ok(r.windowed === false && r.shown.length === 5, "showAll -> show all");

  // A window that matches nothing: a shotgun start, say.
  const SHOTGUN = ["2026-11-18T04:00:00", "2026-11-18T05:00:00"];
  r = visibleClips(day, SHOTGUN, 7, false);
  ok(r.windowed === false && r.shown.length === 5,
     "a window that hides everything must show everything instead");
  ok(r.hidden === 0, "and must not claim anything is hidden");

  // ---- a paired shot is never hidden ------------------------------
  // The 3pm clip already belongs to this golfer. Wrong by the clock,
  // but it has to stay visible or the mis-tap cannot be undone.
  const withPaired = [clip(1, "09:00"), clip(2, "12:10"), clip(5, "15:00", 7)];
  r = visibleClips(withPaired, WIN, 7, false);
  ok(r.shown.some((c) => c.id === 5),
     "a shot already paired to this golfer was hidden");
  ok(!r.shown.some((c) => c.id === 1), "but someone else's 9am still goes");

  // Another golfer's paired shot is not special.
  r = visibleClips(withPaired, WIN, 99, false);
  ok(!r.shown.some((c) => c.id === 5),
     "another golfer's out-of-window shot was kept");

  // ---- naive UTC is not read as local -----------------------------
  // The backend sends no timezone. Read as local this is 5 hours out
  // and the whole filter picks the wrong clips.
  ok(asDate("2026-11-18T12:00:00").getTime()
     === Date.UTC(2026, 10, 18, 12, 0, 0), "tz-less string not read as UTC");

  // ---- degenerate input -------------------------------------------
  ok(visibleClips([], WIN, 7, false).shown.length === 0, "empty day");
  ok(visibleClips(null, WIN, 7, false).shown.length === 0, "null clips");
  ok(visibleClips(day, ["bad", "worse"], 7, false).shown.length === 5,
     "an unparseable window must not hide anything");

  return "OK — " + checks + " checks";
}

run();
