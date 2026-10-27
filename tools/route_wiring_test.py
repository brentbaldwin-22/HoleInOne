#!/usr/bin/env python3
"""Is every route still attached to the handler it was written for?

A DECORATOR BINDS TO WHATEVER FUNCTION FOLLOWS IT. Insert a helper
between @router.post(...) and its handler -- which is easy to do by
anchoring an edit on the `def` line rather than the decorator -- and
the route silently starts serving the helper. The real handler becomes
an ordinary function nobody calls, nothing fails at import, and the
tests pass.

On 2026-10-27 that happened to /event-trigger. The route bound itself
to `_parse_trigger_detail(raw)`, FastAPI read `raw` as a required query
parameter, and every trigger from both rigs came back:

    422 {"loc":["query","raw"],"msg":"Field required"}

The Pis recorded anyway -- they are built to survive a backend that
cannot be reached -- so the only symptom on the course was that nothing
appeared on the events page, which looks exactly like a camera that has
stopped triggering.

    python3 tools/route_wiring_test.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.main import app  # noqa: E402

FAILED = []

# ---- no route may serve a private helper -------------------------------
print("every route serves a public handler, not a helper:")
bad = []
for route in app.routes:
    fn = getattr(route, "endpoint", None)
    if fn is None:
        continue
    name = getattr(fn, "__name__", "")
    if name.startswith("_"):
        bad.append("%s -> %s" % (getattr(route, "path", "?"), name))
print("  %-54s %s" % ("none bound to a _private function",
                      "ok" if not bad else "FAIL: %s" % bad))
if bad:
    FAILED.append("private handlers")

# ---- the Pi-facing routes keep the arguments the agents send -----------
# These are the calls a rig cannot complete without. A required
# parameter appearing here that the agent does not send is a 422 on
# every swing, and the agent's own resilience hides it from everyone
# except whoever reads the journal.
EXPECTED = {
    "/api/cameras/{token}/event-trigger": {
        "handler": "event_trigger",
        "required": {"token", "session_id"},
        "optional": {"recover", "impact_at", "clock_offset_sec",
                     "trigger_detail"},
    },
}
print()
print("the agent-facing routes take what the agents actually send:")
by_path = {getattr(r, "path", ""): r for r in app.routes}
for path, want in EXPECTED.items():
    route = by_path.get(path)
    if route is None:
        print("  %-54s %s" % (path, "FAIL: route is gone"))
        FAILED.append(path)
        continue
    got_name = route.endpoint.__name__
    ok = got_name == want["handler"]
    print("  %-54s %s" % ("%s serves %s" % (path, want["handler"]),
                          "ok" if ok else "FAIL: serves %s" % got_name))
    if not ok:
        FAILED.append(path)
        continue
    params = inspect.signature(route.endpoint).parameters
    missing = want["required"] - set(params)
    print("  %-54s %s" % ("...and still takes %s" % sorted(want["required"]),
                          "ok" if not missing else "FAIL: lost %s" % missing))
    if missing:
        FAILED.append("%s params" % path)
    # Anything REQUIRED that the agent does not send is a 422 per swing.
    unexpected = {
        n for n, p in params.items()
        if p.default is inspect.Parameter.empty
        and n not in want["required"] and n != "db"
    }
    print("  %-54s %s" % ("...and demands nothing else",
                          "ok" if not unexpected else
                          "FAIL: also requires %s" % sorted(unexpected)))
    if unexpected:
        FAILED.append("%s extra required" % path)
    strays = set(params) - want["required"] - want["optional"] - {"db"}
    if strays:
        print("  %-54s %s" % ("note: new optional params", sorted(strays)))

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
