#!/usr/bin/env python3
"""Every verdict names the knobs that set it, and none of them drift.

"The rise gate is 120" is not a setting anybody typed. It is
`ascent_min_rise_frac` times this camera's frame height -- and until
the page says so, the knob and the number on screen look unrelated.
The ascent legend named ten verdicts and nothing connected any of them
to the thirty tunables behind them.

The descent sweep has published this for a while and the comment there
says why it is published rather than written into the page: "a number
typed into a UI drifts from the constant it describes and then quietly
lies." So the ascent list is emitted the same way, from the values the
run actually used, and this checks the three ways it could still rot:

  * a verdict with no entry -- a gate nobody can tune because nothing
    says what tunes it;
  * an entry naming a knob that is not in GATE_SPEC, so the editor
    cannot show it;
  * a knob whose GATE_VERDICT says it moves gate X while the entry for
    X does not list it.

    python3 tools/gate_explain_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.debug3 import (  # noqa: E402
    ASCENT_WHY_ORDER,
    GATE_SPEC,
    GATE_VERDICT,
)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


_src = (ROOT / "backend" / "app" / "services"
        / "debug3.py").read_text(encoding="utf-8")
# The ascent list, as written. Parsed rather than executed because
# building it for real needs a video.
# BOTH SWEEPS PUBLISH `out["gates"]`, the descent first in the file.
# Slicing from the first one ran straight through it into the ascent
# list and resolved "points" to the DESCENT entry -- the two sweeps
# share verdict names, which is exactly how a legend ends up pointing
# at the wrong knob. So each block is taken on its own.
def _blocks(src):
    """Each `out["gates"] = [...]`, ending at ITS OWN indentation.

    The descent list sits at four spaces and the ascent one at eight,
    so a fixed closing pattern walks straight out of the first block
    and into the second -- which is how this test first reported the
    ascent's `points` gate naming `descent_min_points`.
    """
    out = []
    i = src.find('out["gates"] = [')
    while i != -1:
        bol = src.rfind("\n", 0, i) + 1
        indent = " " * (i - bol)
        end = src.index("\n" + indent + "]", i)
        out.append(src[i:end])
        i = src.find('out["gates"] = [', end)
    return out


def _parse(blk):
    return dict(re.findall(r'\{"key": "(\w+)".*?"knobs": (\[[^\]]*\])',
                           blk, re.S))


_ALL = [_parse(b) for b in _blocks(_src)]
_ascent = next((e for e in _ALL if "rise" in e and "wander" in e), {})
_descent = next((e for e in _ALL if "drop" in e and "sky" in e), {})
_entries = _ascent

print("every verdict the legend can show has an entry:")
for w in ASCENT_WHY_ORDER:
    check("%-9s is explained" % w, w in _entries, True)

print()
print("every knob named is one the editor can actually show:")
_named = set()
for w, raw in _entries.items():
    for k in re.findall(r'"([a-z_]+)"', raw):
        _named.add(k)
        check("%s is in GATE_SPEC" % k, k in GATE_SPEC, True)

print()
print("and every tunable that moves an ascent gate is named by it:")
# GATE_VERDICT is the other direction: knob -> the verdict it moves.
# The two must agree or the legend sends somebody to the wrong knob.
for knob, verdict in GATE_VERDICT.items():
    if not knob.startswith("ascent_"):
        continue
    if verdict not in _entries:
        continue
    check("%s is listed under %s" % (knob, verdict),
          knob in _entries[verdict], True)

print()
print("the two gates with no knob say so rather than inventing one:")
check("outside has an empty knob list", _entries.get("outside"), "[]")

print()
print("and the descent list is a separate list, not this one:")
# Both sweeps use the names points / rate / bend / tilt / sparse. If
# the two ever collapse into one dict the legend sends an operator to
# an ascent knob for a descent refusal, which is unfalsifiable from
# the page.
check("the descent list was found", bool(_descent), True)
# On CONTENT, not on spelling: one list is hand-written with double
# quotes and the other was inserted programmatically with single ones,
# and which quote a literal uses is not what this test is about.
check("...and its points gate names the descent knob",
      "descent_min_points" in (_descent.get("points") or ""), True)
check("...while the ascent one names the ascent knob",
      "ascent_min_points" in (_ascent.get("points") or ""), True)
check("...and neither names the other's",
      "ascent_min_points" in (_descent.get("points") or ""), False)
for knob, verdict in GATE_VERDICT.items():
    if knob.startswith("descent_") and verdict in _descent:
        check("%s is listed under %s" % (knob, verdict),
              knob in _descent[verdict], True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
