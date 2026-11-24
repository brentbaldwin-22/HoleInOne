#!/usr/bin/env python3
"""EVERY 9:16 RENDER FRAMES THE SAME WAY.

`_vertical_pan_path` decides the framing and is shared, so in principle
every clip already gets the same treatment. In practice the CALL SITES
have drifted apart three separate times, and each time it looked like a
bug in the framing rather than a bug in one caller:

  _green_ball_x   Produce took the green from metadata ("ball 1", an
                  overflow rather than a position) while the re-render
                  read it off the picture. Same clip, two framings.
  the green scan  Added to the on-demand path only, so Produce never
                  looked for the green tracer at all.
  total_dur       Produce probed seg_path -- the TEE segment -- while
                  rendering the composite, reporting a 7s duration for
                  a 12.2s clip. The green half measured as zero and the
                  crop snapped to the pin, which is how `gball 0.323 ·
                  open@cut 0.679` happened.

None of those were visible in the shared function. All three were one
caller passing something the others did not. So this walks the source
and checks every call carries the arguments that decide the framing.

WHAT IT CANNOT CATCH: an argument sourced from the wrong variable. The
total_dur bug passed an argument of the right name holding the wrong
number, and only a real render showed it. This narrows the gap; it does
not close it.

    python3 tools/vertical_callsites_test.py
"""

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "backend/app/routers/admin.py").read_text()

# Without these the clip is framed by guesswork, and the guess differs
# per caller -- which is precisely how the three bugs above happened.
REQUIRED = {
    "opening_x",    # where the tee half opens: the tracer's foot
    "opening_t",    # and when, so the flight cannot move the camera early
    "ball_green_x", # where the green half opens: where the ball came down
    "green_x",      # where it walks to: the flag
    "total_dur",    # how long the beats get
}


def calls():
    """Every call to _vertical_pan_path, with its line and keywords."""
    for m in re.finditer(r"(?<!def )_vertical_pan_path\(", SRC):
        i, depth = m.end(), 1
        while depth:
            if SRC[i] == "(":
                depth += 1
            elif SRC[i] == ")":
                depth -= 1
            i += 1
        yield (SRC[:m.start()].count("\n") + 1,
               set(re.findall(r"(\w+)=", SRC[m.end():i - 1])))


def main() -> int:
    found = list(calls())
    fails = []

    print(f"{len(found)} call site(s):\n")
    for line, kw in found:
        missing = REQUIRED - kw
        mark = "ok" if not missing else f"MISSING {sorted(missing)}"
        print(f"   admin.py:{line:<6} {mark}")
        if missing:
            fails.append(f"admin.py:{line} omits {sorted(missing)}")

    # There are three renders of a 9:16 -- Produce, the on-demand
    # re-render, and the metadata-less fallback. A fourth appearing
    # without this file being thought about is the thing to catch.
    if len(found) != 3:
        fails.append(
            f"expected 3 call sites, found {len(found)} -- a new 9:16 "
            "render needs the same arguments as the others")

    # The green scans must skip the baseline. Subtracting "every blue
    # thing before the ball is struck" erases the green tracer, because
    # after the cut the ball is already in flight. That was "gball no
    # shape" on every clip for as long as it existed.
    greens = SRC.count("use_baseline=False")
    print(f"\n   green tracer scans skipping the baseline: {greens}")
    if greens != 3:
        fails.append(
            f"{greens} green scans skip the baseline, expected 3 -- one "
            "that does not cannot see its own tracer")

    # ...and look at every frame. The green stroke does not stay; at
    # step=2 whether it is seen at all is a coin toss.
    step1 = SRC.count("use_baseline=False, step=1")
    print(f"   ...and reading every frame: {step1}")
    if step1 != 3:
        fails.append(
            f"{step1} green scans use step=1, expected 3 -- the green "
            "stroke can last a handful of frames")

    if fails:
        print("\nFAILED:")
        for f in fails:
            print("   " + f)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
