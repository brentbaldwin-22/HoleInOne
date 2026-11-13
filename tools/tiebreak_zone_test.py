#!/usr/bin/env python3
"""The heat may only choose between golfers who are ON the tee.

THE FIELD REPORT, twice. First: "why are we zooming on a golfer by the
golf cart, nowhere near the trigger zone? we should never zoom in on
someone not in the zone." Then, after the crop fallback was fixed:
"still grabbing this guy clearly not in the zone."

The second report is the interesting one, because the fallback was
right by then -- it was never reached. `pick_front_golfer` declines
when two golfers are level ("too close to call which is hitting"), and
both callers then ask `break_tie_on_motion` which of them moved. They
passed EVERY person the detector found, and it scored them all.

A golfer at address is not moving. A man walking past the golf carts
is. He won the tie-break, and because the tie-break assigns `front`
directly he arrived downstream as a confirmed golfer -- `golfer_crop`
true, zone tests all behind him.

The geometry here is read off the reported frame: zones starting at
x=467, and three detected foot points, one of them at x=318 by the
carts.

    python3 tools/tiebreak_zone_test.py
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
    from app.services.swingjudge import break_tie_on_motion

    # The three crosshairs from the reported frame.
    CART = {"box": [300, 400, 36, 96], "feet": [318, 457]}   # by the carts
    Z3 = {"box": [575, 400, 36, 96], "feet": [593, 463]}     # in zone 3
    Z2 = {"box": [719, 404, 36, 96], "feet": [737, 467]}     # in zone 2
    PEOPLE = [CART, Z3, Z2]

    # What the picker recorded: only two of them are on the tee.
    CONSIDERED = [
        {"feet": [318, 457], "box": CART["box"], "zone": False},
        {"feet": [593, 463], "box": Z3["box"], "zone": True},
        {"feet": [737, 467], "box": Z2["box"], "zone": True},
    ]

    # The man by the carts is WALKING, so he holds by far the most
    # motion. The golfers are at address and barely register.
    BLOBS = [
        {"x": 298, "y": 398, "w": 60, "h": 110},   # him, striding
        {"x": 580, "y": 470, "w": 14, "h": 20},    # a glance of a golfer
        {"x": 724, "y": 474, "w": 12, "h": 18},
    ]

    # ── THE BUG, PINNED ─────────────────────────────────────────────
    _old = break_tie_on_motion(PEOPLE, BLOBS)
    ok("unzoned, the walking man wins the tie-break",
       (_old["pick"] or {}).get("feet"), CART["feet"])
    print("     unzoned: %s" % _old["why"])

    # ── AND THE FIX ─────────────────────────────────────────────────
    _new = break_tie_on_motion(PEOPLE, BLOBS, zoned=CONSIDERED)
    ok("zoned, he cannot be picked at all",
       (_new["pick"] or {}).get("feet") != CART["feet"], True)
    ok("  and the choice falls to a golfer on the tee",
       (_new["pick"] or {}).get("feet") in ([593, 463], [737, 467]), True)
    print("     zoned:   %s" % _new["why"])

    # The one holding the most motion OF THOSE ON THE TEE wins, which
    # is what the rule was always for.
    ok("  the one of them holding the most motion",
       (_new["pick"] or {}).get("feet"), Z3["feet"])

    # ── NOBODY ON THE TEE MEANS NO ANSWER ───────────────────────────
    #
    # Not "pick the best of a bad lot": an empty tee with motion
    # somewhere else has not told us who was hitting, and saying so
    # sends the crop to the zones instead of to a stranger.
    _none = break_tie_on_motion(
        PEOPLE, BLOBS,
        zoned=[{"feet": [318, 457], "box": CART["box"], "zone": False}])
    ok("with nobody in a zone there is no pick", _none["pick"], None)
    ok("  and it says why, in the zones' terms",
       "standing in a zone" in _none["why"], True)

    # ── THE OLD CONTRACT STILL HOLDS WHERE IT IS WANTED ─────────────
    ok("zoned=None keeps the previous behaviour",
       (break_tie_on_motion(PEOPLE, BLOBS)["pick"] or {}).get("feet"),
       CART["feet"])
    ok("no motion anywhere is still no pick",
       break_tie_on_motion(PEOPLE, [], zoned=CONSIDERED)["pick"], None)
    ok("  and junk does not raise",
       break_tie_on_motion([None, {}], BLOBS, zoned=CONSIDERED)["pick"],
       None)

    # ── BOTH CALLERS PASS IT ────────────────────────────────────────
    #
    # There are two copies of this crop decision -- /verify-swing goes
    # through swinganalysis.analyse, the Test cut page through
    # tracer.swing_heat_check -- and a fix that reaches only one of
    # them is why the second report happened at all.
    for _rel in ("backend/app/services/swinganalysis.py",
                 "backend/app/services/tracer.py"):
        _src = (ROOT / _rel).read_text()
        _i = _src.find("break_tie_on_motion(")
        _tail = _src[_i:_i + 400]
        ok("%s passes zoned=" % _rel.split("/")[-1],
           "zoned=" in _tail and "considered" in _tail, True)

    # And the rig's copy of the judge is the backend's, byte for byte.
    ok("the rig and the backend share one judge",
       (ROOT / "pi-agent/agent/swingjudge.py").read_bytes()
       == (ROOT / "backend/app/services/swingjudge.py").read_bytes(), True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
