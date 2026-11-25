#!/usr/bin/env python3
"""The gates in front of the operator are the gates that will cut.

A DIFFERENT QUESTION FROM "what has this hole stored". That list
contains both "audio_min_ratio" and "audio_min_ratio@1" with nothing
saying which of them cuts the clip in front of you -- the zone that
fired the recording decides. Resolving it by eye is how a cut gets
verified at a ratio the cutter would never run at, which is the thing
the whole test cut exists to prevent.

So the view is resolved by the SAME resolver the cutter uses, and
every gate says where its value came from: set for this tee box, set
for the hole, or the module default nobody has touched. "1.3" alone
does not say whether anybody chose it.

AND THE TWO KINDS DO NOT SWITCH OFF ALIKE. Zero is off for an audio1
gate and below the floor for the detector's own three, which cannot
be absent at all. A panel offering one switch for both would write a
ratio of zero -- a detector with no amplitude threshold.

    python3 tools/audio_gates_view_test.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.admin as A  # noqa: E402
from app.services import debug3 as d3  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


class Row:
    id = 1916
    course_id = 1


class DB:
    def get(self, model, ident):
        return Row()


def view(overrides, zone):
    with patch.object(A, "_gate_tuning_for",
                      lambda db, r: (overrides, "k", "why")), \
         patch.object(A, "_trigger_zone", lambda db, r: zone), \
         patch.object(A, "_view_map_for", lambda db, r: (None, 4, None)):
        return A.get_audio_gates(1916, db=DB())


def by_name(got):
    return {g["name"]: g for g in got["gates"]}


print(__doc__.strip().splitlines()[0])

MIDDLE = {"audio_min_ratio@1": 1.3, "audio_max_attack_ms@1": 14.0,
          "audio_min_crest@1": 4.5, "audio_min_hf_snr_db@1": 12.0,
          "audio_min_amplitude@1": 0.005, "audio1_armed@1": 1.0,
          "audio_min_separation_sec": 3.0}

print("\n1. an untouched hole shows the defaults, and says so")
g = by_name(view({}, 1))
check("ratio", g["audio_min_ratio"]["value"], 4.0)
check("...from the default", g["audio_min_ratio"]["source"], "default")
check("attack", g["audio_max_attack_ms"]["value"], 25.0)
check("every audio1 gate is off", 
      all(g[k]["value"] == 0.0 for k in d3.AUDIO_GATE_FIELDS
          if k not in ("audio_min_ratio", "audio_max_attack_ms")), True)
check("and nothing is armed", view({}, 1)["armed"], False)

print("\n2. a tee box's own gates win, and are labelled as its own")
g = by_name(view(MIDDLE, 1))
check("ratio is the tee box's", g["audio_min_ratio"]["value"], 1.3)
check("...and says where it came from",
      g["audio_min_ratio"]["source"], "tee box")
check("separation came from the hole, not the tee box",
      (g["audio_min_separation_sec"]["value"],
       g["audio_min_separation_sec"]["source"]), (3.0, "hole"))
check("a gate nobody set is still the default",
      g["audio_max_decay_ms"]["source"], "default")
check("armed", view(MIDDLE, 1)["armed"], True)

print("\n3. the SAME stored tuning reads differently for another tee box")
# THE WHOLE POINT. Zone 1 and Zone 2 share a hole and do not share
# gates, and the operator is looking at one recording.
g0 = by_name(view(MIDDLE, 0))
check("zone 0 does not get zone 1's ratio",
      g0["audio_min_ratio"]["value"], 4.0)
check("...nor its crest", g0["audio_min_crest"]["value"], 0.0)
check("but does get the hole's separation",
      g0["audio_min_separation_sec"]["value"], 3.0)
check("and is not armed", view(MIDDLE, 0)["armed"], False)

print("\n4. the two kinds of gate are distinguished")
g = by_name(view(MIDDLE, 1))
for k in ("audio_min_ratio", "audio_max_attack_ms",
          "audio_min_separation_sec"):
    check("  %s is the detector's own" % k, g[k]["detector_gate"], True)
    check("  ...so it offers no off switch", g[k]["off_is"], None)
for k in ("audio_min_crest", "audio_min_hf_snr_db", "audio_min_amplitude"):
    check("  %s is an audio1 gate" % k, g[k]["detector_gate"], False)
    check("  ...and zero is its off", g[k]["off_is"], 0.0)

print("\n5. what the panel writes is what the cutter then reads")
posted = {"audio_min_ratio@1": 1.3, "audio_max_attack_ms@1": 14.0,
          "audio_min_crest@1": 4.5, "audio_min_hf_snr_db@1": 12.0,
          "audio_min_amplitude@1": 0.005, "audio1_armed@1": 1}
live = d3.resolve_gates(posted, zone=1)
check("the ratio the cutter will use", live["audio_min_ratio"], 1.3)
check("the attack", live["audio_max_attack_ms"], 14.0)
check("the crest", live["audio_min_crest"], 4.5)
check("armed", live["audio1_armed"], 1.0)
# A BLANK CLEARS RATHER THAN MEANING ZERO: set_gate_tuning drops the
# nulls, and a gate with nothing stored resolves to its default.
live2 = d3.resolve_gates({k: v for k, v in posted.items()}, zone=1)
check("a gate left blank falls back to the default",
      live2["audio_min_separation_sec"], 2.0)
check("and another tee box is untouched",
      d3.resolve_gates(posted, zone=0)["audio_min_ratio"], 4.0)

print("\n6. no zone recorded means the hole's own gates, said plainly")
got = view({"audio_min_ratio": 2.5}, None)
check("there is no zone name", got["zone_name"], None)
check("the hole's value is in force",
      by_name(got)["audio_min_ratio"]["value"], 2.5)
check("...and is labelled the hole's",
      by_name(got)["audio_min_ratio"]["source"], "hole")

print("\n7. the sentence matches the gates listed beside it")
got = view(MIDDLE, 1)
check("it names the ratio in force", "×1.3" in got["sentence"], True)
check("...the attack", "≤14ms" in got["sentence"], True)
check("...the separation", "3s apart" in got["sentence"], True)
check("...and that the audio1 gates are armed",
      "armed:" in got["sentence"], True)
check("the unarmed case says so instead",
      "NOT armed" in view({**MIDDLE, "audio1_armed@1": 0.0}, 1)["sentence"],
      True)

print("\n8. every gate carries what the editor needs to be safe")
for g in view(MIDDLE, 1)["gates"]:
    check("  %s has a range" % g["name"],
          isinstance(g["lo"], float) and isinstance(g["hi"], float), True)
    check("  ...and a human label", g["label"] != g["name"]
          or g["name"] in d3.AUDIO_GATE_FIELDS, True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
