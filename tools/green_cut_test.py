#!/usr/bin/env python3
"""The cut to the green comes before the ball does.

THE OPERATOR, on a produced clip: "we stick with the tee view way too
long. we go to green view when the ball hits the green. we should cut
to it a little before we pick up the descent chain/comet."

The rule was "the landing wins when there is one", on the argument
that the comet's first frame is merely where the ball became VISIBLE
and cutting there costs the tee tracer its descent. That is true, and
it buys the wrong thing: the cut then lands at the touchdown, the
first second or more of a plotted comet is never on screen, and the
green half opens with the ball already down.

So the green half now opens a lead before whichever comes FIRST. On a
shot seen well before it lands that is the comet, and the viewer
watches the ball arrive instead of finding that it has.

    python3 tools/green_cut_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def green_start(comet_sec, landing_sec, lead):
    """The rule, as admin.py now applies it."""
    cand = comet_sec if landing_sec is None else min(comet_sec, landing_sec)
    return max(0.0, cand - lead)


def main():
    src = (ROOT / "backend/app/routers/admin.py").read_text()

    # ── THE CONSTANT, AND THAT IT IS STILL THE LEAD ─────────────────
    ok("the lead is a named constant",
       "D3_LANDING_LEAD_SEC = 0.75" in src, True)
    LEAD = 0.75

    # ── THE REPORTED CLIP ───────────────────────────────────────────
    #
    # Swing 2 of #1666: the comet runs f2036-f2177 at 60fps, so the
    # ball is first seen at 33.9s on the green clock, and it is still
    # coming down for another two seconds after that.
    comet, landing = 33.93, 36.28
    ok("the cut now leads the comet, not the landing",
       round(green_start(comet, landing, LEAD), 2), round(comet - LEAD, 2))
    ok("  which is earlier than it used to be",
       green_start(comet, landing, LEAD) < landing - LEAD, True)
    print("     comet %.2fs, landing %.2fs -> cut at %.2fs (was %.2fs)"
          % (comet, landing, green_start(comet, landing, LEAD),
             landing - LEAD))

    # ── AND THE CASES THAT MUST NOT CHANGE ──────────────────────────
    #
    # Where the comet starts LATE -- the ball only picked up as it
    # bounces -- the landing is still the earlier of the two and still
    # answers. The reversal must not drag the cut later than it was.
    ok("a late comet leaves the landing in charge",
       round(green_start(37.5, 36.28, LEAD), 2), round(36.28 - LEAD, 2))
    ok("no landing marked still cuts on the comet",
       round(green_start(33.93, None, LEAD), 2), round(33.93 - LEAD, 2))
    ok("and the cut never goes negative",
       green_start(0.2, 0.3, LEAD), 0.0)

    # IT CAN ONLY EVER MOVE THE CUT EARLIER. That is the whole claim,
    # and the one thing that would make this a regression if it were
    # false -- a clip cut LATER than before shows less of the arrival,
    # which is what was being complained about.
    import random
    rng = random.Random(4)
    worse = 0
    for _ in range(2000):
        c = round(rng.uniform(0.0, 60.0), 2)
        l = round(rng.uniform(0.0, 60.0), 2)
        if green_start(c, l, LEAD) > max(0.0, l - LEAD) + 1e-9:
            worse += 1
    ok("over 2000 random pairs it never cuts later than the landing did",
       worse, 0)

    # ── THE CODE SAYS SO ────────────────────────────────────────────
    ok("the earlier of the two is taken",
       "_cand = _c0 if _land_tee is None else min(" in src, True)
    ok("  with the lead applied to it",
       "_g_start = max(0.0, _cand - D3_LANDING_LEAD_SEC)" in src, True)
    ok("  and the old rule's comment is gone, not just overruled",
       "THE LANDING WINS WHEN THERE IS ONE" in src, False)
    ok("  while the log names which one answered",
       '"plotted comet"' in src and '"landing"' in src, True)

    # ── AND AN UNTIMED ARC SAYS SO ──────────────────────────────────
    #
    # The sharp apex reported on this same clip came with no log line
    # at all: every branch that REACHES a timing target logs it, and
    # the branches that do not, did not. "This tracer was never timed"
    # was signalled only by the ABSENCE of a line -- unreadable in a
    # log full of heartbeats, and indistinguishable from a produce
    # that failed earlier.
    #
    # It is not a neutral outcome. Untimed, the tail derives its own
    # duration and extrapolates a quadratic fitted to whatever was
    # measured; on a hand-plotted swing that is eight points spanning
    # a third of a second, run for three.
    ok("an arc with no timing target is logged",
       "has NO timing target" in src, True)
    ok("  naming the three numbers it would have used",
       ("descent-first=%s landing=%s delta=%s" in src), True)
    ok("  and the reason already recorded for the report",
       '_timing.get("why")' in src, True)
    ok("the timed case still logs too",
       "bridges the last ascent frame" in src, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
