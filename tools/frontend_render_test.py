#!/usr/bin/env python3
"""The admin components must survive being rendered.

A BLANK PAGE IS THE FAILURE MODE. React unmounts the whole tree when
a component throws during render, so one bad read in one cell takes
the entire admin page with it -- and `vite build` compiles every one
of these happily, because they are RUNTIME faults.

That shipped: the gate editor read `CONE` eleven lines before the
`const` declaring it. `const` is hoisted but not initialised, so
every render threw a ReferenceError and the operator got a white
screen on Find ascents.

ONE CHECK, DONE PROPERLY. Each component is bundled and rendered to a string with
          props shaped like the real ones, including a bare and an
          empty case -- which is how they arrive before a run
          finishes. This caught an unguarded `.toFixed` on a cut
          with no start_sec, as well as the bug it was written for.

  PAST THE LOADING GUARD, which is the part that matters. An
          editor that fetches its data bails on the first render, and
          `renderToString` never runs effects -- so the body below
          that early return is never reached, which is exactly where
          the shipped bug lived. The harness hands the first
          null-initialised state its seed, and fails a component that
          rendered too little to have been tested at all.

    python3 tools/frontend_render_test.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend"
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("\n-- every component renders, with real-shaped props")
if not (FE / "node_modules" / "esbuild").exists():
    print("  frontend deps are not installed here — render unchecked")
else:
    _r = subprocess.run(
        ["node", str(ROOT / "tools" / "render" / "smoke.mjs"), "."],
        cwd=FE, capture_output=True, text=True, timeout=600)
    for _line in (_r.stdout or "").strip().splitlines():
        print("  " + _line.strip())
    check("  none of them threw", _r.returncode, 0)
    if _r.returncode != 0:
        print((_r.stderr or "")[-800:])


# ── WHAT THIS DELIBERATELY DOES NOT DO ──────────────────────────────
#
# An earlier version of this file tried to catch use-before-declare by
# reading the source with regular expressions. It flagged twenty-four
# files, every one a false alarm: a function DEFINED above and CALLED
# below is legal and is the dominant shape in a React component, and a
# narrower rule still matched words inside comments. Telling those
# apart needs a real parser -- `eslint no-use-before-define` does it
# properly and is not installed here.
#
# A check that cries wolf is worse than none, because nobody reads the
# twenty-fifth line. So that check is gone, and the render above is
# what guards this: running the component body is a stricter test than
# any reading of it, and it caught the shipped bug by name --
# "Cannot access 'CONE' before initialization" -- on a file `vite
# build` compiles without complaint.

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
