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
print("every helper it names exists at module level:")
# EVERY _name( CALLED IN THE VERIFIER BLOCK. A nested helper from
# another endpoint resolves at import time to nothing, and the failure
# arrives as a swallowed NameError at the one moment someone is
# watching for a picture.
called = set(re.findall(r"\b(_[a-z][a-z0-9_]*)\(", src))
for name in sorted(called):
    if name in params or name in ("_j", "_chk"):
        continue
    have = hasattr(admin, name) or name in src.split("def ")[0]
    # Locally defined inside the endpoint counts too.
    if not have:
        have = re.search(r"\n\s+def %s\(" % re.escape(name), src) is not None
    print("  %-56s %s" % (name, "ok" if have else "FAIL: not in reach"))
    if not have:
        FAILED.append(name)

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
