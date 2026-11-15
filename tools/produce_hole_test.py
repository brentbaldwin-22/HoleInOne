#!/usr/bin/env python3
"""Which hole a produce says it is, and never 1 as a guess.

A camera bolted to hole 8 produced a clip captioned "hole 1", with a
distance plate reading 1337ft -- hole 1's yardage looked up instead of
hole 8's -- and a "hole 1 is not in this course's par-3 list [2, 8, 14,
17]" note in the log that nobody could act on, because nothing said
where the 1 came from.

The hole was knowable the whole time. The upload had a camera event,
the event carries the camera's `assigned_hole`, and
`_hole_for_upload_ex` already reads that chain: camera event, import,
finalised, clips, and only then a default it NAMES as a default.

TWO CHAINS ANSWERED THE SAME QUESTION. `_view_map_for` asked
`_hole_for_upload_ex` and got 8, so the green->tee calibration was
found under the right hole. `run_produce_job` asked nobody and went
caller -> clips -> 1, so the caption was wrong while the aiming was
right. One question, one chain.

AND AN EXPLICIT 1 IS NOT AN ANSWER, IT IS A GAG. A caller's hole
short-circuits the chain, so the two routes that passed a literal 1 --
quick-upload, and the dual-camera upload when the operator left the
hole blank -- were not saying "hole 1", they were stopping the chain
from running at all.

    python3 tools/produce_hole_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "app" / "routers" / "admin.py"
SRC_TEXT = SRC.read_text(encoding="utf-8")
TREE = ast.parse(SRC_TEXT)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def fn_named(name):
    return next((n for n in ast.walk(TREE)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


# ---------------------------------------------------------------- 1
print("\n-- the chain exists and names its default")
ex = fn_named("_hole_for_upload_ex")
check("_hole_for_upload_ex is in the router", ex is not None, True)
_src_ex = ast.get_source_segment(SRC_TEXT, ex) or ""
# Every link, in order. A reordering here is a behaviour change and
# should have to come past this line.
_order = [w for w in ("camera_event", "import", "finalized", "clip",
                      "default")
          if '"%s"' % w in _src_ex]
check("it reports the source, camera event first, default last",
      _order, ["camera_event", "import", "finalized", "clip", "default"])


# ---------------------------------------------------------------- 2
print("\n-- produce asks that chain instead of defaulting to 1")
# THE WORK IS IN `_d3_fast_produce`, not in the thin `run_produce_job`
# wrapper -- which is itself worth pinning, because looking in the
# wrapper and finding nothing is how this was first read as fixed.
rp = fn_named("_d3_fast_produce")
check("_d3_fast_produce is in the router", rp is not None, True)
_src_rp = ast.get_source_segment(SRC_TEXT, rp) or ""
check("it calls _hole_for_upload_ex",
      "_hole_for_upload_ex(db, row)" in _src_rp, True)
# THE OLD SHAPE, GONE. `next((...), 1)` straight off the clips was the
# whole bug: it skipped the camera event entirely.
check("no bare `else 1` hole default survives in it",
      "_hole = int(hole_number) if hole_number else 1" in _src_rp, False)
check("it records where the hole came from",
      '"hole_source"' in _src_rp, True)
check("it says so in the log",
      "is hole %s (from %s)" in _src_rp, True)
# The clips are still a fallback -- they are the only thing that knows
# on an upload whose camera event was deleted.
check("the clips are still consulted as a fallback",
      "int(c.hole_number) for c in _all_old" in _src_rp, True)


# ---------------------------------------------------------------- 3
print("\n-- no caller gags the chain with a literal 1")
# Every enqueue_produce_job / run_produce_job call in the router, with
# the hole it passes. A literal 1 is the thing being banned: it is
# indistinguishable from a real hole 1 and it stops the chain.
bad = []
for node in ast.walk(TREE):
    if not isinstance(node, ast.Call):
        continue
    _f = node.func
    _name = getattr(_f, "id", None) or getattr(_f, "attr", None)
    if _name not in ("enqueue_produce_job", "run_produce_job"):
        continue
    for kw in node.keywords:
        if kw.arg != "hole_number":
            continue
        _v = kw.value
        # `hole_number=1`
        if isinstance(_v, ast.Constant) and _v.value == 1:
            bad.append("line %d: literal 1" % node.lineno)
        # `hole_number=int(x or 1)` -- the same guess wearing a coat
        if isinstance(_v, ast.Call) and getattr(_v.func, "id", "") == "int" \
                and _v.args and isinstance(_v.args[0], ast.BoolOp) \
                and isinstance(_v.args[0].op, ast.Or) \
                and any(isinstance(q, ast.Constant) and q.value == 1
                        for q in _v.args[0].values):
            bad.append("line %d: int(... or 1)" % node.lineno)
check("no produce caller passes a hole of 1", bad, [])

# CONTROL: this test can actually see those calls. Without it an
# empty `bad` would also be what a broken walk returns.
_seen = sum(
    1 for n in ast.walk(TREE)
    if isinstance(n, ast.Call)
    and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
    in ("enqueue_produce_job", "run_produce_job")
    and any(k.arg == "hole_number" for k in n.keywords)
)
check("control, the walk does find produce calls passing a hole",
      _seen >= 3, True)


print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
