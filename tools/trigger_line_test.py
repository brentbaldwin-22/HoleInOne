#!/usr/bin/env python3
"""The page the operator opens prints what the rig sent.

The rig has described its dwells for some time: which zone, how long
somebody stood there, the threshold it beat, the blinks forgiven, and
what the microphone was doing at the time. `trigger_detail_test`
checks the rig builds that and the server stores it.

Nothing checked that a SCREEN read it. The current cameras page --
/admin/cameras, the one that is routed -- printed
`trigger_detail.kind` and the zone, so a fully described dwell
arrived and was rendered as the single word "dwell". The renderer that
shows the rest existed the whole time, on the page that is no longer
routed.

So: one renderer, imported by both, and the fields asserted by name.
Checked against the built bundle as well as the source, because an
export that does not survive the build is a blank column.

    python3 tools/trigger_line_test.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend"
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


COMP = FE / "src" / "components" / "TriggerLine.jsx"
LIVE = FE / "src" / "pages" / "AdminCameras.jsx"
OLD = FE / "src" / "pages" / "AdminCamerasOld.jsx"

print("\n-- there is ONE renderer and both pages use it")
check("the component exists", COMP.exists(), True)
src = COMP.read_text(encoding="utf-8")
check("  and exports TriggerLine", "export function TriggerLine" in src, True)
live = LIVE.read_text(encoding="utf-8")
old = OLD.read_text(encoding="utf-8")
check("the ROUTED cameras page renders it", "<TriggerLine" in live, True)
check("  and imports it from the component",
      "components/TriggerLine.jsx" in live, True)
check("the old page renders it too", "<TriggerLine" in old, True)
check("  and no longer has its own copy",
      "function TriggerLine(" in old, False)
# THE HELPERS WENT WITH IT. Left behind they are dead code that still
# compiles, and the next person to need one copies it back.
for _h in ("function clockOf", "function zoneOf", "function judgeOf"):
    check("  nor its own %s" % _h.split()[-1], _h in old, False)

print("\n-- and the routed page no longer prints only the kind")
check("the bare `kind` column is gone",
      "{e.trigger_detail?.kind ||" in live, False)

print("\n-- every dwell field the rig sends is named by the renderer")
# These are the keys `_dwell_detail` builds on the Pi (see
# pi-agent/agent/tee.py). A key the rig sends and no screen mentions
# is the bug this file exists for.
for _k in ("stood_sec", "dwell_sec", "restarts", "restarts_total",
           "zone_label", "capture_sec"):
    check("  %s" % _k, _k in src, True)
print("  -- and the ears, which is why the dwell carries them at all")
for _k in ("healthy", "shadow", "heard", "cuts"):
    check("  audio.%s" % _k, _k in src, True)

print("\n-- a detail it cannot read says so, rather than rendering blank")
check("an absent detail is explained",
      "not recorded" in src, True)
check("  and an unknown kind still shows its contents",
      "JSON.stringify(detail)" in src, True)

print("\n-- and it survives the build")
# AN EXPORT THAT DOES NOT SURVIVE THE BUILD IS A BLANK COLUMN, and
# neither the source checks above nor a passing build would say so.
_dist = FE / "dist"
_r = subprocess.run(["npx", "vite", "build", "--logLevel", "error"],
                    cwd=FE, capture_output=True, text=True, timeout=900)
check("the bundle builds", _r.returncode, 0)
if _r.returncode != 0:
    print(_r.stderr[-1500:])
else:
    _js = sorted((_dist / "assets").glob("*.js"),
                 key=lambda p: p.stat().st_size, reverse=True)
    check("  there is a bundle to look at", bool(_js), True)
    if _js:
        _blob = _js[0].read_text(encoding="utf-8", errors="replace")
        # The prose is the fingerprint: minification keeps strings.
        check("  the dwell sentence is in it",
              "stood in" in _blob, True)
        check("  the threshold line is in it",
              "threshold " in _blob, True)
        check("  and the ears line is in it",
              "ears NOT hearing the course" in _blob, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
