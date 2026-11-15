#!/usr/bin/env python3
"""A GUESSED AIM POINT IS NOT AN OPERATOR DECISION.

A tee tracer came down on the right side of the green when the ball
had landed on the left, and no amount of re-plotting the ascent and
descent chains moved it. The chains were never what it was aiming at.

THE LOOP:

  1. The tracer editor opens on a swing with no aim point and fills one
     in FROM THE MODEL so there is an arc to look at and a handle to
     grab. `endGuessed` marks it as a guess, and the Save button
     correctly stays dark.
  2. But the save payload wrote `tracer_end` whenever one existed,
     guessed or not. So saving the swing for ANY other reason -- marking
     an impact frame, plotting a descent -- persisted the guess.
  3. Produce prefers a saved aim point over the green->tee mapping ON
     PURPOSE: a saved one is supposed to mean "the operator looked at
     the tee picture and said HERE".
  4. So from that save onwards every produce aimed at the guess and the
     calibration was never consulted again. A single wrong number,
     self-reinforcing, unreachable.

And there was no way to take one back: "Reset arc" restores the SAVED
aim point, which is no help when the saved one is what is wrong.

THREE THINGS KEEP IT SHUT, and this file checks all three:
  - a guess is never written,
  - a clearance IS written, as an explicit null (the merge keeps
    whatever it is not told about -- see the landing block beside it),
  - produce records WHICH source aimed the arc, because a mapping and an
    override are indistinguishable once both are just a pair of numbers.

    python3 tools/tracer_aim_source_test.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSX = ROOT / "frontend" / "src" / "pages" / "AdminProduction.jsx"
PY = ROOT / "backend" / "app" / "routers" / "admin.py"

J = JSX.read_text(encoding="utf-8")
P = PY.read_text(encoding="utf-8")

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("\n-- the editor knows a guess from a decision")
check("there is a guessed flag", "endGuessed" in J, True)
check("there is a cleared flag", "endCleared" in J, True)
# The seeding path is what makes a guess in the first place.
check("seeding an aim from the model marks it guessed",
      bool(re.search(r"setTracerEnd\(\{[^}]*predicted\[0\][^}]*\}\);\s*\n\s*"
                     r"setEndGuessed\(true\);", J)), True)
# A drag is the operator saying "here", so it clears BOTH flags.
check("dragging the handle clears guessed and cleared",
      bool(re.search(r"setTracerEnd\(pt\);\s*\n\s*setEndGuessed\(false\);"
                     r"\s*\n\s*setEndCleared\(false\);", J)), True)


print("\n-- the save never persists a guess")
# THE SHAPE OF THE WRITE. A bare `tracerEnd ? {...}` is the bug: it
# cannot tell a guess from a decision because it never asks.
_write = re.search(r"\.\.\.\(endCleared\s*\n?\s*\?\s*\{\s*tracer_end:\s*null\s*\}"
                   r"\s*\n?\s*:\s*\(tracerEnd\s*&&\s*!endGuessed", J)
check("tracer_end is written only when not guessed", bool(_write), True)
check("a clearance is written as an explicit null",
      "tracer_end: null" in J, True)
# THE OLD SHAPE, GONE.
check("no unconditional `tracerEnd ? {tracer_end:` write survives",
      bool(re.search(r"\.\.\.\(tracerEnd\s*\n?\s*\?\s*\{\s*tracer_end:", J)),
      False)


print("\n-- there is a way to take a saved aim back")
check("a control clears it to the mapping",
      "Use mapped landing" in J, True)
check("  and it sets the clearance flag",
      bool(re.search(r"setTracerEnd\(null\);\s*\n\s*setEndGuessed\(false\);"
                     r"\s*\n\s*setEndCleared\(true\);", J)), True)
check("  and a clearance makes Save available",
      bool(re.search(r"const shapeChanged =\s*\n\s*endCleared", J)), True)
# CONTROL: "Reset arc" is a DIFFERENT button and must stay. It restores
# the saved aim; the new one discards it. Collapsing them would take
# away the only way back from a bad drag.
check("control, Reset arc still exists", "Reset arc" in J, True)


print("\n-- produce says which source aimed the arc")
_fn = next((n for n in ast.walk(ast.parse(P))
            if isinstance(n, ast.FunctionDef)
            and n.name == "_d3_fast_produce"), None)
check("_d3_fast_produce is in the router", _fn is not None, True)
_src = ast.get_source_segment(P, _fn) or ""
check("the aim source is tracked", "_aim_src" in _src, True)
check("the mapping names itself", "the green→tee mapping" in _src, True)
check("an override says it overrides", "OVERRIDES the mapping" in _src, True)
check("it reaches the produce report", '"source": _aim_src' in _src, True)
check("  on both records", _src.count('"source": _aim_src'), 2)
check("and the log line carries it",
      "from %s" in _src and "an unrecorded source" in _src, True)
check("the page shows it", "tracer_aim.source" in J, True)


print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
