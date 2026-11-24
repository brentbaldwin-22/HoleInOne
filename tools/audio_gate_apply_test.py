#!/usr/bin/env python3
"""Writing audio gates onto one tee box: what gets stored, and what off means.

WHAT THIS GUARDS is a set of gates that looks armed and is not, or one
that quietly leaves a value behind.

  A GATE SWITCHED OFF MUST BE WRITTEN OFF. Omitting it from the
  payload leaves whatever was stored before in force, so switching a
  gate off on screen and saving would not switch it off -- and the
  screen would then disagree with the detector about what is armed.

  "OFF" IS NOT 0.0 FOR ALL OF THEM. Nine are audio1's own and default
  to zero, which is their off switch. Two are the DETECTOR's existing
  thresholds -- audio_min_ratio floors at 1.5, audio_max_attack_ms at
  2.0 -- and zero is out of range there, not off. A ratio gate of zero
  would be a detector with no amplitude threshold at all, which is not
  a stricter reading of anything; it is a broken one.

  A PRESET MEANS ITS OWN GATES AND NO OTHERS. Applying "back tee" to a
  zone carrying a hand-tuned centroid gate must leave the back tee's
  gates in force, not the back tee's plus a leftover.

    python3 tools/audio_gate_apply_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _audio_gate_off  # noqa: E402
from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print(__doc__.strip().splitlines()[0])

print("\n1. off means zero where zero is a legal value")
for k in ("audio_min_hf_snr_db", "audio_min_crest", "audio_min_amplitude",
          "audio_min_centroid_hz", "audio_min_hf_concentration",
          "audio_min_isolation_db", "audio_max_decay_ms",
          "audio_min_confidence", "audio_max_raw_attack_ms"):
    check("  %s" % k, _audio_gate_off(k), 0.0)
    check("  ...and its range starts there", d3.GATE_SPEC[k][1], 0.0)

print("\n2. and clears to the default where zero is out of range")
for k in ("audio_min_ratio", "audio_max_attack_ms"):
    # The floor is the thing being asserted, not its value: these two
    # are the detector's own thresholds and it cannot run without
    # them, so their range must not reach zero whatever the number is
    # tuned to.
    check("  %s cannot be switched to zero" % k, d3.GATE_SPEC[k][1] > 0.0,
          True)
    check("  ...so off is a clear, not a zero", _audio_gate_off(k), None)

print("\n2b. and the floor leaves room for the tee box that needs least")
# THE BUG THIS CAUGHT. The middle-tee preset asks for a ratio of 1.3
# and the floor was 1.5, so resolve_gates -- which clamps silently --
# stored 1.3, enforced 1.5, and said nothing. The sandbox would score
# the recoveries 1.3 buys and the detector would never make them.
_lowest = min(p["gates"].get("audio_min_ratio", 99)
              for p in d3.AUDIO_TEE_PRESETS.values())
check("the lowest ratio any preset asks for is reachable",
      _lowest >= d3.GATE_SPEC["audio_min_ratio"][1], True)
check("...and it is not clamped on the way through",
      d3.resolve_gates({"audio_min_ratio": _lowest})["audio_min_ratio"],
      _lowest)
check("while a ratio of 1.0 -- no threshold at all -- still is clamped",
      d3.resolve_gates({"audio_min_ratio": 1.0})["audio_min_ratio"]
      > 1.0, True)

print("\n3. every gate in the table has an answer")
for k in d3.AUDIO_GATE_FIELDS:
    check("  %s" % k, _audio_gate_off(k) in (0.0, None), True)

print("\n4. a gate nobody has heard of does not become 0.0")
# It must not read as "off" -- a name the resolver will not accept
# written as a number is a value stored under a key nothing reads.
check("an unknown name clears rather than zeroes",
      _audio_gate_off("audio_min_nonsense"), None)

print("\n5. the presets name only gates that exist")
for name, p in d3.AUDIO_TEE_PRESETS.items():
    for k in p["gates"]:
        check("  %s: %s" % (name, k), k in d3.AUDIO_GATE_FIELDS, True)
        # NOT MERELY IN RANGE: unchanged by the resolver. A value the
        # clamp moves is a preset the operator cannot actually get,
        # and the clamp is silent.
        check("  ...and %.4g survives the resolver" % p["gates"][k],
              d3.resolve_gates({k: p["gates"][k]})[k], p["gates"][k])

print("\n6. the endpoint writes the whole audio set, not a subset")
_src = (ROOT / "backend/app/routers/admin.py").read_text()
_fn = _src[_src.index("def apply_audio_gate_preset("):]
_fn = _fn[:_fn.index("\n@router.post")]
check("explicit values are accepted", "gates_json" in _fn, True)
check("...validated against the gate table",
      "k not in _d3.AUDIO_GATE_FIELDS" in _fn, True)
check("...and a non-number is refused, not coerced",
      "is not a number" in _fn, True)
check("every gate in the table gets written, on or off",
      "for _k in _d3.AUDIO_GATE_FIELDS:" in _fn, True)
check("off goes through the per-gate rule",
      "_audio_gate_off(_k)" in _fn, True)
check("a preset also writes the rest off",
      "_audio_gate_off(k)" in _fn, True)
check("arming still rides with it, separately",
      'gates["audio1_armed%s" % _suffix]' in _fn, True)
check("and arming is NOT in the audio gate table, so it is never "
      "zeroed by the sweep above",
      "audio1_armed" in d3.AUDIO_GATE_FIELDS, False)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
