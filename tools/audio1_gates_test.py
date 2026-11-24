#!/usr/bin/env python3
"""Per-tee audio gates: off by default, armed as data, never fatal.

THESE GATES DECIDE WHETHER A SHOT IS PRODUCED, which makes them
different in kind from everything else audio1 does. Four properties
matter more than the thresholds themselves:

  NOTHING HAPPENS UNTIL SOMEBODY SETS ONE. Every audio1 gate defaults
  to zero, which is off. A hole nobody has touched behaves exactly as
  it did before these existed, and the measurement pass does not even
  run.

  ARMING IS DATA, PER TEE BOX. `audio1_armed@N` is a stored value like
  any other gate, so a box is tuned on real traffic while still
  reporting, then switched on — and off again — without a deploy.

  AN UNMEASURABLE VALUE NEVER REJECTS. A quiet strike from a far tee
  is exactly where HF SNR comes back blank; a gate that rejected what
  it could not measure would select against distance, silently, and
  worst on the shots hardest to catch.

  A FAILURE KEEPS EVERYTHING. If the audio cannot be decoded, every
  peak survives. A diagnostic that deletes a customer's shot because
  ffmpeg hiccuped is not worth having.

    python3 tools/audio1_gates_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    _audio1_enabled_gates, _audio1_value,
)
from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print("\n1. every new gate is OFF out of the box")
for k in d3.AUDIO_GATE_FIELDS:
    if k in ("audio_min_ratio", "audio_max_attack_ms"):
        continue           # the detector's own, defaulted long ago
    check("  %s defaults to off" % k, d3.GATE_DEFAULTS[k], 0.0)
check("and so does arming", d3.GATE_DEFAULTS["audio1_armed"], 0.0)
check("so an untouched hole switches nothing on",
      _audio1_enabled_gates(d3.resolve_gates({})), {})

print("\n2. the two tee presets, as given")
mid = d3.AUDIO_TEE_PRESETS["middle"]["gates"]
back = d3.AUDIO_TEE_PRESETS["back"]["gates"]
check("middle: HF SNR 12, crest 4.5, attack 14, ratio 1.3, amp 0.005",
      [mid["audio_min_hf_snr_db"], mid["audio_min_crest"],
       mid["audio_max_attack_ms"], mid["audio_min_ratio"],
       mid["audio_min_amplitude"]], [12.0, 4.5, 14.0, 1.3, 0.005])
check("back:   HF SNR 20, crest 7.0, attack 14, ratio 3.5, amp 0.050",
      [back["audio_min_hf_snr_db"], back["audio_min_crest"],
       back["audio_max_attack_ms"], back["audio_min_ratio"],
       back["audio_min_amplitude"]], [20.0, 7.0, 14.0, 3.5, 0.050])
# WHAT DOES NOT MOVE IS THE INTERESTING PART: rise time is about how
# fast a struck ball leaves, not how far away it is.
check("the attack gate is the SAME on both — it is not a loudness gate",
      mid["audio_max_attack_ms"], back["audio_max_attack_ms"])
for k in ("audio_min_hf_snr_db", "audio_min_crest", "audio_min_ratio",
          "audio_min_amplitude"):
    check("  %s is tighter on the back tee" % k, back[k] > mid[k], True)

print("\n3. a preset lands on ONE zone and leaves the others alone")
stored = {("%s@2" % k): v for k, v in back.items()}
stored["audio1_armed@2"] = 1.0
z2 = d3.resolve_gates(stored, zone=2)
z0 = d3.resolve_gates(stored, zone=0)
check("zone 2 takes the back-tee bars", z2["audio_min_hf_snr_db"], 20.0)
check("...and is armed", z2["audio1_armed"], 1.0)
check("zone 0 is untouched", z0["audio_min_hf_snr_db"], 0.0)
check("...and unarmed", z0["audio1_armed"], 0.0)
check("so zone 0 still switches nothing on",
      _audio1_enabled_gates(z0), {})

print("\n4. the detector's own two are not applied twice")
# `audio_min_ratio` and `audio_max_attack_ms` are arguments to
# detect_swings_from_audio and are already applied inside it, per
# zone, by both produce paths. Re-applying them as a post-filter
# would be a second opinion on a decision already taken.
en = _audio1_enabled_gates(z2)
check("the post-filter does not re-check the peak ratio",
      "audio_min_ratio" in en, False)
check("...nor the detector attack", "audio_max_attack_ms" in en, False)
check("but it does check HF SNR", "audio_min_hf_snr_db" in en, True)
check("...crest", "audio_min_crest" in en, True)
check("...and amplitude", "audio_min_amplitude" in en, True)

print("\n5. each gate reads the measurement it names")
M = {"hf_snr_db": 18.2, "crest_factor": 6.1, "peak_amplitude": 0.031,
     "concentration_hf": {"e20_over_e200": 0.44},
     "confidence": {"score": 71.0}, "decay_ms": None}
check("HF SNR", _audio1_value(M, "hf_snr_db"), 18.2)
check("crest", _audio1_value(M, "crest_factor"), 6.1)
check("amplitude", _audio1_value(M, "peak_amplitude"), 0.031)
check("HF concentration comes out of the nested dict",
      _audio1_value(M, "hf_concentration"), 0.44)
check("...and the confidence score out of its own",
      _audio1_value(M, "confidence_score"), 71.0)
check("AN UNMEASURED VALUE IS None, so no gate can reject on it",
      _audio1_value(M, "decay_ms"), None)

print("\n6. the code says so where it counts")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_g = SRC[SRC.index("def _audio1_gate("):]
_g = _g[:_g.index("\ndef ", 10)]
check("a blank value is skipped, not failed",
      "if v is None:\n                    blanks.append(label)" in _g, True)
check("unarmed returns every hit", 'if not armed:' in _g
      and "return hits, report" in _g, True)
check("a crash keeps every hit",
      "keeping all peaks" in _g and "return hits, report" in _g, True)
check("and every rejection is recorded with reading and bar",
      '"read": round(float(v), 3), "bar": bar' in _g, True)
check("both produce paths run it",
      SRC.count("_audio1_gate(db, row, src_path, hits, _az, dbg)"), 2)
check("arming is a stored gate, not a constant",
      '"audio1_armed": (0.0, 0.0, 1.0, True)'
      in (ROOT / "backend/app/services/debug3.py").read_text(), True)
check("...and a preset can be applied without arming",
      'gates["audio1_armed%s" % _suffix] = 1.0 if arm else 0.0' in SRC,
      True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
