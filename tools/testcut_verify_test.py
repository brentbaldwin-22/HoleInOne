#!/usr/bin/env python3
"""The Test Cut verifier's wiring: names that exist, in reach.

THE BUG THIS EXISTS FOR was a helper called by the wrong name.
`_clip_url` is nested inside a different endpoint; the module-level one
is `_clip_url_for`. The call sat inside the verifier's own try/except,
so the NameError it raised was swallowed and reported as "verify
failed" -- forever, on every peak, with no picture and no hint that the
cause was a typo rather than a missing API key.

Nothing here needs a video, a model or a key: it asks whether the
endpoint can reach what it calls, which is what silently broke.

    python3 tools/testcut_verify_test.py
"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.routers import admin  # noqa: E402
from backend.app.services.tracer import swing_heat_check  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


src = inspect.getsource(admin.test_cut_long_upload)
params = inspect.signature(admin.test_cut_long_upload).parameters

print("the verifier is reachable from Test Cut:")
check("the endpoint takes verify_swings", "verify_swings" in params, True)
check("...and a window", "verify_window_sec" in params, True)
check("off by default", params["verify_swings"].default.default, False)
check("half a second either side",
      params["verify_window_sec"].default.default, 0.5)

print()
print("every name the endpoint uses resolves to something:")
# THE CHECK THAT MATTERS, and the first version was too narrow: it
# looked only at _underscore_ CALLS, so it caught `_clip_url` and sailed
# past `tee_path` -- an ordinary variable, misremembered, in the same
# swallowed try/except, reported as "verify failed: name 'tee_path' is
# not defined" after the operator had gone looking for a picture.
#
# So: every bare name the function loads must be a parameter, something
# it assigns, a module global, or a builtin. Anything else is a
# NameError waiting for the one code path that reaches it.
import builtins  # noqa: E402
import ast  # noqa: E402

tree = ast.parse(inspect.getsource(admin.test_cut_long_upload).lstrip())
fn = tree.body[0]
assigned = set()
for node in ast.walk(fn):
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        assigned.add(node.id)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        assigned.add(node.name)
    elif isinstance(node, ast.alias):
        assigned.add((node.asname or node.name).split(".")[0])
    elif isinstance(node, ast.ExceptHandler) and node.name:
        assigned.add(node.name)
    elif isinstance(node, (ast.comprehension,)):
        for t in ast.walk(node.target):
            if isinstance(t, ast.Name):
                assigned.add(t.id)
known = (assigned | set(params) | set(dir(admin))
         | set(dir(builtins)) | {"self"})
unresolved = sorted({
    n.id for n in ast.walk(fn)
    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    and n.id not in known
})
print("  %-56s %s" % ("no name is loaded that was never bound",
                      "ok" if not unresolved else "FAIL: %s" % unresolved))
if unresolved:
    FAILED.append("unresolved names")

print()
print("the row carries what the table columns read:")
# cut_entry copies NAMED KEYS, not the window, so a measurement added
# upstream reaches the UI only if it is named here too. attack_ms was
# printed in the debug line and dashed in the swing's own row for
# exactly this reason.
esrc = inspect.getsource(admin.test_cut_long_upload)
for key in ("attack_ms", "attack_measured", "max_attack_ms",
            "peak_time_sec", "ratio", "confidence"):
    have = '"%s": w.get("%s")' % (key, key) in esrc or '"%s":' % key in esrc
    print("  %-56s %s" % ("cut_entry carries %s" % key,
                          "ok" if have else "FAIL: dropped"))
    if not have:
        FAILED.append(key)

print()
print("the heat window is a parameter, and produce keeps its old one:")
hp = inspect.signature(swing_heat_check).parameters
check("before_sec exists", "before_sec" in hp, True)
check("after_sec exists", "after_sec" in hp, True)
check("default before is the old 2.0", hp["before_sec"].default, 2.0)
check("default after is the old 2.5", hp["after_sec"].default, 2.5)

# THE WARMUP MUST NOT EAT THE WINDOW. At 60fps a +/-0.5s window is 60
# frames and the 12-frame warmup would cost a fifth of the swing, so
# the pass has to start before the window rather than inside it.
hsrc = inspect.getsource(swing_heat_check)
check("heat is counted from its own cursor, not from the pass start",
      "idx < accum_f0" in hsrc, True)
check("...and the pass starts earlier than the window",
      "_lead" in hsrc and "start_f = max(0, int(" in hsrc, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
