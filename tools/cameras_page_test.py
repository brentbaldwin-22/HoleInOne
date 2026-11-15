#!/usr/bin/env python3
"""The cameras table, and the API calls a build cannot check.

`npm run build` type-checks nothing here: `api.listCameraEvents(key,
{cameraId, limit})` compiles perfectly and builds
"?limit=[object Object]", which returns a 422 and reads on screen as
a camera that has never recorded anything. That is the whole class of
bug this file is for -- a page calling the API with a shape the API
does not have.

It also holds the split the page is built on: the table answers "is
this alive", and everything else sits behind a button that names it.

    python3 tools/cameras_page_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def api_arity():
    """{name: (min_args, max_args)} from api.js's own definitions."""
    src = (SRC / "api.js").read_text()
    out = {}
    for m in re.finditer(r"^\s{2}(\w+):\s*\(([^)]*)\)\s*=>", src, re.M):
        name, args = m.group(1), m.group(2).strip()
        if not args:
            out[name] = (0, 0)
            continue
        parts = [a.strip() for a in re.split(r",(?![^{(\[]*[})\]])", args)]
        required = sum(1 for a in parts if "=" not in a)
        out[name] = (required, len(parts))
    return out


def main():
    page = (SRC / "pages" / "AdminCameras.jsx").read_text()
    arity = api_arity()
    ok("api.js parsed", len(arity) > 50, True)

    # ── EVERY api.X(...) THE PAGE MAKES, CHECKED ────────────────────
    calls = re.findall(r"api\.(\w+)\s*\(([^;]*?)\)(?=[\s,;.)]|$)", page)
    seen = []
    for name, args in calls:
        if name not in arity:
            _fails.append("api.%s does not exist" % name)
            print("FAIL api.%s is called and api.js has no such function"
                  % name)
            continue
        # Count top-level commas only: an object or template literal
        # argument contains commas of its own.
        depth, n = 0, (1 if args.strip() else 0)
        for ch in args:
            if ch in "({[":
                depth += 1
            elif ch in ")}]":
                depth -= 1
            elif ch == "," and depth == 0:
                n += 1
        lo, hi = arity[name]
        good = lo <= n <= hi
        seen.append((name, n, lo, hi, good))
        if not good:
            _fails.append("api.%s arity" % name)
        print("%-4s api.%-22s called with %d arg(s), takes %d..%d"
              % ("ok" if good else "FAIL", name, n, lo, hi))
    ok("the page calls the API at all", len(seen) >= 6, True)

    # ── THE COLUMNS THE PAGE IS FOR ─────────────────────────────────
    for col in ("Camera", "Course", "Hole", "Role", "Status", "Functions"):
        ok("the table has a %s column" % col,
           '"%s"' % col in page, True)

    # ── AND THE SIX FUNCTIONS, EACH BEHIND ITS OWN BUTTON ───────────
    for fn in ("Details", "Live", "Edit", "Events", "Setup"):
        ok("  %s is a button" % fn, 'label="%s"' % fn in page, True)
    ok("  and disable toggles rather than being one-way",
       '"Enable" : "Disable"' in page, True)

    # ── SETUP IS ROLE-AWARE ─────────────────────────────────────────
    #
    # A green camera has no trigger zones and a tee camera has no
    # green to outline. Showing both to both is how an operator draws
    # the wrong thing.
    ok("setup knows which camera it is on",
       'cam.assigned_role === "green"' in page, True)
    ok("  tee gets trigger zones", 'label="Sky line"' in page, True)
    ok("  green gets the outline", '"Green outline"' in page, True)
    ok("  and the editors draw over a still, not a form",
       "portalTarget={pictureEl}" in page, True)
    ok("  at the frame's NATURAL size, not its displayed one",
       "naturalWidth" in page, True)

    # ── THE PI-BOUND CONTROLS KNOW WHEN THE PI IS GONE ──────────────
    ok("offline disables what needs the Pi", "agentIsDown(cam)" in page, True)
    ok("  and says why rather than going silently grey",
       "offlineReason(cam)" in page, True)
    ok("  while pausing stays live, being the backend's switch",
       "triggeringEnabled" in page, True)

    # ── ONE DEFINITION OF 'LATE', SHARED WITH THE OLD CONSOLE ───────
    old = (SRC / "pages" / "AdminCamerasOld.jsx").read_text()
    ok("the old console still exists", len(old) > 1000, True)
    ok("  and imports the shared status helpers",
       "from \"../components/cameraStatus.js\"" in old, True)
    ok("  keeping no copy of its own",
       "function heartbeatTone(" in old, False)
    ok("the new page imports them too",
       "from \"../components/cameraStatus.js\"" in page, True)

    # ── BOTH ROUTES ─────────────────────────────────────────────────
    app = (SRC / "App.jsx").read_text()
    ok("/admin/cameras is the table",
       'path="/admin/cameras" element={<AdminCameras />}' in app, True)
    ok("/admin/cameras-old is the console",
       'path="/admin/cameras-old"' in app, True)
    ok("  and the table links to it", "/admin/cameras-old" in page, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(sorted(set(_fails)))))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
