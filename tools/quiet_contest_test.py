#!/usr/bin/env python3
"""Inside a quiet period, does the BEST swing win, or the earliest one?

WHAT THE TABLE SHOWED. Seven audio candidates. The one at 36.54s was
confirmed with an x18.8 ratio, and the one at 43.07s -- x40.8, the
loudest strike on the page -- was never looked at. No heat map, no
judge, one line: "12s quiet after the confirmed swing at 36.54s".
Rows 3, 4 and 6 all read like that.

That is the earlier candidate winning by being earlier, which is not a
property of a golf swing. The refractory exists because one strike
fires the audio detector more than once -- the echo, the club hitting
the ground, the ball landing -- and suppressing those is right. What
was wrong is deciding WHICH of the group to keep before looking at
any of them.

So every candidate is judged, and the quiet period became a contest:
best first, each winner claiming its own window.

    python3 tools/quiet_contest_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    resolve_swing_refractory as resolve,
)

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def cut(i, t, swing=True, conf="medium", ratio=10.0):
    return {"index": i, "peak_time_sec": t, "is_swing": swing,
            "verify_confidence": conf, "ratio": ratio}


def kept(cuts):
    return [c["index"] for c in cuts if c.get("kept")]


def main() -> int:
    # ---- the row off the operator's table ---------------------------
    # 36.54s confirmed at x18.8 against 43.07s at x40.8, 6.5s apart,
    # inside a 12s quiet period. Judged the same, the louder one wins.
    cs = [cut(5, 36.54, conf="medium", ratio=18.8),
          cut(6, 43.07, conf="medium", ratio=40.8)]
    n = resolve(cs, 12.0)
    check("one of the two is superseded", n, 1)
    check("  and it is the quieter one, not the later one", kept(cs), [6])
    check("  the loser keeps the judge's own answer",
          cs[0]["is_swing"], True)
    check("  and is told which one beat it", cs[0]["superseded_by"], 6)
    check("  in words that name both scores",
          "x40.8" in cs[0]["supersede_reason"]
          and "x18.8" in cs[0]["supersede_reason"], True)

    # ---- THE JUDGE OUTRANKS THE MICROPHONE --------------------------
    # A loud bang that does not look like a swing loses to a quieter
    # one that does. The ratio is loudness; the judge has looked at
    # four seconds of motion heat.
    cs = [cut(1, 10.0, conf="high", ratio=5.0),
          cut(2, 14.0, conf="low", ratio=99.0)]
    resolve(cs, 12.0)
    check("a confident swing beats a louder unconvincing one",
          kept(cs), [1])
    # ...and inside one confidence band the ratio is what is left.
    cs = [cut(1, 10.0, conf="high", ratio=5.0),
          cut(2, 14.0, conf="high", ratio=99.0)]
    resolve(cs, 12.0)
    check("  within a band, the louder one wins", kept(cs), [2])

    # ---- IT DOES NOT CHAIN ------------------------------------------
    # Walking in time order links 36.5 to 43.1 and 43.1 to 54.5, and
    # so swallows 54.5 into a group whose other end is eighteen
    # seconds away. Here the MIDDLE candidate is the weak one: the
    # winner claims its own 12s and 54.5, outside it, survives.
    cs = [cut(1, 36.5, conf="high", ratio=40.0),
          cut(2, 43.1, conf="medium", ratio=10.0),
          cut(3, 54.5, conf="medium", ratio=20.0)]
    n = resolve(cs, 12.0)
    check("a losing middle candidate does not link the two ends",
          (n, kept(cs)), (1, [1, 3]))
    check("  and the one that lost names the winner it clashed with",
          cs[1]["superseded_by"], 1)

    # ---- what it leaves alone ---------------------------------------
    cs = [cut(1, 10.0), cut(2, 40.0), cut(3, 70.0)]
    check("swings further apart than the period are all kept",
          (resolve(cs, 12.0), kept(cs)), (0, [1, 2, 3]))
    cs = [cut(1, 10.0), cut(2, 14.0)]
    check("a zero period suppresses nothing",
          (resolve(cs, 0.0), kept(cs)), (0, [1, 2]))
    cs = [cut(1, 10.0, swing=False), cut(2, 14.0, swing=True)]
    check("a refused candidate cannot supersede or be superseded",
          (resolve(cs, 12.0), kept(cs)), (0, [2]))
    cs = [cut(1, 10.0, swing=None), cut(2, 14.0, swing=None)]
    check("  nor can two undecided ones",
          (resolve(cs, 12.0), kept(cs)), (0, []))
    check("no candidates at all is not an error", resolve([], 12.0), 0)
    cs = [{"index": 1, "is_swing": True, "peak_time_sec": None},
          cut(2, 14.0)]
    check("a confirmed swing with no peak time is left out of the contest",
          (resolve(cs, 12.0), kept(cs)), (0, [2]))

    # ---- and nothing is skipped before being judged -----------------
    # The actual defect: the old gate ran BEFORE the heat check, so a
    # superseded candidate had no picture and no verdict to compare.
    adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
    # THE GATE ITSELF, not a sentence describing it: the docstring
    # above quotes the old table line, so searching for the words
    # would pass on a file where the gate was never removed.
    check("the quiet period no longer gates the heat check",
          "peak_time_sec < _quiet_until" in adm, False)
    check("  and the running quiet cursor is gone with it",
          "_quiet_until" in adm, False)
    check("  it resolves after every candidate is judged",
          "resolve_swing_refractory(cuts, float(verify_refractory_sec))"
          in adm, True)
    page = (ROOT / "frontend" / "src" / "pages"
            / "AdminLongUpload.jsx").read_text()
    check("the table tells a superseded swing from a refused one",
          "superseded" in page and "c.supersede_reason" in page, True)
    check("  and the header counts both", "n_superseded" in page, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
