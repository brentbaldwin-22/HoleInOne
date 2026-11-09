#!/usr/bin/env python3
"""The button and the produce look at the SAME frames.

THE BUG THIS LOCKS OUT. `Find ball on green` used 2.0s and 13.0s;
the produce used impact-6 and impact+14. Eleven seconds apart against
twenty, anchored differently, on the same clip -- so the button found
balls the produce did not, and an operator checking one against the
other was checking a different question. The whole value of having
the button is that it answers what the produce answers.

AND WHY IT IS A LADDER, NOT A NUMBER. The one failure this has, over
and over, is an after frame that is TOO LATE: the group reaches the
green, somebody marks the ball, and the picture shows a green with
nothing new on it -- which reads exactly like a shot that missed. One
constant cannot be right for a 230-yard par 3 and a 110-yard one, and
picking one by fitting it to a clip is how three other constants in
this file came to be provisional. So a few are tried, soonest first,
and the first that finds a ball wins.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    from app.routers.admin import (
        GREEN_AFTER_LADDER_SEC, GREEN_BEFORE_SEC, _green_windows)

    w = _green_windows(30.0)
    ok("a strike gives one window per rung",
       len(w), len(GREEN_AFTER_LADDER_SEC))
    ok("  every before frame is the same, and before the strike",
       {round(b, 2) for b, _ in w}, {round(30.0 - GREEN_BEFORE_SEC, 2)})
    ok("  and every after frame is after it",
       all(a > 30.0 for _, a in w), True)
    # SOONEST FIRST. A later window is not a better look at the same
    # green, it is a look at a green more things have had time to
    # happen on -- the group walking up, the shade moving, the ball
    # being marked.
    ok("the first rung is the soonest after settling",
       w[0][1] - 30.0, min(GREEN_AFTER_LADDER_SEC[:3]))

    # ── A STRIKE EARLY IN THE CLIP DOES NOT ASK FOR A NEGATIVE ──────
    w2 = _green_windows(2.0)
    ok("an early strike clamps the before frame to the clip start",
       min(b for b, _ in w2) >= 0.0, True)

    # ── NO STRIKE FALLS BACK TO THE PAIR THE BUTTON ALWAYS USED ─────
    # Not to a guess at "a while after the strike": without a strike
    # there is nothing to be a while after.
    ok("no strike gives one window", len(_green_windows(None)), 1)
    ok("  and it is the operator's own 2 and 13",
       _green_windows(None)[0], (2.0, 13.0))

    # ── THE LADDER IS ORDERED, AND HAS NO DUPLICATES ────────────────
    # A repeated rung costs two frame grabs and a zoned pass for an
    # answer already known to be no.
    ok("no rung is repeated",
       len(set(GREEN_AFTER_LADDER_SEC)), len(GREEN_AFTER_LADDER_SEC))
    ok("every rung is after the strike",
       all(a > 0 for a in GREEN_AFTER_LADDER_SEC), True)
    ok("and the before frame really is before it",
       GREEN_BEFORE_SEC > 0, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
