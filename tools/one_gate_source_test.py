#!/usr/bin/env python3
"""One place sets the audio gates, and it is the one production reads.

THE SPLIT THIS CLOSES. The Test Cut page carried its own ratio,
separation and attack boxes and passed them straight to the detector.
Production read the hole's STORED gates for its tee box and applied
the audio1 gates on top. So the two cut differently by construction:
a cut could be verified on the page at a ratio the cutter would never
run at, and the audio1 gates -- the ones the sandbox tunes and the
presets write -- never touched a test cut at all.

That makes a test cut evidence about nothing. It is supposed to be
the rehearsal.

Both now go through `_cutter_hits`: one gate resolution, one detector
call, one application of this tee box's audio1 gates.

    python3 tools/one_gate_source_test.py
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
PAGE = (ROOT / "frontend/src/pages/AdminLongUpload.jsx").read_text()

# The audio branch of the test cut.
_i = ADMIN.index('if detector in ("audio", "audio2", "audio1"):')
# To the end of the audio branch, not a fixed slice: the fallback
# sits at the bottom of it and a short window reported it missing.
BRANCH = ADMIN[_i:ADMIN.index('elif detector == "ballgone"', _i)]

print("\n1. the test cut resolves the stored gates")
check("it calls the one resolver",
      '_cutter_hits("audio", db, row, src_path, tee_fps' in BRANCH)
check("...which applies this tee box's audio1 gates",
      "_audio1_gate(db, row, src_path, hits, _az, dbg)" in ADMIN)

print("\n2. the form values no longer cut, except as an announced fallback")
_live = BRANCH[:BRANCH.index("except Exception as _gx")]
check("the happy path does not read the form ratio",
      "audio_min_peak_ratio" in _live, False)
check("...nor the form separation", "audio_min_separation_sec" in _live, False)
check("...nor the form attack", "audio_max_attack_ms" in _live, False)
# A fallback is allowed -- a diagnostic page must not 500 on an upload
# with no course to store gates against -- but it must SAY it fell
# back, or the two-sources problem is rebuilt by accident.
_fb = BRANCH[BRANCH.index("except Exception as _gx"):]
check("the fallback exists", "detect_swings_from_audio(" in _fb)
check("...and uses the form values there", "audio_min_peak_ratio" in _fb)
check("...and says so rather than falling back in silence",
      "because this upload has no" in _fb)

print("\n3. the run reports which gates it actually used")
check("through the same builder the production flow prints",
      "from .production_flow import _gates_sentence as _gs" in BRANCH)
check("...and the page renders it",
      "debug?.audio_gates_from" in PAGE)

print("\n4. the duplicate boxes are gone from the page")
# The three inputs shown for the AUDIO detectors are what made a
# second source possible. The state survives for the form post, and
# the `combined` detector keeps its own audio-ratio box -- that is a
# different detector down a different branch of the test cut, and it
# was not part of this change.
_audio_controls = re.findall(
    r'\["audio", "audio2", "audio1"\]\.includes\(\s*\n?\s*'
    r'testCutDetector\[u\.id\] \|\| "motion"\)[\s\S]{0,900}?\n\s{22}\)\}',
    PAGE)
check("the audio detectors' control block was found",
      len(_audio_controls) > 0, True)
_audio_block = "\n".join(_audio_controls)
for name in ("audioMinRatio", "audioMinSep", "audioMaxAttack"):
    check("  no %s box on the audio detectors" % name,
          "value={%s[u.id]" % name in _audio_block, False)
check("the combined detector keeps its own, deliberately",
      'value={audioMinRatio[u.id] ?? 5}' in PAGE, True)
check("and the page says where they went",
      "gates: stored, per tee box" in PAGE)

print("\n5. the detector still gets real numbers, from the gate table")
from app.services import debug3 as d3  # noqa: E402

_g = d3.resolve_gates({}, zone=1)
for _k in ("audio_min_ratio", "audio_min_separation_sec",
           "audio_max_attack_ms"):
    check("  %s resolves" % _k, isinstance(_g.get(_k), float), True)
check("ratio and attack are gates the sandbox can set",
      all(k in d3.AUDIO_GATE_FIELDS
          for k in ("audio_min_ratio", "audio_max_attack_ms")), True)
# SEPARATION IS NOT IN THE SANDBOX, and that is worth knowing: it is
# resolved from the hole's tuning like the others, but no preset sets
# it and the sandbox offers no control for it. The run prints it so
# the operator can at least see the value that is being used.
check("separation is NOT one of them, so it is only ever the hole's",
      "audio_min_separation_sec" in d3.AUDIO_GATE_FIELDS, False)
check("...and the sentence prints it anyway",
      "s apart" in (ROOT / "backend/app/routers/production_flow.py")
      .read_text())

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
