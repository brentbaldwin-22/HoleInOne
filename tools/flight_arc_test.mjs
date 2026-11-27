// A flight arc for the swing the detector never saw, and a plotted
// point you can actually see.
//
// THE TRACER'S SOLVER EXTENDS A TRACKED FLIGHT. It reads the launch
// direction and the fall off the MOG2 points, so on a swing with no
// dots it has nothing to extend and says "the model could not draw a
// tail here". That is the swing an operator is most often looking at:
// the one the produce could not do by itself.
//
// But the picture shows where the ball finished, and the shape in
// between is not a mystery. So: a landing placed on the tee view, an
// apex that can be dragged anywhere -- including off the top of the
// frame, which is where a real golf shot goes -- and the frames in
// between filled with the same per-frame points a hand plot makes.
// Nothing new downstream; the arc just fills them in faster than
// clicking two hundred of them would.
//
// AND THE POINTS SHOW UP NOW. `marks` was only ever read to decorate
// a detected dot -- "is this one queued?" -- so a point plotted where
// the detector found nothing matched no dot and was drawn nowhere.
// The click registered, the mark was stored, the save used it, and
// the picture did not change.
//
//   node tools/flight_arc_test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(
  new URL("../frontend/src/pages/AdminProduction.jsx", import.meta.url),
  "utf8");

function grab(name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found in AdminProduction.jsx`);
  let d = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { d++; started = true; }
    else if (src[j] === "}") {
      d--; if (started && d === 0) return src.slice(i, j + 1);
    }
  }
  throw new Error(`${name} did not close`);
}

// THE PAGE'S OWN FUNCTIONS, not a copy of them. A copy would pass
// this test forever while the page moved underneath it.
const { arcAt, defaultApex } = await import(
  "data:text/javascript," + encodeURIComponent(
    grab("arcAt") + "\n" + grab("defaultApex")
    + "\nexport { arcAt, defaultApex };"));

let failed = [];
function check(label, got, want = true) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label.padEnd(58)} ${ok ? "ok" : `FAIL got ${
    JSON.stringify(got)}`}`);
  if (!ok) failed.push(label);
}
const near = (a, b, tol = 0.51) => Math.abs(a - b) <= tol;

const TEE = { x: 1410, y: 590 };     // the ball on the tee peg
const LAND = { x: 1060, y: 548 };    // beside the flag, up the hole

console.log("the arc, and the points that were never drawn");

console.log("\n1. it starts and ends where it was told to");
{
  const apex = defaultApex(TEE, LAND);
  const a = arcAt(TEE, LAND, apex, 0);
  const b = arcAt(TEE, LAND, apex, 1);
  check("t=0 is the tee", [near(a.x, TEE.x), near(a.y, TEE.y)],
        [true, true]);
  check("t=1 is the landing", [near(b.x, LAND.x), near(b.y, LAND.y)],
        [true, true]);
}

console.log("\n2. and it goes THROUGH the apex handle");
{
  // A PLAIN QUADRATIC BÉZIER DOES NOT. The curve reaches half way to
  // its control point, so an apex handle dragged to the treeline
  // tops the ball out half way there -- the operator drags, the ball
  // does not follow, and the control is useless. Solving the control
  // point backwards from the midpoint is what makes "apex" mean what
  // it says.
  for (const apex of [{ x: 1235, y: 300 }, { x: 1100, y: -240 },
                      { x: 1380, y: 120 }]) {
    const m = arcAt(TEE, LAND, apex, 0.5);
    check(`apex ${apex.x},${apex.y} is on the curve`,
          [near(m.x, apex.x), near(m.y, apex.y)], [true, true]);
  }
}

console.log("\n3. the default is a flight, not a straight line");
{
  const apex = defaultApex(TEE, LAND);
  const chordMidY = (TEE.y + LAND.y) / 2;
  check("the apex is above the line between the two",
        apex.y < chordMidY);
  check("...over the middle of it", near(apex.x, (TEE.x + LAND.x) / 2, 1));
  // AND THE HEIGHT IS IN PROPORTION TO THE SHOT. A fixed number of
  // pixels is a mountain on a chip and a flat line on a drive.
  const far = defaultApex(TEE, { x: 300, y: 540 });
  check("a longer shot gets a higher default",
        (chordMidY - apex.y) < ((TEE.y + 540) / 2 - far.y));
}

console.log("\n4. the apex may be above the frame, and the arc goes there");
{
  // THE REASON THE HANDLE IS UNCLAMPED. From a camera at tee level a
  // struck ball leaves the top of the picture, so an apex pinned to
  // y=0 caps every arc at the skyline.
  const apex = { x: 1235, y: -260 };
  const ys = [];
  for (let i = 0; i <= 20; i++) ys.push(arcAt(TEE, LAND, apex, i / 20).y);
  check("the curve rises above the top of the frame",
        ys.some((y) => y < 0));
  check("...and still comes back down to the landing",
        near(ys[ys.length - 1], LAND.y));
  // IT IS NOT CLIPPED ON THE WAY: the points above the frame are
  // real points on real frames, and the renderer draws what it can
  // see of the line through them.
  check("...passing through the apex itself", near(
    arcAt(TEE, LAND, apex, 0.5).y, -260));
}

console.log("\n5. one point per frame, inside the swing's own window");
{
  // THE FUNCTION ITSELF, not the rest of the file after it: an
  // unbounded slice makes every "this is absent" check below pass on
  // the strength of some other function a thousand lines later.
  const build = grab("buildArc");
  check("a point for every frame, not a sparse few",
        /for \(let f = f0 \+ 1; f <= f1; f \+= 1\)/.test(build));
  // ── THE FOOTAGE IS THE ONLY LIMIT ──────────────────────────────
  //
  // It used to stop at `swing.end_frame` -- the window a PREVIOUS
  // produce chose, often a second or two after impact -- and the arc
  // was silently squeezed into it. The flight then ran its whole
  // length inside that second: up and down in a blink, with nothing
  // on screen saying the duration had been overruled. The window is
  // widened to hold the points instead; see `renderWindow`.
  check("the clip's last frame is the bound",
        /const lastF = row\.tee_nb_frames \? row\.tee_nb_frames - 1 : null;/
          .test(build));
  check("...and the window a previous produce chose is not",
        /const lastF = swing\.end_frame/.test(build), false);
  check("the saved window is widened to hold the points",
        /const hi = Math\.max\(baseWindow\.end_frame, \.\.\.fs\);/
          .test(src));
  check("...clamped to the footage that exists",
        /end_frame: lastF_ == null \? hi : Math\.min\(hi, lastF_\),/
          .test(src));
  // ITS OWN PREVIOUS POINTS GO AND NOTHING ELSE DOES: re-drawing
  // after a drag must not leave the old curve behind, and must not
  // take a hand-plotted ascent with it.
  check("re-drawing replaces only the arc's own points",
        /for \(const f of arcFrames\) delete kept\[f\];/.test(build));
}

console.log("\n5b. and it lasts as long as the shot does");
{
  // THE ARC IS PACED BY THE FRAMES IT COVERS, so the duration IS the
  // speed: the same curve over a fifth of the frames is the same shot
  // at five times the speed, which is what "it seems to be going too
  // fast" looked like -- a 145-yard shot up and down inside a second.
  check("a hard-coded default is not the answer",
        /const arcSec = flightSec != null \? flightSec : 4\.5;/.test(src),
        false);
  // IN ORDER OF AUTHORITY: what the operator set, then the two
  // cameras' own clocks, then the hole's yardage through the
  // carry-time table the renderer itself paces with.
  check("what the operator set wins",
        /const arcSec = \(arcFlight != null && arcFlight > 0\) \? arcFlight/
          .test(src));
  check("...then anything measured", /: \(flightSec != null && flightSec > 0 \? flightSec : null\);/
    .test(src));
  check("...then the table, fetched from the server",
        /async function askFlightSec\(\)/.test(src)
          && /setArcFlight\(Number\(out\.flight_sec\)\)/.test(src));
  // NOT COMPUTED IN THE BROWSER. A second copy of the carry-time
  // table is a copy that drifts, and the one that drifts is the one
  // the operator is looking at.
  check("the table itself is not copied here",
        /3\.1 \+ \(y - 75/.test(src), false);
  check("asked for when the arc is armed, not mid-drag",
        /askFlightSec\(\);\s*\n\s*setPlaceOnTee/.test(src));
  check("the operator can overrule it", /setArcFlight\(v\); setArcFlightFrom\("you"\);/
    .test(src));
  check("...and changing it re-draws the arc",
        /buildArc\(arcEnd, arcApex\);\s*\n\s*\/\/ eslint-disable-next-line[\s\S]{0,60}\}, \[arcSec\]\);/
          .test(src));
  check("...and the panel says whose number it is",
        /arcFlightFrom === "you" \? "— set here"/.test(src));
}

console.log("\n6. it picks up from what is already plotted");
{
  const start = src.slice(src.indexOf("const arcStart = (() => {"),
                          src.indexOf("function defaultApex("));
  check("hand-plotted points are found", /\.sort\(\(a, b\) => a\.frame - b\.frame\)/
    .test(start));
  check("...the arc's own are not counted as them",
        /!own\.has\(p\.frame\)/.test(start));
  check("...and the LAST of them is where the arc begins",
        /hand\[hand\.length - 1\]/.test(start));
  check("with the tee as the fallback", /from: "tee"/.test(start));
  check("...and no arc at all without one", /if \(!b\) return null;/
    .test(start));
}

console.log("\n7. the picture draws what has been plotted");
{
  // THE BUG: `marks` was read ONLY to decorate a detected dot.
  check("the canvas takes a path to draw", /markPath,/.test(src));
  check("...and the tee view hands it one", /markPath=\{markPath\}/.test(src));
  check("...built from every mark, in frame order",
        /const markPath = Object\.entries\(marks\)/.test(src)
          && /\.sort\(\(a, b\) => a\.frame - b\.frame\)/.test(src));
  check("...drawn as a line and its points",
        /points=\{markPath\.map\(\(q\) => `\$\{q\.x\},\$\{q\.y\}`\)/.test(src)
          && /markPath\.map\(\(q, i\) => \(/.test(src));
  // AND A MIS-CLICK IS CORRECTABLE. The same bargain the green view
  // already makes: a point put down wrong should not mean clearing
  // the chain and starting again.
  check("a hand-plotted point can be dragged",
        /id: `mark-\$\{q\.frame\}`, x: q\.x, y: q\.y, icon: "ball"/.test(src));
  check("...but the arc's hundreds are not each a handle",
        /\.filter\(\(q\) => !arcOwned\.has\(q\.frame\)\)/.test(src));
  // ON EVERY POINTER MOVE OF A DRAG, against one point per frame: a
  // linear scan inside a filter over the same list is the drag going
  // sticky at the moment it has to feel direct.
  check("...and that test is a set lookup",
        /const arcOwned = new Set\(arcFrames\);/.test(src));
}

console.log("\n8. and the handles are wired to the drag");
{
  check("the landing is a handle", /id: "arcend"/.test(src));
  check("the apex is a handle", /id: "arcapex"/.test(src));
  check("...which may leave the frame", /unclamped: true/.test(src));
  check("the stage stops clamping for it",
        /function stageXY\(e, loose = false\)/.test(src)
          && /if \(loose\) return \{ x, y \};/.test(src));
  check("...and both drags re-draw the curve",
        /if \(id === "arcend"\) buildArc\(pt, arcApex\);/.test(src)
          && /if \(id === "arcapex"\) buildArc\(arcEnd, pt\);/.test(src));
  // A HANDLE DRAWN AT A NEGATIVE PERCENTAGE IS CLIPPED BY THE STAGE
  // AND CANNOT BE GRABBED BACK.
  check("an out-of-frame handle rides the edge",
        /Math\.max\(h\.unclamped \? frameH \* 0\.02 : 0, h\.y\)/.test(src));
  check("...and says how far past it the point is",
        /px above frame/.test(src));
}

console.log("\n9. the arc is ONE thing in the list below the map");
{
  // WHAT WENT WRONG THE FIRST TIME IT WAS USED. That list is of
  // things to remove ONE AT A TIME, which is what a hand-placed point
  // is. An arc is not: it is one decision that happens to be three
  // hundred points, and a chip for each pushed the map itself down to
  // a strip at the top of the modal — the whole screen a wall of
  // frame numbers nobody would ever click.
  check("the arc's frames are not chips",
        /\.filter\(\(f\) => Number\.isFinite\(f\) && !arcOwned\.has\(f\)\)/
          .test(src));
  check("...it gets one chip that removes the arc",
        /arc f\{arcFrames\[0\]\}–f\{arcFrames\[arcFrames\.length - 1\]\}/
          .test(src));
  // AND THE HAND POINTS ARE CAPPED TOO. A green crawl merges its
  // whole chain into the marks, which is the same wall by another
  // road.
  check("hand points are capped", /all\.slice\(0, 60\)/.test(src));
  check("...and the rest are counted, not hidden",
        /more: Math\.max\(0, all\.length - 60\)/.test(src)
          && /\{handChips\.more\} more/.test(src));
}

console.log("\n10. clearing the arc leaves the hand work alone");
{
  const clear = src.slice(src.indexOf("function clearArc()"),
                          src.indexOf("function clearArc()") + 420);
  check("only the arc's frames are removed",
        /for \(const f of arcFrames\) delete kept\[f\];/.test(clear));
  check("...and the handles go with them",
        /setArcApex\(null\);/.test(clear) && /setArcEnd\(null\);/.test(clear));
  // CLEAR EVERYTHING STILL MEANS EVERYTHING, and must not leave the
  // arc's frame list behind pointing at marks that no longer exist.
  check("clearing all marks forgets the arc too",
        /setMarks\(\{\}\);\s*\n\s*setArcFrames\(\[\]\);/.test(src));
}

console.log("\n" + (failed.length
  ? `${failed.length} FAILED:\n  ${failed.join("\n  ")}` : "all ok"));
process.exit(failed.length ? 1 : 0);
