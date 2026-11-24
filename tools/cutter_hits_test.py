#!/usr/bin/env python3
"""The cutter's hits, and the flattening that used to hide them.

`_cutter_times` returns BARE FLOATS. `_cutter_hits` returns the dicts
the detector actually produced. The flow needs the second: the cuts
column shows what was HEARD -- the ratio and the rise time that got
each strike through this tee box's gates -- and a float carries
neither.

THE BUG. The flow called `_cutter_times` and then read `.get("ratio")`
off each item, which is `'float' object has no attribute 'get'` on the
first strike of every recording. It failed loudly, which is the one
good thing about it; the same mistake one line later, on a key that
simply is not in the dict, would have shown a dash forever.

So this checks BOTH halves: that the hits are dicts, and that the keys
the flow reads off them are keys the detector writes.

    python3 tools/cutter_hits_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print(__doc__.strip().splitlines()[0])

ADMIN = (ROOT / "backend/app/routers/admin.py").read_text()
FLOW = (ROOT / "backend/app/routers/production_flow.py").read_text()
TRACER = (ROOT / "backend/app/services/ai_tracer.py").read_text()

print("\n1. there are two functions, and one is the other's flattening")
check("_cutter_hits exists", "def _cutter_hits(" in ADMIN)
check("_cutter_times exists", "def _cutter_times(" in ADMIN)
check("times is built from hits, not from a second detection",
      "for h in _cutter_hits(" in ADMIN)
# ONE DETECTION PER CALLER. `_cutter_hits` resolves this tee box's
# gates and applies the audio1 ones; the flattening must not do it
# again, or the two would be free to disagree about what was heard.
_hits_src = ADMIN[ADMIN.index("def _cutter_hits("):]
_hits_src = _hits_src[:_hits_src.index("\ndef ", 10)]
_times_src = ADMIN[ADMIN.index("def _cutter_times("):]
_times_src = _times_src[:_times_src.index("\ndef ", 10)]
check("the hits resolve the gates",
      "_ag = _d3.resolve_gates(_ag, zone=_az)" in _hits_src)
check("...and apply this tee box's audio1 gates",
      "_audio1_gate(db, row, src_path, hits, _az, dbg)" in _hits_src)
check("the flattening resolves nothing of its own",
      "resolve_gates" in _times_src, False)
check("...and runs no detector of its own",
      "detect_swings" in _times_src, False)
# NOTE, not an assertion: the auto-produce path at the other end of
# this file resolves the same gates itself rather than calling
# `_cutter_hits`. That is a second copy and it predates this split;
# it is recorded here so the next person to touch either knows.
check("the known second copy is still just the one",
      ADMIN.count("_ag = _d3.resolve_gates(_ag, zone=_az)"), 2)

print("\n2. hits are dicts; times are floats")
_h = ADMIN[ADMIN.index("def _cutter_hits("):]
_h = _h[:_h.index("\ndef ", 10)]
check("hits returns the dicts themselves",
      "return [h for h in hits if isinstance(h, dict)]" in _h)
_t = ADMIN[ADMIN.index("def _cutter_times("):]
_t = _t[:_t.index("\ndef ", 10)]
check("times coerces to float", "out.append(float(t))" in _t)

print("\n3. the flow asks for the hits, not the times")
check("it imports _cutter_hits", "_cutter_hits," in FLOW)
check("...and calls _cutter_times nowhere", "_cutter_times(" in FLOW, False)

print("\n4. every key the flow reads is a key the detector writes")
# What the detector puts on a swing.
# EVERY segments.append IN THE FILE, not the first one. There are
# several and they do not all carry the same keys; slicing to the
# first blank line found a short one and reported `attack_ms` as a key
# the detector never writes -- which it does, three appends later.
written = set()
for _m in re.finditer(r"segments\.append\(", TRACER):
    _blk = TRACER[_m.end():_m.end() + 1400]
    _end = _blk.find("})")
    written |= set(re.findall(r'"([a-z_]+)":',
                              _blk[:_end if _end > 0 else 1400]))
check("the detector's keys were found", len(written) > 5, True)
# What the flow reads off a hit.
read = set(re.findall(r'_of\("([a-z_]+)"\)', FLOW))
read |= set(re.findall(r'\bhit\.get\("([a-z_]+)"\)', FLOW))
check("the flow reads something off them", len(read) > 0, True)
for k in sorted(read):
    check("  %s" % k, k in written, True)

print("\n5. and the peak time is read by a name the detector uses")
_peak = set(re.findall(r'for k in \(([^)]*)\)', FLOW))
check("peak_time_sec is among the names tried",
      any("peak_time_sec" in s for s in _peak), True)
check("...and it is what the detector writes",
      "peak_time_sec" in written, True)

print("\n6. a float hit does not crash the cut loop")
# THE CRASH ITSELF, as a case. A cutter that reports bare times --
# which is exactly what _cutter_times returns, and what this code was
# handed -- must cut the clip and leave the detail blank, not die.
sys.path.insert(0, str(ROOT / "backend"))
from app.routers.production_flow import _hit_peak_sec  # noqa: E402
check("a float is read as its own time", _hit_peak_sec(12.5), 12.5)
check("an int too", _hit_peak_sec(3), 3.0)
check("a dict by its peak", _hit_peak_sec({"peak_time_sec": 7.25}), 7.25)
check("a dict with another spelling", _hit_peak_sec({"peak_sec": 9.0}), 9.0)
check("and nothing readable is None", _hit_peak_sec({"nope": 1}), None)
check("...as is None itself", _hit_peak_sec(None), None)
check("the guard that made this safe is still there",
      '_of = hit.get if isinstance(hit, dict) else' in FLOW)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
