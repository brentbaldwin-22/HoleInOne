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
    aim_verdict, break_tie_on_motion, confidence_verdict, decide_verdict,
    measure_heat, pick_front_golfer,
    pick_stance, postjudge, prejudge, shape_verdict, stance_verdict,
    tiebreak, MIN_ASPECT,
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

    # ---- the operator's tie-break ----------------------------------
    # THE RULE, 7 Nov: when the heat and the frames contradict each
    # other, a strike over x10 is a swing and anything quieter is not.
    check("agreement is not a split", tiebreak(True, True, 3.0)["split"],
          False)
    check("  and the agreed answer stands", tiebreak(True, True, 3.0)
          ["is_swing"], True)
    check("  so does an agreed no", tiebreak(False, False, 99.0)["is_swing"],
          False)
    # A NULL IS "COULD NOT TELL", not a contradiction.
    check("a null from one judge is not a split",
          tiebreak(None, True, 3.0)["split"], False)
    check("  either way round", tiebreak(True, None, 3.0)["split"], False)
    # THE SPLIT ITSELF, both directions, either side of x10.
    check("heat yes, frames no, loud -> swing",
          tiebreak(True, False, 12.4)["is_swing"], True)
    check("heat yes, frames no, quiet -> not",
          tiebreak(True, False, 5.2)["is_swing"], False)
    check("heat no, frames yes, loud -> swing",
          tiebreak(False, True, 103.7)["is_swing"], True)
    check("heat no, frames yes, quiet -> not",
          tiebreak(False, True, 9.9)["is_swing"], False)
    # EXACTLY x10 IS NOT OVER x10. "if it's over 10, call it a swing."
    check("  x10.0 exactly is under the bar",
          tiebreak(True, False, 10.0)["is_swing"], False)
    check("  and x10.1 is over it", tiebreak(True, False, 10.1)["is_swing"],
          True)
    # NO SOUND TO ASK -- a long upload with no ratio recorded. Falls
    # back to the frames, which is what this did before the rule.
    check("no ratio falls back to the frames",
          tiebreak(True, False, None)["is_swing"], False)
    check("  and still reports the split",
          tiebreak(True, False, None)["split"], True)
    # AND THE TIE-BREAK SURVIVES postjudge. The confidence gate
    # narrows hedged yeses on quiet strikes; a verdict the ratio has
    # already decided is not the judge's hedge to second-guess.
    check("postjudge leaves a tie-break alone",
          postjudge(True, "tiebreak", 10.4, 4.0)["keep"], True)

    # ---- the rig's wire shape --------------------------------------
    # THE RIG BREAKS THE TIE, NOT THE BACKEND, because the x-ratio
    # never leaves the rig. What arrives is /verify-swing's response
    # dict, so exercise THAT shape rather than the arguments.
    def rig(resp, ratio):
        """What copyengine does with a /verify-swing reply."""
        v, conf = resp.get("is_swing"), resp.get("confidence")
        t = tiebreak(resp.get("heat_is_swing"),
                     resp.get("frames_is_swing"), ratio)
        if t["split"]:
            v, conf = t["is_swing"], "tiebreak"
        return postjudge(v, conf, ratio, 4.0)["keep"] and v

    # A BACKEND TOO OLD TO SEND THE PAIR. Both keys absent -> no
    # split -> the frames answer stands, exactly as it did before.
    check("an old backend's reply is unchanged",
          rig({"is_swing": True, "confidence": "high"}, 12.0), True)
    check("  including its noes",
          rig({"is_swing": False, "confidence": "high"}, 12.0), False)
    # AGREEMENT: nothing to settle, the headline answer stands.
    check("agreement passes straight through",
          rig({"is_swing": True, "confidence": "high",
               "heat_is_swing": True, "frames_is_swing": True}, 12.0), True)
    # A SPLIT, LOUD. The frames said no; x12 overrides them.
    check("a loud split becomes a swing",
          rig({"is_swing": False, "confidence": "high",
               "heat_is_swing": True, "frames_is_swing": False}, 12.0), True)
    # A SPLIT, QUIET. The frames said yes; x5 refuses it.
    check("a quiet split is refused",
          rig({"is_swing": True, "confidence": "high",
               "heat_is_swing": False, "frames_is_swing": True}, 5.0), False)
    # AND THE TIE-BREAK SURVIVES postjudge AT THE EDGE. x10.1 against
    # a x4.0 gate is 2.5x -- over the medium bar anyway -- but a
    # tie-break must not be re-weighed whatever the arithmetic says.
    check("a tie-break is not re-weighed by postjudge",
          rig({"is_swing": False, "confidence": "medium",
               "heat_is_swing": True, "frames_is_swing": False}, 10.1), True)
    # ONE JUDGE FAILED on the server: its keys come back None. Not a
    # contradiction -- the headline answer stands.
    check("a failed judge is not a contradiction",
          rig({"is_swing": True, "confidence": "high",
               "heat_is_swing": None, "frames_is_swing": True}, 12.0), True)

    # ---- audio 2: which golfer is hitting --------------------------
    # THE RULE: among golfers whose feet are in a trigger zone, the
    # one furthest UP the picture is the one who has walked forward to
    # the markers. Blobs are in crop pixels; zones in frame pixels.
    ZONES = [(700, 800, 220, 90), (960, 820, 200, 80)]

    def blob(x, y, w, h):
        return {"x": x, "y": y, "w": w, "h": h,
                "base": (x + w // 2, y + h)}

    # Two on the tee: one at the markers (feet y=840), one back
    # (feet y=890). 50 px apart against a 0.25 x 200 = 50 px bar --
    # just clear.
    front = blob(740, 640, 140, 200)        # feet (810, 840)
    back = blob(990, 690, 130, 200)         # feet (1055, 890)
    r = pick_front_golfer([back, front], ZONES)
    check("picks the golfer furthest up the tee",
          r["pick"] is front, True)
    check("  and says which", "furthest up" in r["why"], True)
    # THE GROUND LINE IS THE ZONE'S BOTTOM, NOT THE BLOB'S. Zone 1 is
    # (700, 800, 220, 90), so its floor is y=890. The front golfer's
    # blob ends at y=840 -- 50 px higher, which is where the first
    # version cropped and why the player came out cut off at the belt.
    check("the floor is the zone's bottom edge", r["floor"], 890)
    # Order must not decide it.
    check("  whichever order they arrive in",
          pick_front_golfer([front, back], ZONES)["pick"] is front, True)
    # LEVEL ON THE TEE -> no pick, and say why. 20 px apart.
    _lvl = blob(990, 660, 130, 200)         # feet (1055, 860)
    r = pick_front_golfer([_lvl, front], ZONES)
    check("two level golfers is too close to call",
          r["pick"] is None and r["close"], True)
    check("  which is not the same as nobody there",
          "too close" in r["why"], True)
    # THE ZONE THEY ARE IN, not the union. Standing in zone 2
    # (960, 820, 200, 80) must give 900, not zone 1's 890.
    _z2 = blob(1000, 660, 130, 200)         # feet (1065, 860) in zone 2
    check("the floor comes from the zone they stand in",
          pick_front_golfer([_z2], ZONES)["floor"], 900)
    # AND THE ZONE THEY ARE IN BEATS ONE THE SLACK MERELY REACHES.
    # Two stacked boxes on the same x: a FAR one (up the picture) and
    # a NEAR one below it. A golfer standing in the far box is within
    # one near-box height of the near box too, so first-match-wins
    # put them on the near box's ground line -- 150 px below their
    # feet -- and the crop started there. This is the 8 Nov report.
    STACKED = [
        (700, 900, 220, 120),    # "zone 1", NEAR, listed first
        (700, 700, 220, 80),     # "zone 2", FAR, where they stand
    ]
    _far = blob(740, 560, 140, 200)          # feet (810, 760): in zone 2
    _r2 = pick_front_golfer([_far], STACKED)
    check("a golfer in the far zone gets the FAR zone's floor",
          _r2["floor"], 780)
    check("  not the near zone's, which the slack also reaches",
          _r2["floor"] == 1020, False)
    # Order in the list must not decide it either.
    check("  whichever order the zones are listed in",
          pick_front_golfer([_far], list(reversed(STACKED)))["floor"], 780)
    # A golfer genuinely in the NEAR box still gets the near floor.
    _near = blob(740, 760, 140, 200)         # feet (810, 960): in zone 1
    check("  and the near zone still wins when they are in it",
          pick_front_golfer([_near], STACKED)["floor"], 1020)
    # FEET OUTSIDE EVERY ZONE -> no pick, and a different reason.
    _off = blob(100, 640, 140, 200)         # feet (170, 840)
    r = pick_front_golfer([_off], ZONES)
    check("feet outside the zones is no pick",
          r["pick"] is None, True)
    check("  and is NOT reported as too close", r["close"], False)
    # THE SLACK. Heat that stops at the waist leaves the blob's bottom
    # well above the real stance; one zone height of slack keeps that
    # golfer eligible.
    _waist = blob(740, 600, 140, 130)       # feet (810, 730), zone top 800
    _rw = pick_front_golfer([_waist], ZONES)
    check("heat stopping above the zone still counts",
          _rw["pick"] is _waist, True)
    # AND THIS IS THE CASE THAT MATTERS: motion that stops at the
    # waist still gets the real ground line, 160 px below the blob.
    check("  and still gets the ground line from the zone",
          _rw["floor"], 890)
    _high = blob(740, 300, 140, 130)        # feet (810, 430) -- far above
    check("  but not a blob in the sky",
          pick_front_golfer([_high], ZONES)["pick"] is None, True)
    # NO ZONES AT ALL -> nothing to pick from.
    check("no zones means no pick",
          pick_front_golfer([front], None)["pick"] is None, True)

    # ---- and with a real person detector ---------------------------
    # THE FEET COME FROM A BOX, NOT FROM MOTION. A golfer at address
    # is not moving their legs, so the heat stops at the waist and
    # every statistic over those pixels reports a stance at chest
    # height. The detector finds a body whether or not it moved.
    PZ = [(300, 720, 900, 110)]                   # one zone, floor 830
    def person(x, y, w, h, c=0.9):
        return {"box": [x, y, w, h], "feet": [x + w // 2, y + h],
                "conf": c}
    stand = person(840, 400, 90, 400)             # feet y=800
    # Feet INSIDE the zone (720..830) and further up than `stand`.
    bend = person(1070, 340, 150, 400)            # feet y=740
    r = pick_front_golfer([], PZ, people=[stand, bend])
    check("the detector's people are used over the blobs",
          r["from"], "the person detector")
    check("  and the golfer further up wins",
          r["pick"]["feet"], [1145, 740])
    check("  with the zone's floor, not the box's bottom",
          r["floor"], 830)
    # THE FEET IT REPORTS ARE THE BOX BOTTOM, every time.
    check("  feet are the bottom of the box",
          all(c["feet"][1] == c["box"][1] + c["box"][3]
              for c in r["considered"]), True)
    # BLOBS ARE IGNORED WHEN PEOPLE ARE GIVEN -- a decoy blob whose
    # "feet" sit higher than anybody must not win.
    decoy = blob(900, 300, 120, 200)              # blob feet y=500
    r2 = pick_front_golfer([decoy], PZ, people=[stand, bend])
    check("a heat blob cannot outvote a detected person",
          r2["pick"]["feet"], [1145, 740])
    # NO PEOPLE -> the blobs are still the fallback, and it says so.
    r3 = pick_front_golfer([front], ZONES, people=[])
    check("no detections falls back to the heat",
          r3["from"], "the motion heat (no person detector)")
    # THE MARGIN IS 0.15, AND IT IS SET WIDE ON PURPOSE. Two measured
    # clips sit 5% apart and want opposite answers -- 0.067 must
    # decline, 0.071 must separate -- so no value of this gets both
    # right. It is set above both, and everything inside it goes to
    # break_tie_on_motion. `stand` has feet at y=800.
    #
    # A CLEAR DIFFERENCE IN DEPTH still belongs to the feet: a golfer
    # at the markers against one standing well back.
    _step = person(1070, 290, 150, 440)           # feet y=730: 70 px up
    check("a clear step forward is still settled by the feet",
          pick_front_golfer([], PZ, people=[stand, _step])["pick"] is not None,
          True)
    # AND EVERYTHING NEARER THAN THAT IS HANDED ON, including the
    # 24 px gap an earlier margin was tuned to separate. The feet
    # decline; the heat is asked.
    _close = person(1070, 400, 150, 376)          # feet y=776: 24 px up
    _rc = pick_front_golfer([], PZ, people=[stand, _close])
    check("  but a 24 px gap is now handed to the heat",
          _rc["pick"] is None and _rc["close"], True)
    # ...which is not the same as nobody being there.
    check("  and reported as too close, not as nobody found",
          "too close" in _rc["why"], True)
    _abreast = person(1070, 416, 150, 380)        # feet y=796: 4 px up
    check("  as is four pixels",
          pick_front_golfer([], PZ,
                            people=[stand, _abreast])["close"], True)
    # THE TREE, 8 Nov. The detector offered a palm as a person; its
    # feet sat well ABOVE the zone, and one zone height of slack --
    # which exists only to excuse a motion blob ending at the waist
    # -- waved it through to win. Real feet need no such reach.
    # PZ's zone is y 720..830, so 110 px tall; the tree's feet are
    # 120 px above its top.
    tree = person(600, 300, 110, 300)             # feet y=600
    r4 = pick_front_golfer([], PZ, people=[tree])
    check("a detection above the zones is refused",
          r4["pick"] is None, True)
    check("  and is reported as out of the zones, not too close",
          r4["close"], False)
    check("  and still appears in the working, marked out-of-zone",
          [c["zone"] for c in r4["considered"]], [False])
    # ...while the real golfer beside it is unaffected.
    r5 = pick_front_golfer([], PZ, people=[tree, stand])
    check("  and the golfer on the tee still wins",
          r5["pick"]["feet"], [885, 800])
    # THE BLOB FALLBACK KEEPS ITS SLACK, because a blob really does
    # stop at the waist.
    check("the heat fallback still forgives a high blob",
          pick_front_golfer([_waist], ZONES)["pick"] is _waist, True)

    # ---- when the feet are level, the heat decides ------------------
    # TWO PLAYERS ABREAST look identical to a rule about feet, and on
    # 8 Nov that cost a legitimate swing: the partner stood well back,
    # the stance rule had nothing to separate them and declined, and
    # the whole-tee crop went to the wrong man. They do not look
    # identical to the MOTION.
    LEFT = {"box": [500, 200, 90, 380], "feet": [545, 580]}
    RIGHT = {"box": [800, 200, 90, 380], "feet": [845, 580]}
    # The swing: a wide hot blob over the right-hand golfer. Blobs
    # are in CROP pixels, the crop sits at (100, 50) in the frame.
    SWING = {"x": 690, "y": 170, "w": 160, "h": 300}
    r = break_tie_on_motion([LEFT, RIGHT], [SWING], offset=(100, 50))
    check("the golfer holding the motion wins",
          r["pick"] is RIGHT, True)
    check("  and it says the heat decided", "heat decided" in r["why"],
          True)
    # The other way round, so it is not just picking the last one.
    SWING_L = {"x": 390, "y": 170, "w": 160, "h": 300}
    check("  whichever side the motion is on",
          break_tie_on_motion([LEFT, RIGHT], [SWING_L],
                              offset=(100, 50))["pick"] is LEFT, True)
    # MOTION SOMEWHERE ELSE ENTIRELY is not an answer. A tee with two
    # still golfers and a cart moving behind them has not told us who
    # was hitting.
    ELSEWHERE = {"x": 1200, "y": 170, "w": 100, "h": 100}
    check("motion over nobody decides nothing",
          break_tie_on_motion([LEFT, RIGHT], [ELSEWHERE],
                              offset=(100, 50))["pick"] is None, True)
    check("  and no blobs at all likewise",
          break_tie_on_motion([LEFT, RIGHT], [], offset=(100, 50))
          ["pick"] is None, True)

    # ---- three tests, majority wins --------------------------------
    # THE BALL STOPS BEING A VETO. It was overturning two judges that
    # agreed, on the strength of a few bright pixels, and on one clip
    # those pixels were a shoe. As one vote of three it still decides
    # every split -- which is what it was brought in for.
    def V(h, f, b, ratio=5.0):
        return decide_verdict(h, f, b, ratio)

    check("two judges agreeing beat a dissenting ball",
          V(True, True, False)["is_swing"], True)
    check("  and the ball cannot overturn an agreed no",
          V(False, False, True)["is_swing"], False)
    check("  which is reported as a majority",
          V(True, True, False)["how"], "majority")
    # THE SPLIT IS WHERE IT EARNS ITS PLACE.
    check("the ball decides a split, for a swing",
          V(True, False, True)["is_swing"], True)
    check("  and against one", V(True, False, False)["is_swing"], False)
    check("  without the x-ratio being consulted",
          V(True, False, False)["how"], "majority")
    # NO BALL -> THE X-RATIO IS BACK. "If ball test can't happen
    # because of no zoom or obstruction, then use the xaudio."
    check("a split with no ball falls to the x-ratio",
          V(True, False, None, 12.0)["is_swing"], True)
    check("  which still respects the x10 bar",
          V(True, False, None, 5.2)["is_swing"], False)
    check("  and says so", V(True, False, None, 5.2)["how"], "x-ratio")
    # AND AGREEMENT NEEDS NOTHING BREAKING.
    check("two judges agreeing with no ball stand",
          V(True, True, None)["is_swing"], True)
    # A TEST THAT COULD NOT ANSWER DOES NOT VOTE.
    check("one judge silent leaves the other two to it",
          V(None, True, True)["is_swing"], True)
    check("  and the ball alone decides when both are silent",
          V(None, None, False)["is_swing"], False)
    check("  reported as a single test",
          V(None, None, False)["how"], "one test")
    check("nothing to go on is None",
          V(None, None, None)["is_swing"], None)
    # AN EVEN SPLIT WITH NO RATIO EITHER falls back to the frames,
    # which is tiebreak's own long-standing rule: they at least show
    # a club and a body through time. Not a new decision here.
    check("an even split and no ratio leaves the frames standing",
          V(True, False, None, None)["is_swing"], False)
    check("  and says why", "no x-ratio" in V(True, False, None, None)["why"],
          True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
