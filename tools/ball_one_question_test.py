#!/usr/bin/env python3
"""Test Cut and the find must ask the ball the same question.

THE BUG THIS PREVENTS, reported from the field with both panels open
on one clip. Test Cut said:

    a ball sat at (727, 727) in 2 of the first 3 frames and is gone
    from every frame after the strike -- FOUND BY THE STANCE BOX

and the find's own stage 1, on the same footage, said
NO_BALL_DETECTED.

Same function, same clip, different ARGUMENTS. Test Cut passes
`feet_x` and `zone_y`, which is what builds the narrow box beside the
golfer's feet; the find passed neither, so no box was built, the
search fell back to the whole crop, and a ball that is unmistakable
in a 10x box became one faint speck among a hundred across open turf.

It cost more than a panel disagreeing. The launch cone is anchored on
that ball, and without it the anchor falls back to the CENTRE of a
stance box -- tens of pixels off, which tilts every candidate line the
accumulator scores and put its bearing 11 degrees out.

`lag_sec` is the one argument that must NOT match: Test Cut hands the
raw audio peak and lets the function subtract the lag, while stage 1
has already corrected its own instant. Passing it there takes the lag
off twice, which this stage shipped once before.

    python3 tools/ball_one_question_test.py
"""
import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ball_vanish import ball_at_address  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


src = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
tree = ast.parse(src)


def kwargs_of(fn_name, callee="ball_at_address", alias=None):
    """Which keywords a function hands to the ball check."""
    fn = next(x for x in ast.walk(tree)
              if isinstance(x, ast.FunctionDef) and x.name == fn_name)
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        nm = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if nm in (callee, alias):
            out |= {k.arg for k in node.keywords if k.arg}
    return out


cut = kwargs_of("_test_cut_run")
find = kwargs_of("_ascent_ai_launch", alias="_baa")
print("Test Cut passes :", ", ".join(sorted(cut)) or "(none)")
print("the find passes :", ", ".join(sorted(find)) or "(none)")

print()
print("both actually call it:")
check("Test Cut calls the ball check", len(cut) > 0)
check("the find calls it too", len(find) > 0)

print()
print("the arguments that decide WHERE it looks must match:")
# These three are the search itself. Differ on any of them and the two
# screens are answering different questions about one clip.
for k in ("feet_x", "zone_y", "region_contrast"):
    check("both pass %s" % k, (k in cut, k in find), (True, True))
check("both pass the contrast bar",
      ("min_contrast" in cut, "min_contrast" in find), (True, True))

print()
print("and the one that must NOT match:")
# Test Cut hands the raw peak; stage 1's instant is already corrected.
check("Test Cut subtracts the lag inside the call", "lag_sec" in cut)
check("  and the find does not, having done it already",
      "lag_sec" in find, False)

print()
print("the two that legitimately differ, and why:")
# `handed` and `ai_xy` come from the AI judge, which Test Cut runs and
# stage 1 does not. They decide which SIDE of the feet the box goes,
# and the default is right-handed -- so a LEFT-handed golfer's ball
# can still be missed by the find while Test Cut finds it. Recorded
# here rather than left to be rediscovered.
check("Test Cut has the judge's handedness", "handed" in cut)
check("  stage 1 does not, and defaults to right-handed",
      "handed" in find, False)

print()
print("the function really takes them (not silently swallowed):")
_ps = inspect.signature(ball_at_address).parameters
for k in sorted(cut | find):
    check("ball_at_address accepts %s" % k, k in _ps)

print()
print("and the find reports what the check decided:")
_i = src.index("def _ascent_ai_launch")
_j = src.index("\ndef ", _i + 10)
_stage1 = src[_i:_j]
for k in ("address_why", "address_found_by", "address_min_contrast"):
    check("stage 1 records %s" % k, 'out["%s"]' % k in _stage1)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — one question, asked the same way twice")
