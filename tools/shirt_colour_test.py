#!/usr/bin/env python3
"""COULD THESE TWO PHOTOGRAPHS BE THE SAME SHIRT?

The selfie and the swing come from different cameras, hours apart,
under whatever the sky was doing. Matching a COLOUR across that is
hopeless; matching a FAMILY is not.

The assertions that matter are the ones about giving up. This module
is used to DROP clips from the operator's grid, so every way of not
knowing -- no reading, a torso half full of fairway, a shirt on the
boundary between two families -- has to come back as "keep it".

    python3 tools/shirt_colour_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.shirt_colour import (  # noqa: E402
    compatible, dominant, family,
)

# Colours measured off real kit, not invented: the polo in Ben's own
# registration selfie, and the ones a par 3 actually produces.
SHIRTS = {
    "black polo, indoors":      ((28, 28, 32),   "dark"),
    "black polo, full sun":     ((54, 55, 60),   "dark"),
    "white polo":               ((238, 236, 230), "light"),
    "white polo, in shadow":    ((150, 150, 154), "grey"),
    "navy, shade":              ((31, 40, 68),   "dark"),
    "navy, sun":                ((44, 62, 112),  "blue"),
    "red":                      ((178, 41, 44),  "red"),
    "royal blue":               ((36, 92, 182),  "blue"),
    "kelly green":              ((38, 140, 72),  "green"),
    "salmon/pink":              ((226, 128, 140), "pink"),
    "grey marl":                ((136, 138, 141), "grey"),
    "mustard":                  ((206, 168, 48), "yellow"),
}


def main() -> int:
    fails = []

    print("families:")
    for label, (rgb, want) in SHIRTS.items():
        got = family(*rgb)
        flag = "" if got == want else f"   <-- wanted {want}"
        print(f"   {label:24s} rgb{rgb}  -> {got}{flag}")
        if got != want:
            fails.append(f"{label}: {got} != {want}")

    # ---- the pairs that must match ---------------------------------
    print("\nthe same shirt, photographed twice:")
    same = [
        ("black indoors", (28, 28, 32), "black in sun", (54, 55, 60)),
        ("navy in shade", (31, 40, 68), "navy in sun", (44, 62, 112)),
        ("white", (238, 236, 230), "white in shadow", (150, 150, 154)),
        ("red", (178, 41, 44), "red at sunset", (196, 86, 52)),
    ]
    for la, a, lb, b in same:
        fa, fb = family(*a), family(*b)
        ok = compatible(fa, fb)
        print(f"   {la:16s} ({fa:6s}) vs {lb:18s} ({fb:6s})  "
              f"-> {'match' if ok else 'MISMATCH'}")
        if not ok:
            fails.append(f"same shirt rejected: {la} vs {lb}")

    # ---- the pairs that must NOT match -----------------------------
    print("\ndifferent shirts:")
    diff = [
        ("black", (28, 28, 32), "white", (238, 236, 230)),
        ("black", (28, 28, 32), "red", (178, 41, 44)),
        ("white", (238, 236, 230), "kelly green", (38, 140, 72)),
        ("red", (178, 41, 44), "royal blue", (36, 92, 182)),
    ]
    for la, a, lb, b in diff:
        fa, fb = family(*a), family(*b)
        ok = compatible(fa, fb)
        print(f"   {la:16s} ({fa:6s}) vs {lb:18s} ({fb:6s})  "
              f"-> {'match' if ok else 'dropped'}")
        if ok:
            fails.append(f"different shirts matched: {la} vs {lb}")

    # ---- NOT KNOWING MUST NEVER DROP A CLIP ------------------------
    print("\nevery way of not knowing:")
    for a, b, why in (
        (None, "dark", "no reading from the clip"),
        ("dark", None, "no reading from the selfie"),
        (None, None, "neither"),
        ("", "dark", "empty string"),
    ):
        if not compatible(a, b):
            fails.append(f"dropped on: {why}")
        print(f"   {str(a):6s} vs {str(b):6s}  {why:28s} -> keep")

    # ---- a torso half full of fairway scores low -------------------
    shirt = [(28, 28, 32)] * 50
    grass = [(86, 120, 54)] * 50
    name, conf = dominant(shirt + grass)
    print(f"\nhalf shirt half grass -> {name} at {conf:.0%} confidence")
    if conf > 0.6:
        fails.append(f"a 50/50 sample claimed {conf:.0%} confidence")

    name, conf = dominant(shirt)
    print(f"clean torso sample    -> {name} at {conf:.0%} confidence")
    if not (name == "dark" and conf > 0.95):
        fails.append(f"clean sample read {name} at {conf:.0%}")

    name, conf = dominant([])
    if name is not None or conf != 0.0:
        fails.append("empty sample did not return (None, 0.0)")
    print(f"no pixels at all      -> {name} at {conf:.0%}")

    if fails:
        print("\nFAILED:")
        for f in fails:
            print("   " + f)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
