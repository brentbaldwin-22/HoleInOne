#!/usr/bin/env python3
"""The disk, where somebody will see it — and nothing deleted by accident.

THREE OUTAGES, NONE OF THEM VISIBLE. This deployment ran out of disk on
24, 27 and 29 October, and every time the first anybody knew was an
unrelated 500: ffmpeg refusing to write, a skyline modal that could not
save a frame. The numbers were always there behind
GET /api/admin/storage; there was simply no page that showed them, so
the only way to look was curl with an admin header.

So the panel is on the DASHBOARD, not behind a link: a full disk is not
something you go looking for.

AND EVERY SWEEP IS A DRY RUN FIRST. Nothing any of them deletes can be
recovered — there is no object-storage mirror behind this deployment —
so a sweep has to be asked twice: once to show what it would take, and
again to take it. The client defaults match the server's, so a call
that forgets the flag cannot delete anything.

    python3 tools/storage_panel_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


_api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
_page = (ROOT / "frontend" / "src" / "pages"
         / "Admin.jsx").read_text(encoding="utf-8")
_admin = (ROOT / "backend" / "app" / "routers"
          / "admin.py").read_text(encoding="utf-8")

print("a call that forgets the flag deletes nothing:")
for fn in ("sweepImages", "sweepHeadroom", "sweepRetention"):
    check("%s defaults to a dry run" % fn,
          bool(re.search(fn + r": \(key, \{ dryRun = true", _api)), True)

print()
print("...and neither does the server:")
for route, arg in (("storage/images", "run_image_sweep"),
                   ("storage/headroom", "run_headroom_sweep"),
                   ("storage/retention", "run_clip_retention")):
    _i = _admin.index('def %s(' % arg)
    _sig = _admin[_i:_admin.index(")", _i)]
    check("%s defaults to a dry run" % route, "dry_run: bool = True" in _sig,
          True)

print()
print("the panel is where it will be seen:")
check("it is mounted on the dashboard",
      "<StorageCard adminPassword={adminPassword}" in _page, True)
check("...above the revenue card, not buried",
      _page.index("<StorageCard") < _page.index("<RevenueCard"), True)
check("...and it reads the real usage route",
      "await api.storage(adminPassword)" in _page, True)

print()
print("it asks twice:")
# preview() runs the sweep dry and parks the result; only commit()
# passes false, and only from a button inside that result.
check("preview runs it dry", "SWEEPS[kind].run(true)" in _page, True)
check("commit runs it for real", "SWEEPS[kind].run(false)" in _page, True)
check("...and commit is reachable only from a shown plan",
      bool(re.search(r"\{plan && \(.*?onClick=\{\(\) => commit\(plan\.kind\)\}",
                     _page, re.S)), True)
check("the footage sweep is marked as the dangerous one",
      bool(re.search(r'label: "Free space \(deletes footage\)".*?'
                     r'tone: "err-text"', _page, re.S)), True)
check("...and the image sweep is not",
      bool(re.search(r'label: "Clear cached images".*?tone: ""',
                     _page, re.S)), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
