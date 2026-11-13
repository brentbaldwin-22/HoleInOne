#!/usr/bin/env python3
"""A swing needs a club above the head, and now that is CHECKED.

THE OPERATOR, 13 Nov: "on the heat test, it's required that there be
a plume from the club swing. i'm getting some positive swings with no
plume at all. this was deemed a swing."

The heat prompt has said so for weeks -- "THE CLUB HAS TO SHOW ABOVE
THE HEAD... Answer false when nothing rises above the head" -- and
composites with no plume still come back as swings. Asking a model
more firmly is the move this file exists to replace: four attempts at
that wording have gone in and three were reverted the same evening.

So it is measured. `plume_above_head` reads the heat map the judge
was shown and says how far motion CONNECTED TO THE GOLFER rises above
their head; `plume_veto` turns a yes into a no when nothing does.

    python3 tools/plume_test.py
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
    import numpy as np
    from app.services.swingjudge import (
        plume_above_head, plume_veto, PLUME_MIN_RISE_FRAC)

    H, W = 600, 400
    G = {"x": 170, "y": 200, "w": 60, "h": 240}     # head at y=200

    def body():
        m = np.zeros((H, W), np.uint8)
        m[200:440, 170:230] = 200                   # the golfer, hot
        return m

    # ── THE REPORTED PICTURE ────────────────────────────────────────
    #
    # A body, a warm smear at the legs, and tree speckle across the
    # top of the frame. Nothing between the head and the speckle.
    a = body()
    a[440:560, 185:215] = 230
    rng = np.random.default_rng(3)
    for _ in range(120):
        a[rng.integers(0, 120), rng.integers(0, W)] = 90
    r = plume_above_head(a, G)
    ok("no plume: the rise is zero", r["rise_px"], 0)
    ok("  and it is a measurement, not a failure", r["ok"], True)
    ok("  sky speckle is a different component and does not count",
       "rises above their head" in r["why"], True)
    ok("  so a swing verdict is refused",
       plume_veto(True, r)["is_swing"], False)

    # ── A REAL SWING, AND A THIN ONE ────────────────────────────────
    #
    # The prompt's own description is "may be only a few pixels wide",
    # which is why the mask is NOT opened first: a 3x3 open removes
    # anything thinner than three pixels and would erase the club.
    b = body()
    b[120:205, 198:203] = 40                        # 5px wide, cool
    r = plume_above_head(b, G)
    ok("a 5px cool club is seen", r["rise_px"] > 0, True)
    ok("  and measured as a fraction of the golfer",
       r["rise_frac"] >= PLUME_MIN_RISE_FRAC, True)
    ok("  so the verdict stands", plume_veto(True, r)["is_swing"], True)
    print("     rise %dpx = %.0f%% of a %dpx golfer"
          % (r["rise_px"], r["rise_frac"] * 100, G["h"]))

    # ── A WAGGLE ────────────────────────────────────────────────────
    c = body()
    c[300:380, 235:275] = 60                        # motion beside the body
    ok("motion beside the body is not a club above the head",
       plume_veto(True, plume_above_head(c, G))["is_swing"], False)

    # ── AND THE THREE THINGS IT MUST NEVER DO ───────────────────────
    #
    # NULL IS NOT FALSE anywhere in this system, and an unmeasurable
    # clip is not a refusal. A crop that could not be cut came back as
    # "not a swing" for a day and deleted the clips; that lesson is
    # what these three pin.
    _un = plume_above_head(None, G)
    ok("unmeasurable is not a verdict", _un["ok"], False)
    ok("  so the judge's answer stands",
       plume_veto(True, _un)["is_swing"], True)
    ok("a 'no' is never argued with",
       plume_veto(False, plume_above_head(b, G))["is_swing"], False)
    ok("a 'could not tell' stays could-not-tell",
       plume_veto(None, plume_above_head(a, G))["is_swing"], None)
    ok("  and only a veto is reported as one",
       plume_veto(True, plume_above_head(b, G))["vetoed"], False)

    # ── A HEAD AT THE TOP OF THE PICTURE IS NOT A REFUSAL ───────────
    ok("nothing above to look in is not 'no plume'",
       plume_veto(True, plume_above_head(body(), {"x": 170, "y": 0,
                                                  "w": 60, "h": 240})
                  )["is_swing"], True)

    # ── THE RIG'S PATH, WHICH IS THE ONE THAT WAS FIRING ───────────
    #
    # The veto first went into the Test cut only, and the complaint
    # was about the RIG. The rig's picture came back: a figure with
    # warm motion down one side, nothing above the head, verdict
    # SWING at x11.2. "under no iteration should that be a swing."
    #
    # `analyse` only ever sees the COLOUR composite -- 0.4 photo
    # blended with 0.6 of a TURBO colormap -- so it cannot measure
    # this. The rig does, against its own hot blob, and sends the
    # number in `geo`.
    from app.services.swingjudge import measure_heat
    H2, W2 = 1000, 880
    m = np.zeros((H2, W2), np.uint8)
    m[600:770, 430:450] = 210                   # body, head top y=600
    m[610:700, 450:470] = 240                   # warm streak, one side
    _r = np.random.default_rng(5)
    for _ in range(900):
        m[_r.integers(0, H2), _r.integers(0, W2)] = _r.integers(40, 110)
    _blob = measure_heat(m).get("main")
    ok("the hot blob is the body", _blob is not None, True)
    _pl = plume_above_head(m, _blob)
    ok("  and nothing rises off it", _pl["rise_px"], 0)
    ok("  so a swing verdict is refused",
       plume_veto(True, _pl)["is_swing"], False)
    m2 = m.copy()
    m2[480:605, 452:458] = 45                   # a club, cool, 6px wide
    _pl2 = plume_above_head(m2, measure_heat(m2).get("main"))
    ok("the same figure WITH a club is kept",
       plume_veto(True, _pl2)["is_swing"], True)
    print("     no club %dpx, with club %dpx"
          % (_pl["rise_px"], _pl2["rise_px"]))

    _sh = (ROOT / "pi-agent/agent/swingheat.py").read_text()
    ok("the rig measures it against its own hot blob",
       'plume_above_head(\n                            hm_n, _m)' in _sh
       or "plume_above_head(" in _sh, True)
    ok("  and sends it in geo, which travels",
       '_geo["plume"]' in _sh, True)
    _sa = (ROOT / "backend/app/services/swinganalysis.py").read_text()
    ok("the verify path applies it to the VERDICT, not as a vote",
       '_sj.plume_veto(out["is_swing"]' in _sa, True)
    ok("  after the three tests have voted",
       _sa.index("decide_verdict(") < _sa.index("_sj.plume_veto("), True)
    ok("  keeping what it overruled",
       'out["is_swing_before_plume"]' in _sa, True)
    ok("  and telling the rig why",
       '"plume_why")' in
       (ROOT / "backend/app/routers/cameras.py").read_text(), True)

    # ── WIRED WHERE THE RAW HEAT MAP IS ─────────────────────────────
    _tr = (ROOT / "backend/app/services/tracer.py").read_text()
    ok("swing_heat_check measures it", 'out["plume"]' in _tr, True)
    _ad = (ROOT / "backend/app/routers/admin.py").read_text()
    ok("the Test cut applies it", "_sj.plume_veto(" in _ad, True)
    ok("  but not across a multi-golfer ask",
       'not cut_entry.get("candidates")' in _ad, True)
    ok("the rig and the backend share one copy",
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
