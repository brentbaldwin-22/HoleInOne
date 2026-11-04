#!/usr/bin/env python3
"""Do the rig and the server pick the same swing out of a quiet period?

TWO COPIES THAT CANNOT BE ONE. The test-cut endpoint resolves this
contest over a whole clip, after every candidate has been judged; the
rig resolves it in real time, inside a hold window, with the future
still unheard. They are different algorithms by necessity and they
deploy separately, so the ranking rule exists twice:

    backend/app/routers/admin.py    _swing_strength(cut)
    pi-agent/agent/copyengine.py    _swing_strength(conf, detail)

A rig that picks a different candidate from the one the operator's
tuning table picks on the same audio is worse than either rule alone,
because the table is how the rule gets tuned. So this holds the two to
the same answers over the same inputs.

IT ALSO PINS WHAT CHANGED AND WHY. The rig used to replace its leader
only on STRICTLY higher confidence, keeping the first on a tie,
because a golfer lowering his follow-through paints the same
figure-and-plume and arrives AFTER the strike. The ratio answers that
argument rather than ignoring it: a strike is the loud one, and a
waggle, a practice swing and a lowered follow-through are all quiet.

And it is not min_separation_sec, which sorted by loudness ALONE and
let a false positive at 15.43s delete a genuine swing 5.6s later. The
ratio is read only after the judge has said swing, and only to
separate candidates it rated the same.

    python3 tools/pick_best_rank_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "pi-agent"))

from app.routers.admin import _swing_strength as server_rank  # noqa: E402
from agent.copyengine import _swing_strength as rig_rank  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


# (confidence, ratio) pairs covering every band, both directions, the
# missing-value cases each side can be handed, and the nonsense a
# judge or a detector could emit.
CASES = [
    ("high", 40.8), ("high", 18.8), ("high", 0.0),
    ("medium", 40.8), ("medium", 18.8), ("medium", 5.0),
    ("low", 99.0), ("low", 1.0),
    (None, 50.0), ("", 3.0), ("HIGH", 7.0), (" Medium ", 7.0),
    ("nonsense", 12.0), ("high", None), ("medium", "x"),
]


def main() -> int:
    # ---- the two rules agree, value for value ----------------------
    _diff = [(c, r, server_rank({"verify_confidence": c, "ratio": r}),
              rig_rank(c, {"ratio": r}))
             for c, r in CASES
             if server_rank({"verify_confidence": c, "ratio": r})
             != rig_rank(c, {"ratio": r})]
    check("the rig and the server score every case the same", _diff, [])

    # ---- and ORDER them the same, which is what actually decides ---
    _s = sorted(CASES, key=lambda cr: server_rank(
        {"verify_confidence": cr[0], "ratio": cr[1]}), reverse=True)
    _r = sorted(CASES, key=lambda cr: rig_rank(cr[0], {"ratio": cr[1]}),
                reverse=True)
    check("  and rank them in the same order", _s, _r)

    # ---- the judge outranks the microphone -------------------------
    check("a confident swing beats a louder unconvincing one",
          rig_rank("high", {"ratio": 5.0})
          > rig_rank("low", {"ratio": 99.0}), True)
    check("  on the server too",
          server_rank({"verify_confidence": "high", "ratio": 5.0})
          > server_rank({"verify_confidence": "low", "ratio": 99.0}), True)

    # ---- THE CHANGE: a tie now goes to the louder one --------------
    # The operator's own case: two mediums, x18.8 at 36.54s against
    # x40.8 at 43.07s. The rig used to keep the first.
    check("within a band the louder candidate wins",
          rig_rank("medium", {"ratio": 40.8})
          > rig_rank("medium", {"ratio": 18.8}), True)
    check("  which is the server's answer as well",
          server_rank({"verify_confidence": "medium", "ratio": 40.8})
          > server_rank({"verify_confidence": "medium", "ratio": 18.8}),
          True)
    # AND AN EXACT TIE STILL KEEPS THE FIRST. `>` is what the hold
    # tests, so equal scores leave the leader in place -- which is the
    # original follow-through argument, surviving where nothing
    # distinguishes the two.
    check("an exact tie does not displace the leader",
          rig_rank("medium", {"ratio": 20.0})
          > rig_rank("medium", {"ratio": 20.0}), False)

    # ---- nothing is read before the judge has spoken ---------------
    # The ratio must never be able to promote a candidate past a
    # better-judged one, which is the failure min_separation_sec had.
    _loudest_low = rig_rank("low", {"ratio": 10_000.0})
    check("no ratio promotes a low-confidence candidate over a medium",
          _loudest_low < rig_rank("medium", {"ratio": 0.0}), True)
    check("  or an unjudged one over a low",
          rig_rank(None, {"ratio": 10_000.0})
          < rig_rank("low", {"ratio": 0.0}), True)

    # ---- the junk each side can really be handed -------------------
    check("a missing ratio scores as zero, not as an error",
          rig_rank("high", {}), rig_rank("high", {"ratio": 0.0}))
    check("  a missing detail dict too", rig_rank("high", None),
          rig_rank("high", {"ratio": 0.0}))
    check("  and an unparseable one", rig_rank("high", {"ratio": "x"}),
          rig_rank("high", {"ratio": 0.0}))
    check("case and whitespace do not change a band",
          rig_rank(" HIGH ", {"ratio": 1.0}), rig_rank("high", {"ratio": 1.0}))

    # ---- the hold reads the tuple, not the old int -----------------
    # A mutation that left `rank` as _confidence_rank would pass every
    # check above and change nothing at all on the rig.
    eng = (ROOT / "pi-agent" / "agent" / "copyengine.py").read_text()
    check("the held candidate is ranked by the tuple",
          '"rank": _swing_strength(conf, detail),' in eng, True)
    check("  and the old confidence-only ranking is not what it holds",
          '"rank": _confidence_rank(conf)' in eng, False)
    check("  the log says which number separated them",
          'cand["rank"][1]' in eng, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
