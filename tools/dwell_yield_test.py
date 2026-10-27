#!/usr/bin/env python3
"""Which tees may stand their dwell down for the audio trigger.

THE BUG THIS PREVENTS is a rig that records nothing while looking
perfectly healthy. One camera covers tees at different ranges; a strike
from the back tees measures x19-x30 after the high-pass and the same
strike from the forward tees measures x1.02, which is the noise floor.
So `replaces_dwell: true` suppresses the dwell on a tee whose only
trigger was the dwell, and the journal stays empty because nothing
failed.

Everything here therefore checks the SAFE DIRECTION as hard as the
working one: every unknown must fire the dwell, not yield to it.

    python3 tools/dwell_yield_test.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent import tee  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-14s %s" % (label, repr(got), "ok" if ok else
                                "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


# ---- parsing -----------------------------------------------------------
print("replaces_dwell parses to None / True / a set of indices:")
for label, value, want in (
    ("absent (False) -> nowhere", False, None),
    ("true -> anywhere", True, True),
    ("[1, 2] -> those zones", [1, 2], {1, 2}),
    ("[0] -> zone 0, not falsy-collapsed", [0], {0}),
    ("strings are indices too", ["1", "2"], {1, 2}),
    ("EMPTY LIST IS NOWHERE, not anywhere", [], None),
    ("junk entries dropped, rest kept", [1, None, "x"], {1}),
    ("all-junk list -> nowhere", [None, "x"], None),
    ("a bare string is not a zone list", "true", None),
    ("None -> nowhere", None, None),
    ("a number is not a zone list", 1, None),
):
    check(label, tee._dwell_yield_zones(value), want)


# ---- which zone yields -------------------------------------------------
class FakeTrigger:
    def __init__(self, confirm_zones=None):
        self.confirm_zones = confirm_zones


def runner(yields, trigger=None):
    r = object.__new__(tee.TeeAgent)
    r._dwell_yields = yields
    r._audio_trigger = trigger
    r.roi = ROI
    return r


# Two zones side by side: 0 is the forward tee (out of earshot), 1 the
# back tee. Points are feet, as _in_roi takes them.
ROI = {"boxes": [{"x": 0, "y": 0, "w": 10, "h": 10},
                 {"x": 100, "y": 0, "w": 10, "h": 10}],
       "frame": {"w": 1920, "h": 1080}}
FORWARD, BACK, OUTSIDE = (5, 5), (105, 5), (500, 500)

print()
print("only a named zone stands down, and it says which:")
check("back tee is named -> yields",
      runner({1}).  _yield_zone_for(BACK, ROI), "zone 1")
check("FORWARD TEE IS NOT NAMED -> dwell fires",
      runner({1}).  _yield_zone_for(FORWARD, ROI), None)
check("true yields in any zone, and still names it",
      runner(True). _yield_zone_for(FORWARD, ROI), "zone 0")
check("zone 0 named -> yields, index 0 is a real zone",
      runner({0}).  _yield_zone_for(FORWARD, ROI), "zone 0")
check("a stance in no zone -> dwell fires",
      runner({1}).  _yield_zone_for(OUTSIDE, ROI), None)
check("...even with true: any ZONE is not anywhere",
      runner(True). _yield_zone_for(OUTSIDE, ROI), None)
check("an unreadable stance -> dwell fires",
      runner({1}).  _yield_zone_for(None, ROI), None)
check("roi gone missing -> dwell fires",
      runner({1}).  _yield_zone_for(BACK, None), None)
check("garbage zones -> dwell fires",
      runner({1}).  _yield_zone_for(BACK, "not an roi"), None)


class Exploding:
    """A zone set whose membership test throws, standing in for
    anything unforeseen inside the branch."""

    def __contains__(self, item):
        raise RuntimeError("boom")


check("a throwing membership test -> dwell fires",
      runner(Exploding())._yield_zone_for(BACK, ROI), None)

# The seam the stop-policy suite patches must still be the one that
# decides whether anybody is on a tee at all.
check("_in_roi is still what says 'on a tee'",
      tee._in_roi(BACK, ROI) and not tee._in_roi(OUTSIDE, ROI), True)


# ---- the orphaned-zone warning -----------------------------------------
class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def warned(yields, confirm_zones):
    cap = Capture()
    cap.setLevel(logging.WARNING)
    tee.log.addHandler(cap)
    try:
        runner(yields, FakeTrigger(confirm_zones)) \
            ._check_dwell_yield_zones()
    finally:
        tee.log.removeHandler(cap)
    return any("replaces_dwell" in m for m in cap.lines)


print()
print("a zone that yields to ears barred from it must be shouted about:")
check("yields zone 2, confirms only 0 and 1 -> WARN",
      warned({2}, [0, 1]), True)
check("yields zone 2, confirms 1 and 2 -> quiet",
      warned({2}, [1, 2]), False)
check("yields 1 and 2, confirms only 1 -> WARN (partial is still a hole)",
      warned({1, 2}, [1]), True)
check("confirm_zones unset means anywhere -> quiet",
      warned({2}, None), False)
check("true with confirm_zones restricted -> WARN",
      warned(True, [0]), True)
check("true with confirm_zones unset -> quiet",
      warned(True, None), False)
check("nothing yields -> quiet",
      warned(None, [0]), False)
def _no_trigger_is_quiet():
    cap = Capture()
    cap.setLevel(logging.WARNING)
    tee.log.addHandler(cap)
    try:
        runner({1}, None)._check_dwell_yield_zones()
    finally:
        tee.log.removeHandler(cap)
    return not cap.lines


check("no audio trigger at all -> quiet", _no_trigger_is_quiet(), True)

def mapping(yields):
    cap = Capture()
    cap.setLevel(logging.INFO)
    lvl = tee.log.level
    tee.log.setLevel(logging.INFO)
    tee.log.addHandler(cap)
    try:
        runner(yields, FakeTrigger(None))._check_dwell_yield_zones()
    finally:
        tee.log.removeHandler(cap)
        tee.log.setLevel(lvl)
    return cap.lines


print()
print("the zero-based index must be stated against the screen's number:")
_m = mapping({1})
check("one line per zone", len(_m), 2)
check("index 0 is \"Zone 1\" on screen and fires",
      "zone 0 (\"Zone 1\"" in _m[0] and "fires" in _m[0], True)
check("index 1 is \"Zone 2\" on screen and stands down",
      "zone 1 (\"Zone 2\"" in _m[1] and "STANDS DOWN" in _m[1], True)
_n = mapping(None)
check("the map is printed BEFORE anything is configured", len(_n), 2)
check("...with every zone firing", all("fires" in m for m in _n), True)
check("true stands every zone down",
      all("STANDS DOWN" in m for m in mapping(True)), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
