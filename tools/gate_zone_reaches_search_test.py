#!/usr/bin/env python3
"""A per-zone gate the editor offers must reach the search that uses it.

THE BUG THIS PREVENTS is a gate that stores, displays as tuned, and
does nothing. The editor offers the launch cone and the audio gates
PER ZONE -- a tee aims somewhere, and loudness is distance -- and the
three search functions resolved their overrides with no zone at all:

    _G = resolve_gates(gates)

`resolve_gates` files a per-zone override under "name@N" and only
looks for it when it is told which N. So a cone set for zone 3 was
written to the database, shown as tuned in the editor, and arrived at
the sweep as -90..+90, which is no cone whatsoever. Nothing anywhere
said so.

This is the third time this exact shape has appeared -- the ball gates
had it ("I changed the lag and nothing happened"), the audio gates had
it before that. So the test is on the resolve AND on the search, not
just on the helper, because the helper was never the broken half.

    python3 tools/gate_zone_reaches_search_test.py
"""
import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("resolve_gates files per-zone overrides under name@N:")
OV = {"ascent_cone_from_deg@3": 45.0, "ascent_cone_to_deg@3": 77.0,
      "ascent_cone_below_skyline@3": 1}
check("without a zone it cannot see them",
      d3.resolve_gates(OV)["ascent_cone_from_deg"], -90.0)
check("with the zone it can",
      d3.resolve_gates(OV, zone=3)["ascent_cone_from_deg"], 45.0)

print()
print("every search that resolves gates takes a zone:")
for fn in ("find_descents", "find_ascents", "sweep_ascents"):
    check("%s has a zone parameter" % fn,
          "zone" in inspect.signature(getattr(d3, fn)).parameters)

src = ROOT / "backend" / "app" / "services" / "debug3.py"
body = src.read_text()
check("no search resolves gates blind to the zone",
      "resolve_gates(gates)" not in body)

print()
print("and the zone actually reaches the sweep's own numbers:")


def _clip(path, n=30):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (640, 480))
    for _ in range(n):
        vw.write(np.full((480, 640, 3), 30, dtype=np.uint8))
    vw.release()


def _cone(zone):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.mp4"
        _clip(p)
        out = d3.sweep_ascents(p, 60.0, roi={"y": 0.5},
                               gates=OV, zone=zone) or {}
        return out.get("cone_deg"), out.get("cone_on")


# THE REPORTED numbers, because the picture is drawn from these -- a
# cone that resolves correctly and reports the default would draw the
# wrong wedge over a correct search.
deg, on = _cone(3)
check("zone 3 runs the tee's own cone", deg, [45.0, 77.0])
check("  and reports it as a cone", on, True)
deg, on = _cone(None)
check("no zone falls back to the hole's values", deg, [-90.0, 90.0])
check("  which is no cone", on, False)

print()
print("the callers pass it:")
adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
# Each ascent search in the admin router must hand over a zone; a
# search that resolves the hole's overrides without one is the bug.
for needle in ("_d3.sweep_ascents(", "_d3.find_ascents("):
    i = 0
    n_calls = n_zoned = 0
    while True:
        i = adm.find(needle, i)
        if i < 0:
            break
        call = adm[i:adm.find(") or {}", i) + 7]
        n_calls += 1
        if "zone=" in call:
            n_zoned += 1
        i += len(needle)
    check("every %s call passes a zone (%d/%d)"
          % (needle.strip("_d3.("), n_zoned, n_calls),
          n_calls > 0 and n_zoned == n_calls)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — a per-zone gate reaches the search that uses it")
