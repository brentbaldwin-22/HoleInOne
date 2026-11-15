#!/usr/bin/env python3
"""Audio gates belong to a hole AND to a tee.

THE OPERATOR, 13 Nov: "i do like the idea of tuning based on the
zones. For example the sound threshold in the back zone(s) should be
a lot higher than the front zones."

There is a physical reason and it is worth writing down. The camera
stands BEHIND the hitting area looking down the hole, so the back tee
is a few yards from its microphone and a forward tee is a hundred.
Loudness is a property of distance: one ratio set for the back tee
deafens the front, and set for the front it fires on everything near
the camera.

The audio gates were global `settings` -- the wrong scope twice over,
since every other gate here is already per camera pair and hole, and
these generalise worse than most.

    python3 tools/zone_gates_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    from app.services.debug3 import (
        GATE_SPEC, GATE_DEFAULTS, resolve_gates, gates_diff)
    from app.config import settings

    # ── THE GATES EXIST, AT THE OLD VALUES ──────────────────────────
    #
    # A hole that tunes nothing must behave exactly as it did when
    # these were global settings, so the defaults have to match.
    for _g, _s in (("audio_min_ratio", "ascent_audio_min_ratio"),
                   ("audio_min_separation_sec",
                    "ascent_audio_min_separation_sec"),
                   ("audio_max_attack_ms", "ascent_audio_max_attack_ms")):
        ok("%s is tunable" % _g, _g in GATE_SPEC, True)
        ok("  and defaults to the old global",
           GATE_DEFAULTS[_g], float(getattr(settings, _s)))

    # ── A ZONE OVERRIDE APPLIES ONLY TO ITS ZONE ────────────────────
    STORE = {"audio_min_ratio": 4.0, "audio_min_ratio@2": 14.0,
             "audio_max_attack_ms@2": 12.0}
    ok("the back tee is turned up hard",
       resolve_gates(STORE, zone=2)["audio_min_ratio"], 14.0)
    ok("  the forward tee is not",
       resolve_gates(STORE, zone=0)["audio_min_ratio"], 4.0)
    ok("  nor the middle",
       resolve_gates(STORE, zone=1)["audio_min_ratio"], 4.0)
    ok("  and a clip with no tee gets the hole's own setting",
       resolve_gates(STORE, zone=None)["audio_min_ratio"], 4.0)
    ok("a second gate can differ for the same zone",
       resolve_gates(STORE, zone=2)["audio_max_attack_ms"], 12.0)
    ok("  while the rest of that zone's gates are the hole's",
       resolve_gates(STORE, zone=2)["audio_min_separation_sec"],
       GATE_DEFAULTS["audio_min_separation_sec"])

    # ── THE OLD SHAPE STILL WORKS, UNTOUCHED ────────────────────────
    #
    # `course.gate_tuning` has held a flat {gate: number} since it was
    # written. A suffix rather than a nested dict is what lets every
    # hole already stored keep working with nothing migrated.
    ok("a plain tuning resolves as it always did",
       resolve_gates({"ascent_min_points": 7})["ascent_min_points"], 7)
    # Compared by equality, reported as a count: printing 35 gates
    # twice buries every other line in this file.
    ok("  and an untuned hole gets every default",
       resolve_gates({}) == dict(GATE_DEFAULTS), True)
    ok("  and so does junk",
       resolve_gates("nonsense") == dict(GATE_DEFAULTS), True)

    # ── ZONE KEYS ARE VALIDATED LIKE ANY OTHER ──────────────────────
    #
    # They are operator input in a JSON column. A zone key must not be
    # able to store what a plain key could not.
    ok("an unknown gate is dropped, zoned or not",
       "nosuch@1" in gates_diff({"nosuch@1": 5}), False)
    ok("  a non-numeric value is dropped",
       resolve_gates({"audio_min_ratio@1": "loud"},
                     zone=1)["audio_min_ratio"],
       GATE_DEFAULTS["audio_min_ratio"])
    ok("  a non-numeric ZONE is dropped",
       resolve_gates({"audio_min_ratio@back": 99.0},
                     zone=1)["audio_min_ratio"],
       GATE_DEFAULTS["audio_min_ratio"])
    ok("  and an out-of-range value is clamped, not refused",
       resolve_gates({"audio_min_ratio@1": 9999.0},
                     zone=1)["audio_min_ratio"], GATE_SPEC["audio_min_ratio"][2])

    # ── AND WHAT GETS STORED KEEPS THE ZONES ────────────────────────
    _d = gates_diff(STORE)
    ok("the diff keeps the zone keys", "audio_min_ratio@2" in _d, True)
    ok("  with the resolved value", _d["audio_min_ratio@2"], 14.0)

    # ── A ZONE KEY MUST BE SAVEABLE, NOT JUST READABLE ──────────────
    #
    # The editor, the diff that decides what is stored and the
    # endpoint that decides what is accepted all have to agree about
    # what a key is. A control the editor can show and the POST
    # rejects is worse than no control, and a key the POST accepts
    # and the resolver ignores is worse still.
    from app.services.debug3 import split_gate_key
    ok("a plain gate key parses", split_gate_key("audio_min_ratio"),
       ("audio_min_ratio", None))
    ok("  a zone key parses", split_gate_key("audio_min_ratio@2"),
       ("audio_min_ratio", 2))
    ok("  an unknown gate does not", split_gate_key("nosuch@2"),
       (None, None))
    ok("  nor an unknown gate plain", split_gate_key("nosuch"),
       (None, None))
    ok("  nor a non-numeric zone", split_gate_key("audio_min_ratio@back"),
       (None, None))
    _adm0 = (ROOT / "backend/app/routers/admin.py").read_text()
    ok("the save endpoint accepts a gate key by that definition",
       "_d3.split_gate_key(k)[0] is None" in _adm0, True)
    ok("  not by membership of GATE_SPEC alone",
       "k not in _d3.GATE_SPEC" in _adm0, False)

    # ── AND THE EDITOR SHOWS THEM ───────────────────────────────────
    #
    # The groups are built by PREFIX, so a gate whose prefix matches
    # none is served correctly and rendered by nobody. That is where
    # the audio gates landed when they were added.
    _jsx = (ROOT / "frontend/src/components/GateTuning.jsx").read_text()
    ok("the editor groups the audio gates",
       'k.startsWith("audio_")' in _jsx, True)
    ok("  with a column per tee", "Zone {z.screen}" in _jsx, True)
    ok("  writing gate@N", "`${k}@${zi}`" in _jsx, True)
    ok("  and nothing can fall through every group",
       "const other = rows.filter(" in _jsx, True)
    ok("the zones come from the backend, not the editor's own idea",
       '"zones": _gate_tuning_zones(db, row)' in _adm0, True)

    # ── THE CONSUMER ASKS FOR THE CLIP'S OWN TEE ────────────────────
    _adm = (ROOT / "backend/app/routers/admin.py").read_text()
    ok("the audio pass resolves per zone",
       "_d3.resolve_gates(_ag, zone=_az)" in _adm, True)
    ok("  reading the tee off the dwell's own record",
       "def _trigger_zone(db, row)" in _adm, True)
    ok("  and no longer uses the global settings",
       "settings.ascent_audio_min_ratio" in _adm, False)
    ok("  saying which gates it used",
       'dbg["gates_from"]' in _adm, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
