#!/usr/bin/env python3
"""Nobody calls the produce job by POSITION.

A TEN-ITEM TUPLE AND A SIGNATURE THAT GREW IN THE MIDDLE.

`run_wizard_produce_job` gained a `descent_first_frame` parameter
between `landing_spot` and `solo`. One caller -- the wizard-produce
HTTP endpoint -- was handing it a positional tuple, so from that commit
onwards `solo` arrived as the descent's first frame, `swing_idx` as
`solo`, the operator's plotted `points` list as `swing_idx`, and
`launch_frame` was never passed at all.

Nothing raised. A bool is a valid frame number, a list is a valid
index, and every one of those parameters has a default -- so the only
symptom was produced clips that were quietly wrong, from the edit
wizard and from click-to-plot, in both deployments.

This is a test about the SHAPE of the call rather than the behaviour,
because the behaviour was indistinguishable: the job ran, returned ok,
and wrote a clip. It reads the router's own source and insists that
every call to that function names what it passes.

    python3 tools/produce_call_args_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "app" / "routers" / "admin.py"

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


tree = ast.parse(SRC.read_text(encoding="utf-8"))

# THE PARAMETERS WITH DEFAULTS ARE THE DANGEROUS ONES. A caller that
# fills them positionally is relying on their order, and their order is
# exactly what a later commit is free to change.
fn = next(
    (n for n in ast.walk(tree)
     if isinstance(n, ast.FunctionDef) and n.name == "run_wizard_produce_job"),
    None,
)
print("the job's signature:")
check("run_wizard_produce_job is in the router", fn is not None, True)
_names = [a.arg for a in (fn.args.args if fn else [])]
_n_req = len(_names) - len(fn.args.defaults) if fn else 0
check("three parameters are required", _n_req, 3)
check("the required three are the ball and the frame",
      _names[:3], ["upload_id", "ball_xy", "impact_frame"])
check("the descent's first frame is one of the optional ones",
      "descent_first_frame" in _names[_n_req:], True)

print()
print("every call to it:")

# `_heavy_thread(run_wizard_produce_job, args=(...))` passes the tuple
# through to the target, so it counts as positional even though the
# call itself looks like a keyword call.
_calls = 0
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    _f = node.func
    _direct = isinstance(_f, ast.Name) and _f.id == "run_wizard_produce_job"
    _threaded = (
        isinstance(_f, ast.Name) and _f.id == "_heavy_thread"
        and node.args and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "run_wizard_produce_job"
    )
    if not (_direct or _threaded):
        continue
    _calls += 1
    _line = node.lineno
    if _direct:
        _pos = len(node.args)
        check("line %d passes nothing positional past the first three"
              % _line, _pos <= 3, True)
    else:
        _args_kw = next((k for k in node.keywords if k.arg == "args"), None)
        _n = (len(_args_kw.value.elts)
              if _args_kw is not None
              and isinstance(_args_kw.value, (ast.Tuple, ast.List))
              else 0)
        check("line %d hands the thread no positional tail" % _line,
              _n <= 3, True)
        check("line %d hands the thread a kwargs mapping" % _line,
              any(k.arg == "kwargs" for k in node.keywords), True)

check("there is at least one call to check", _calls >= 1, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
