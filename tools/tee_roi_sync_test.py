#!/usr/bin/env python3
"""The two copies of tee_roi must agree, function for function.

THE AGENT SHIPS AS ITS OWN TREE with no path into the backend package,
so tee_roi is duplicated rather than imported. The server decides
whether a zone may be saved and the Pi decides whether a golfer is
standing in one; if those two ever answer differently, the symptom is
a camera that does not fire, which looks like hardware.

They drifted once already -- `which`, `seen_times` and `seen_in_zones`
went in on the Pi for the audio trigger and never reached the backend,
while the file's own docstring went on claiming the two were identical.
Nothing noticed, because nothing compared them.

    python3 tools/tee_roi_sync_test.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import tee_roi as pi          # noqa: E402
from backend.app.services import tee_roi as be  # noqa: E402

FAILED = []


def public(mod):
    return {n: f for n, f in vars(mod).items()
            if not n.startswith("_") and inspect.isfunction(f)
            and f.__module__ == mod.__name__}


pi_fns, be_fns = public(pi), public(be)

print("every function exists on both sides:")
for label, missing in (("missing from the backend", set(pi_fns) - set(be_fns)),
                       ("missing from the Pi", set(be_fns) - set(pi_fns))):
    ok = not missing
    print("  %-34s %s" % (label, "ok" if ok else "FAIL: %s" % sorted(missing)))
    if not ok:
        FAILED.append(label)

print()
print("and each one is the same code:")
for name in sorted(set(pi_fns) & set(be_fns)):
    a = inspect.getsource(pi_fns[name])
    b = inspect.getsource(be_fns[name])
    ok = a == b
    print("  %-34s %s" % (name, "ok" if ok else "FAIL: the bodies differ"))
    if not ok:
        FAILED.append(name)

# Constants count too: MAX_BOXES differing would let the server store a
# zone list the Pi silently truncates.
print()
print("and the constants match:")
for name in sorted(n for n in vars(pi)
                   if n.isupper() and not n.startswith("_")):
    got, want = getattr(be, name, "<missing>"), getattr(pi, name)
    ok = got == want
    print("  %-34s %s" % (name, "ok" if ok else "FAIL: %r vs %r" % (got, want)))
    if not ok:
        FAILED.append(name)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(str(f) for f in FAILED))
    print("Copy pi-agent/agent/tee_roi.py over "
          "backend/app/services/tee_roi.py, keeping each file's header.")
    sys.exit(1)
print("ALL PASS")
