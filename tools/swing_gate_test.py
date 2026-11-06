#!/usr/bin/env python3
"""The gates in front of the swing judge, and the two copies of them.

WHY THE GATES EXIST. On 5 Nov the judge accepted this composite from
hole 8, and the operator, ChatGPT and Claude chat all read it as not a
swing:

    the only human-shaped heat in frame    45 x  94 px, h/w 2.09
    the thing it called a golfer          150 x 199 px, h/w 1.33
    distance between them                 837 px, 5.6 blob-widths

The judge described it back as "a compact hot figure with a cooler
plume extending upward consistent with a club arc", which is a fair
description of a 150x199 orange mass with blue edges. Rewriting that
sentence had already been tried three times in one morning and made
things worse each time. So instead: stop asking a model questions a
tape measure can answer.

WHY THE COPIES. swingjudge.py is installed twice -- on the rig as
agent/swingjudge.py and on the server as app/services/swingjudge.py --
because the rig cannot import the backend and the backend cannot
import the rig. The codebase already carries one such pair, and the
warning at the top of swingheat.py says what happens when it drifts:
the failure looks like the MODEL getting worse rather than one side
changing. So this holds them byte-identical, not merely equivalent.

    python3 tools/swing_gate_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

import numpy as np                                    # noqa: E402

from agent.swingjudge import (                        # noqa: E402
    aim_verdict, confidence_verdict, measure_heat, pick_stance, postjudge,
    prejudge, shape_verdict, stance_verdict, MIN_ASPECT,
)

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def heat_with(blobs, w=960, h=540, speckle=0):
    """A synthetic heat map: (x, y, bw, bh) boxes at full heat."""
    m = np.zeros((h, w), np.uint8)
    if speckle:
        rng = np.random.default_rng(11)
        ys = rng.integers(0, h, speckle)
        xs = rng.integers(0, w, speckle)
        m[ys, xs] = 255
    for (x, y, bw, bh) in blobs:
        m[y:y + bh, x:x + bw] = 255
    return m


def main() -> int:
    # ---- 1. the two copies are the same file ------------------------
    a = (ROOT / "pi-agent" / "agent" / "swingjudge.py").read_bytes()
    b = (ROOT / "backend" / "app" / "services" / "swingjudge.py").read_bytes()
    check("the rig's copy and the server's are byte-identical",
          a == b, True)
    if a != b:
        print("      cp pi-agent/agent/swingjudge.py "
              "backend/app/services/swingjudge.py")

    # ---- 2. measuring ------------------------------------------------
    g = measure_heat(heat_with([(400, 200, 60, 180)]))
    m = g["main"]
    check("a tall blob measures tall", (m["w"], m["h"]), (60, 180))
    check("  and its aspect is h/w", m["aspect"], 3.0)
    check("  with its base at the bottom centre", m["base"], (430, 380))

    # SPECKLE IS OPENED AWAY BEFORE ANYTHING IS JOINED. Closing first
    # bridges wind-blown foliage across the whole frame: on the 5 Nov
    # composite that reported one 1741x213 "object", which the shape
    # gate rejected for entirely the wrong reason and would have
    # rejected a real swing for the same one.
    g2 = measure_heat(heat_with([(400, 200, 60, 180)], speckle=4000))
    check("speckle does not become the main blob",
          (g2["main"]["w"], g2["main"]["h"]), (60, 180))

    # A club is a DOTTED plume -- the clubhead is only in any given
    # pixel for a frame or two -- and without closing the body and
    # each dash of the club come back as separate objects, which makes
    # a real swing measure short and square.
    _plume = [(420, 200 + k * 12, 14, 8) for k in range(5)]   # 4 px gaps
    g3 = measure_heat(heat_with([(400, 260, 60, 120)] + _plume))
    check("a dotted club plume joins to its body", g3["main"]["h"] >= 150,
          True)
    check("  and the body alone would have measured short",
          measure_heat(heat_with([(400, 260, 60, 120)]))["main"]["h"], 120)

    # WHICH BLOB IS THE GOLFER. Clip 1734, 6 Nov: a confirmed swing
    # whose golfer measured 74x134 at x=1813 and LOST to a bystander
    # rummaging in a bag beside a cart at x=26 -- 5677 px against
    # 4108. The crop, the frames the judge saw and the stance gate
    # then all pointed at the wrong man, and the judge correctly said
    # it was not a swing.
    m = np.zeros((1080, 1920), np.uint8)
    m[617:751, 26:97] = 255
    m[617:671, 26:97] = 0            # bystander, the larger area
    m[608:742, 1813:1887] = 255
    m[608:686, 1813:1887] = 0        # golfer, smaller
    check("largest wins when nothing says where the golfer is",
          measure_heat(m)["main"]["x"], 26)
    check("  the trigger zone picks the golfer instead",
          measure_heat(m, prefer_span=(728, 1845))["main"]["x"], 1813)
    check("  so does a detected stance point",
          measure_heat(m, prefer_xy=(1850, 880))["main"]["x"], 1813)
    check("  and the measurement says which it was",
          "size alone" in measure_heat(m)["picked_by"], True)

    check("an empty map measures nothing, and says so",
          measure_heat(np.zeros((540, 960), np.uint8))["ok"], False)

    # ---- 3. the shape gate is OFF, and that is a measurement --------
    # judge_bench, run over a swing whose heat is dominated by the CLUB
    # rather than the body, measured the main blob at 75x35 -- h/w 0.47,
    # WIDER THAN TALL, on a real swing. Any aspect floor above that
    # drops it.
    check("the aspect floor is off", MIN_ASPECT, 0.0)
    club = measure_heat(heat_with([(400, 300, 75, 35)]))
    check("  so a club-dominated swing is not refused on shape",
          shape_verdict(club)["ok"], True)
    wash = measure_heat(heat_with([(0, 0, 900, 500)]))
    check("  but a whole-frame wash still is", shape_verdict(wash)["ok"],
          False)
    check("nothing measured is never a rejection",
          shape_verdict({"ok": False})["ok"], True)

    # ---- 4. the stance gate -----------------------------------------
    # THE FREE SIGNAL. The rig has run a person detector all along and
    # recorded every sighting; nothing asked whether the thing that
    # MOVED is the person who was seen.
    g4 = measure_heat(heat_with([(400, 200, 60, 180)]))   # base (430, 380)
    check("feet under the motion pass", stance_verdict(g4, (430, 400))["ok"],
          True)
    check("  and a little to the side still pass",
          stance_verdict(g4, (470, 400))["ok"], True)
    # THE REAL ONE, at the real frame size -- 1920x991, the composite
    # the judge was handed. At 960x540 the blob falls off the canvas
    # and measures nothing, which reads as a pass.
    nov5 = measure_heat(heat_with([(1047, 579, 150, 199)], w=1920, h=991))
    check("the 5 Nov blob measures where it really was",
          (nov5["main"]["w"], nov5["main"]["h"]), (150, 199))
    check("  motion 837 px from the only person, refused",
          stance_verdict(nov5, (210, 736))["ok"], False)
    # Normalising by HEIGHT was the first version and judge_bench
    # refused a real swing on it: the blob was a 35 px club arc with
    # the golfer's feet 85 px below. Width has no such trap.
    club2 = measure_heat(heat_with([(400, 300, 75, 35)]))
    check("a club arc above its own golfer is not 'someone else'",
          stance_verdict(club2, (437, 390))["ok"], True)
    # AN ALLOWANCE THAT SCALES WITH THE BLOB LETS A BIG BLOB EXCUSE
    # ITSELF. Measured on clip 1768 through Test Cut's renderer, whose
    # 4.5s window accumulates far more motion than the rig's 1.0s one:
    # a 727 px wide blob made a golfer 900 px away only 0.74 of its
    # own widths off, and the gate waved it through.
    wide = measure_heat(heat_with([(500, 600, 727, 294)], w=1920, h=1080))
    _b = wide["main"]["base"]
    check("a wide blob cannot excuse a distant golfer",
          stance_verdict(wide, (_b[0] - 900, _b[1] + 10))["ok"], False)
    check("  but still passes one standing under it",
          stance_verdict(wide, (_b[0], _b[1] + 10))["ok"], True)

    check("motion ABOVE the feet with nothing between is refused",
          stance_verdict(g4, (430, 150))["ok"], False)
    check("no sighting is not a rejection", stance_verdict(g4, None)["ok"],
          True)

    # ---- 5. picking a sighting --------------------------------------
    rig = [(100.0, 0, 10, 20), (101.0, 0, 30, 40), (109.0, 0, 99, 99)]
    check("the rig's (t, zone, x, y) shape is read",
          pick_stance(rig, 101.2), (30.0, 40.0))
    check("the server's (t, x, y) shape is read",
          pick_stance([(101.0, 30, 40)], 101.2), (30.0, 40.0))
    check("a stale sighting is not used", pick_stance(rig, 120.0), None)
    check("a sighting from before the point existed is skipped, not guessed",
          pick_stance([(101.0, 0)], 101.0), None)

    # ---- 6. weighing a hedged yes against the microphone ------------
    # A medium verdict is the judge saying it is not sure. On 5 Nov a
    # medium yes on a x4.7 strike -- 1.18 times a x4.0 gate -- was
    # treated as the same evidence as a high verdict on a x40 crack.
    check("the 5 Nov verdict: a medium yes on x4.7 over a x4.0 gate",
          confidence_verdict(True, "medium", 4.7, 4.0)["ok"], False)
    # x6.6 against a x4.0 gate is 1.65 -- #1760 on 6 Nov, which was
    # NOT a swing. The threshold sits above it, in the gap before the
    # only genuine medium-confidence swing in that corpus at 4.95x.
    check("  and a medium yes at 1.65x the gate does not",
          confidence_verdict(True, "medium", 6.6, 4.0)["ok"], False)
    check("  the one real medium swing in the corpus, at 4.95x, stands",
          confidence_verdict(True, "medium", 19.8, 4.0)["ok"], True)
    check("high confidence stands on its own at any volume",
          confidence_verdict(True, "high", 4.7, 4.0)["ok"], True)
    check("a low yes needs much more",
          confidence_verdict(True, "low", 9.0, 4.0)["ok"], False)
    # ONLY EVER NARROWS A YES. A false verdict is the judge's to make,
    # and null has always meant "could not tell" -> upload.
    check("a no is left alone", confidence_verdict(False, "low", 1.0, 4.0)
          ["ok"], True)
    check("a null is left alone", confidence_verdict(None, None, 1.0, 4.0)
          ["ok"], True)
    check("no ratio to weigh means no opinion",
          confidence_verdict(True, "medium", None, 4.0)["ok"], True)

    # ---- 7. the two decisions callers actually make -----------------
    check("prejudge refuses the 5 Nov picture before any judge call",
          prejudge(nov5, (210, 736))["ask"], False)
    check("  and passes the same blob with a golfer standing under it",
          prejudge(nov5, (1120, 800))["ask"], True)
    check("  and passes it when nobody was detected at all",
          prejudge(nov5, None)["ask"], True)
    check("postjudge refuses the 5 Nov verdict",
          postjudge(True, "medium", 4.7, 4.0)["keep"], False)

    # ---- 8. where the ball left from -------------------------------
    # The `outside` gate already runs a chain back to the tee line and
    # refuses it outside the hole's tee box. On hole 8 that box is two
    # wide strips spanning most of the frame, so the 6 Nov sweep
    # accepted a chain running back to x=1018 while the golfer who
    # swung stood at x=1790 -- nearer the man holding his club at rest.
    check("the 6 Nov chain: runs back 772 px from the golfer, refused",
          aim_verdict(1018, 1790, 1920)["ok"], False)
    check("  a chain leaving from the golfer is kept",
          aim_verdict(1835, 1790, 1920)["ok"], True)
    check("  and one just inside the tolerance",
          aim_verdict(1790 - 0.11 * 1920, 1790, 1920)["ok"], True)
    check("  and just outside it",
          aim_verdict(1790 - 0.13 * 1920, 1790, 1920)["ok"], False)
    # MOST OF THE PIPELINE DOES NOT KNOW WHICH GOLFER SWUNG. A gate
    # that fired on absent evidence would refuse every chain on every
    # clip that never asked.
    check("no anchor is not a rejection", aim_verdict(1018, None, 1920)["ok"],
          True)
    check("no run-back is not a rejection", aim_verdict(None, 1790, 1920)
          ["ok"], True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
