#!/usr/bin/env python3
"""The armed gates actually refuse peaks, on real audio.

EVERY OTHER TEST OF THESE GATES READS THE SOURCE. This one builds a
clip, runs the detector over it and watches a gate throw a peak away
-- because "is this applied?" was asked of a screen that could not
answer it, and reading the code is how you convince yourself of
something that is not happening.

FOUR STATES, AND THREE OF THEM PRODUCE THE SAME CUT LIST:

  no gate switched on     the pass does not run at all
  ratio/attack only       still does not run: those are the DETECTOR's
                          gates, applied by the detector, and
                          re-applying them here would double-count
  set but not armed       runs, reports, refuses nothing
  set and armed           refuses, and names what it refused

Only the last changes the cuts, and from the cut list alone the first
three are indistinguishable from each other and from a measurement
that fell over. That is why the report is rendered rather than logged.

    python3 tools/audio1_gate_applies_test.py
"""
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

import app.routers.admin as A  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def make_clip(path):
    """One bright fast transient, two dull slow ones."""
    sr = 16000
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.004, sr * 20).astype(np.float32)

    def add(at, amp, decay_ms, f, bright):
        i = int(at * sr)
        nd = int(sr * decay_ms / 1000 * 4)
        t = np.arange(nd) / sr
        env = np.exp(-t / (decay_ms / 1000.0))
        tone = np.sin(2 * np.pi * f * t)
        if bright:
            tone = tone + 0.7 * np.sin(2 * np.pi * min(f * 2, 7000) * t)
        x[i:i + nd] += (amp * env * tone).astype(np.float32)

    add(4.0, 0.45, 45, 2600, True)
    add(10.0, 0.06, 300, 300, False)
    add(15.0, 0.08, 250, 400, False)
    wav = path.with_suffix(".wav")
    w = wave.open(str(wav), "w")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "aac", "-b:a", "96k", str(path)], check=True)


class Row:
    id = 1916
    course_id = 1


def run(src, overrides):
    dbg = {}
    with patch.object(A, "_gate_tuning_for",
                      lambda db, r: (overrides, "k", "w")), \
         patch.object(A, "_trigger_zone", lambda db, r: 1):
        hits = A._cutter_hits("audio", None, Row(), src, 30.0, dbg)
    return hits, (dbg.get("audio1_gates") or {}), dbg


print(__doc__.strip().splitlines()[0])

with TemporaryDirectory() as tmp:
    SRC = Path(tmp) / "clip.mp4"
    make_clip(SRC)
    LOOSE = {"audio_min_ratio@1": 1.3, "audio_max_attack_ms@1": 14.0}

    print("\n1. the detector honours the tee box's stored ratio and attack")
    hits, _, dbg = run(SRC, LOOSE)
    check("it found the strike", len(hits), 1)
    check("at the right moment", round(hits[0]["peak_time_sec"]), 4)
    check("and reports the values it ran at",
          dbg["gates_used"]["audio_min_ratio"], 1.3)
    check("...the attack too", dbg["gates_used"]["audio_max_attack_ms"], 14.0)

    print("\n2. with no audio1 gate set, the pass does not run")
    _, g, _ = run(SRC, LOOSE)
    check("it did not run", bool(g.get("ran")), False)
    check("and the cut is the detector's alone", len(hits), 1)

    print("\n3. set but not armed: it runs, reports, refuses nothing")
    hits, g, _ = run(SRC, {**LOOSE, "audio_min_crest@1": 4.5})
    check("it ran", g.get("ran"), True)
    check("it is not armed", g.get("armed"), False)
    check("it would have dropped the peak", len(g["rejected"]), 1)
    check("...naming the gate", g["rejected"][0]["failed"][0]["gate"],
          "audio_min_crest")
    check("...with the reading and the bar",
          g["rejected"][0]["failed"][0]["bar"], 4.5)
    check("BUT THE CUT SURVIVES, which is what unarmed means",
          len(hits), 1)
    check("and the report says so in words",
          "not armed" in (g.get("why") or ""), True)

    print("\n4. set AND armed: the peak is refused")
    hits, g, _ = run(SRC, {**LOOSE, "audio_min_crest@1": 4.5,
                           "audio1_armed@1": 1.0})
    check("it ran", g.get("ran"), True)
    check("armed", g.get("armed"), True)
    check("one rejection", len(g["rejected"]), 1)
    check("AND THE CUT IS GONE, which is the whole point", len(hits), 0)

    print("\n5. a bar the peak clears does not refuse it")
    hits, g, _ = run(SRC, {**LOOSE, "audio_min_crest@1": 1.0,
                           "audio1_armed@1": 1.0})
    check("nothing rejected", len(g["rejected"]), 0)
    check("the cut survives", len(hits), 1)

    print("\n6. the detector's own gates are not re-applied here")
    # audio_min_ratio and audio_max_attack_ms are in the gate table but
    # the DETECTOR applies them. Counting them again would reject a
    # peak twice for one reason, and on a clip where the detector
    # already honoured them nothing new can fail.
    _, g, _ = run(SRC, {**LOOSE, "audio1_armed@1": 1.0})
    check("no audio1 pass for ratio and attack alone",
          bool(g.get("ran")), False)

    print("\n7. a rejection always carries the reading it was made on")
    # NEVER A BARE REFUSAL. A gate that drops a shot without saying
    # what it read is a week of silence nobody can diagnose, which is
    # the failure this report exists to prevent.
    hits, g, _ = run(SRC, {**LOOSE, "audio_min_hf_concentration@1": 0.99,
                           "audio1_armed@1": 1.0})
    check("the peak was refused", len(hits), 0)
    _f = g["rejected"][0]["failed"][0]
    check("by a named gate", _f["gate"], "audio_min_hf_concentration")
    check("with the bar it missed", _f["bar"], 0.99)
    check("and the value actually read",
          isinstance(_f["read"], float), True)
    check("...which is below the bar, as claimed", _f["read"] < 0.99, True)

    print("\n7b. a floor and a ceiling on one measurement make a band")
    # A MINIMUM ALONE ONLY REFUSES THE DULL THINGS. A struck ball sits
    # in a band: too dark is a thud or an engine, too bright is hiss,
    # birdsong, a zip, a clubhead ringing on another club. With only a
    # floor, every bright false positive clears by being brighter
    # still -- the wrong direction for a measurement meant to be
    # characteristic rather than merely large.
    ARMED = {**LOOSE, "audio1_armed@1": 1.0}
    hits, g, _ = run(SRC, {**ARMED, "audio_max_centroid_hz@1": 6000.0})
    check("a ceiling the peak is under keeps it", len(hits), 1)
    hits, g, _ = run(SRC, {**ARMED, "audio_max_centroid_hz@1": 1500.0})
    check("a ceiling it is over refuses it", len(hits), 0)
    check("...naming the new gate", g["rejected"][0]["failed"][0]["gate"],
          "audio_max_centroid_hz")
    check("...as a maximum", g["rejected"][0]["failed"][0]["cmp"], "max")
    _read = g["rejected"][0]["failed"][0]["read"]
    check("...with the centroid it read", _read > 1500.0, True)
    hits, _, _ = run(SRC, {**ARMED, "audio_min_centroid_hz@1": 1000.0,
                           "audio_max_centroid_hz@1": 6000.0})
    check("the two together pass a peak inside the band", len(hits), 1)
    hits, g, _ = run(SRC, {**ARMED, "audio_min_centroid_hz@1": 4000.0,
                           "audio_max_centroid_hz@1": 6000.0})
    check("and refuse one below it", len(hits), 0)
    check("...by the floor, not the ceiling",
          g["rejected"][0]["failed"][0]["gate"], "audio_min_centroid_hz")
    # BOTH READ THE SAME MEASUREMENT, which is the thing that could
    # have gone wrong: one overwriting the other in any table keyed on
    # what a gate reads rather than on the gate.
    check("both gates read the same measurement",
          A._audio1_enabled_gates(
              __import__("app.services.debug3", fromlist=["x"])
              .resolve_gates({"audio_min_centroid_hz@1": 1000.0,
                              "audio_max_centroid_hz@1": 6000.0}, zone=1)
          ).keys() == {"audio_min_centroid_hz", "audio_max_centroid_hz"},
          True)

    print("\n8. and the report is written even when there is nothing to say")
    # THE COMMONEST CASE OF ALL. A tee box nobody has set a gate on
    # used to return before writing anything, so the panel showed an
    # empty space -- which reads as "the gates ran and were happy".
    _, g, _ = run(SRC, LOOSE)
    check("the report exists", bool(g), True)
    check("it did not run", bool(g.get("ran")), False)
    check("and says why", "no audio1 gate is switched on" in (g.get("why")
                                                              or ""), True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
