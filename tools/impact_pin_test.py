#!/usr/bin/env python3
"""`impact_manual` must mean a person moved the frame — not that a save
happened.

THE BUG, reported from the field as "I'm not sure I ever manually set
the impact frame to 1446":

    wizard_produce did   _mine["impact_manual"] = True
    on EVERY call. Both paths into that endpoint are ordinary saves --
    the wizard's Produce button and the click-to-plot save -- so
    producing once from the wizard pinned whatever frame happened to be
    on screen. `_d3_save_swing` then honours the pin over every later
    measurement, by design and in silence.

    Upload #1780 sat at f1446 for days that way. Each run measured
    f1286 off the ball, reported f1286 in the step panel, and saved
    f1446, because an earlier produce had "manually" set it. Nobody
    had. There was also no way back: nothing could clear the flag.

So the claim about a person now comes from the only place that can see
one -- the screen with the control on it -- as an explicit
`impact_manual` in the request: True pins, False unpins, absent says
nothing. An ordinary produce sends nothing.

    python3 tools/impact_pin_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _d3_save_swing  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class _Row:
    def __init__(self, em):
        self.id = 1
        self.edit_metrics = em


class _DB:
    def __init__(self, row):
        self.row = row

    def get(self, _model, _id):
        return self.row

    def add(self, _r):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def save(prior, rec):
    """Run one produce-save against a swing that already looks like `prior`."""
    row = _Row({"swings": [dict(prior, idx=0)]} if prior else {"swings": []})
    kept: dict = {}
    _d3_save_swing(_DB(row), 1, 0, dict(rec), 0.0, kept_out=kept)
    sw = next((s for s in (row.edit_metrics.get("swings") or [])
               if int(s.get("idx", -1)) == 0), None)
    return sw, kept


print("\n1. a pin outranks a measurement, and says what it overruled")
sw, kept = save({"impact_frame": 1446, "impact_manual": True},
                {"impact_frame": 1286})
check("the pinned frame is what gets saved", sw.get("impact_frame"), 1446)
check("the pin survives the produce", sw.get("impact_manual"), True)
check("...and the measurement is recorded beside it",
      sw.get("impact_measured"), 1286)
check("the run is told what its number was swapped for",
      kept.get("impact_frame", {}).get("measured"), 1286)

print("\n2. agreeing on the number leaves no disagreement to report")
sw, kept = save({"impact_frame": 1286, "impact_manual": True},
                {"impact_frame": 1286})
check("no stale 'measured' left on the record",
      "impact_measured" not in sw, True)
check("nothing reported as kept", kept, {})

print("\n3. an UNPINNED swing takes the measurement")
sw, _ = save({"impact_frame": 1446}, {"impact_frame": 1286})
check("the run's own frame wins", sw.get("impact_frame"), 1286)
check("and no pin appears from nowhere", sw.get("impact_manual"), None)

print("\n4. a stale 'measured' is dropped when the pin goes")
sw, _ = save({"impact_frame": 1446, "impact_measured": 1286},
             {"impact_frame": 1290})
check("the record follows the run once unpinned",
      sw.get("impact_frame"), 1290)

SRC = (ROOT / "backend/app/routers/admin.py").read_text()

print("\n5. the endpoint no longer pins on its own say-so")
_body = SRC[SRC.index('def wizard_produce('):]
_body = _body[:_body.index('\ndef ', 10)]
_live = "\n".join(ln for ln in _body.splitlines()
                  if not ln.lstrip().startswith("#"))
check("it reads the caller's claim", 'payload.get("impact_manual")' in _live)
check("True pins", '_im is True' in _live)
check("False unpins", '_im is False' in _live)
check("there is no unconditional stamp left",
      re.search(r'^\s*_mine\["impact_manual"\] = True\s*$',
                _live, re.M) is None
      or _live.count('_mine["impact_manual"] = True') == 1)
check("...and the one that remains is guarded",
      bool(re.search(r'if _im is True:\s*\n\s*_mine\["impact_manual"\] = True',
                     _live)))

print("\n6. a produce run never claims a person placed the ball either")
_job = SRC[SRC.index('def run_wizard_produce_job('):]
_job = _job[:_job.index('\ndef ', 10)]
_job_live = "\n".join(ln for ln in _job.splitlines()
                      if not ln.lstrip().startswith("#"))
check('no "ball_manual": True written by the job',
      '"ball_manual": True' not in _job_live)

JSX = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()

print("\n7. the screens send an honest signal")
check("the add/edit map sends it only when the frame moved",
      "impact_manual: movedImpact || !!swing.impact_manual," in JSX)
check("...and writes the same rule onto the swing record",
      "impact_manual: movedImpact || !!s.impact_manual," in JSX)
check("the wizard's Produce carries the stored pin, not an assertion",
      "impact_manual: !!draft.impactManual," in JSX)
check("the wizard loads the pin off the record",
      "impactManual: !!s.impact_manual," in JSX)
check("applying a frame by hand is what pins it",
      "setImpactPin(true, navFrame);" in JSX)
check("and there is a way back out", "setImpactPin(false, null);" in JSX)
check("the pin is written into the swing, not the row's top level",
      "{ swings: next }" in JSX[JSX.index("async function setImpactPin"):
                                JSX.index("async function clearSavedAim")])
check("the operator can SEE the pin", '" (pinned by hand)"' in JSX)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
