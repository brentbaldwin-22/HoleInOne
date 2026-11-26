#!/usr/bin/env python3
"""Phase B actually runs, and the comet actually carries it.

THE BUG THIS GUARDS IS ONE THIS CODEBASE HAS ALREADY HAD. The ball on
the green and the reverse walk sat below an `if not _events: return
out` that fired on every single run, so three rounds of work on them
changed nothing visible and the operator kept reporting the same empty
boxes. See green_reached_test. A forward flight search wired in below
a condition that is never true would fail in exactly the same way:
silently, with every test still green, and with the only symptom being
a comet that starts at the bounce.

A SOURCE-ORDER TEST, because the failure is about statement order and
nothing about behaviour -- anything that reads like a test of the
flight's logic passes throughout (greencrawl_test does, and did while
nothing called it).

    python3 tools/flight_reached_test.py
"""
import inspect
import re
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
    from app.routers.admin import _ascent_descents

    src = inspect.getsource(_ascent_descents)
    lines = src.split("\n")

    def first(pat):
        for i, ln in enumerate(lines):
            if re.search(pat, ln):
                return i
        return None

    # ── THE CRAWL IS GONE FROM THE PRODUCE ──────────────────────────
    #
    # It was the method here, with the chain search as a rung beneath
    # it, on the evidence that the chain search did not work on real
    # footage: 383 chains over the four seconds before the pitch mark
    # and none taken, nearest arc 5px away.
    #
    # THAT WAS THE ARRIVAL TEST, not the chains. `find_flight_to`
    # asks whose arc LANDS ON a pixel at a frame, and anchored on a
    # pitch mark that is itself derived from a chain containing no
    # fall, almost nothing can pass. The question asked now is the one
    # an operator asks of the wizard's own scan picture -- which line
    # of dots is AIMED at the ball lying on the green -- and that is a
    # direction, measured against a measurement.
    #
    # The crawl had no motion model at all, which is how it crossed
    # the bounce and also how it ran up the true fall and then turned
    # off it onto the treeline. A chain that bends away from the ball
    # simply stops being aimed at it, so the new search has no such
    # turn available to it.
    _call = first(r"_gcrawl\.find_chain_to_ball\(")
    ok("produce asks which chain points at the ball", _call is not None,
       True)
    ok("  and the crawl is not called here any more",
       first(r"_gcrawl\.crawl_flight_back\("), None)
    ok("  nor the arrival test", first(r"_gcrawl\.find_flight_to\("),
       None)
    ok("  nor the reverse walk", first(r"_gdsc\.track_descent_back\("),
       None)
    ok("  leaving one answer, from one search",
       src.count("_flight = _gcrawl.") , 1)

    # ── AND NOT BEHIND AN EARLY RETURN ──────────────────────────────
    #
    # Every `return out` above the call is a way for it never to run.
    _returns = [i for i, ln in enumerate(lines)
                if re.match(r"\s{4}return out\b", ln)]
    ok("  with no function-level early return before it",
       [i for i in _returns if i < _call], [])

    # ── THE CONTRACT: NO BALL ON THE GREEN, NO DESCENT ──────────────
    #
    # Unchanged, and the opposite of the search the operator scrapped:
    # that one hunted the green and attached whatever best survived
    # the gates, which is how a landing appeared on clips where the
    # shot missed entirely.
    ok("the chain is only searched where a ball was found at rest",
       'if not c.get("rest_spot") or c.get("impact_frame") is None:'
       in src, True)
    # AND NO ASCENT, NO DESCENT. The new first rung: a descent is the
    # second half of a sentence whose first half is "a ball left the
    # tee", and step 3 used to run in full -- 48% of a produce -- on a
    # clip where step 2 had just reported nothing leaving.
    ok("  and only where a ball was seen leaving the tee",
       "if not _swing_saw_an_ascent(rep, i, c):" in src, True)
    # ANCHORED ON THE BALL ITSELF, which is the measurement. The pitch
    # mark is an inference over a chain that may contain no fall, and
    # on the smeared fixture it was thirteen frames and ninety pixels
    # from the truth.
    ok("  anchored on the ball on the green",
       bool(re.search(r'find_chain_to_ball\(\s*\n\s*_gp, c\["rest_spot"\]',
                      src)), True)

    # ── AND THE COMET CARRIES IT ────────────────────────────────────
    #
    # The whole payoff. A flight found and then not drawn is the same
    # as a flight not found: the comet still starts at the bounce.
    _track = first(r'c\["green_track"\] = ')
    ok("the comet is built after the flight is found",
       _track is not None and _track > _call, True)
    ok("  and the chain's points are in it",
       '_fpts = list(_flight.get("points") or [])' in src, True)
    # MEASURED POINTS ONLY, on both halves. An extrapolated point is a
    # position nobody measured and a comet is a claim about where the
    # ball was.
    # ── ONE SOURCE, SO NOTHING HAS TO BE MERGED ─────────────────────
    #
    # The comet used to be the flight chain AND the reverse walk,
    # merged on frame number because the walk runs from the resting
    # place backwards PAST the pitch, so the pitch sat in the middle
    # of it -- joined any other way it produced duplicated frames and
    # a comet that went backwards in time.
    #
    # There is no walk now. The comet is the one chain that points at
    # the ball, in the order the ball flew, and the merge it needed is
    # gone with the second source.
    ok("the comet is the chain, in flight order",
       "for q in _fpts]" in src, True)
    ok("  with nothing to merge against",
       "_by_frame" in src, False)
    # AND IT STOPS WHERE THE DOTS STOP. The gap from the last dot to
    # the ball is the bounce and the roll; drawing through it is a
    # claim about footage nobody looked at, which is exactly what the
    # crawl did when it veered off the fall onto the treeline.
    ok("  and is not carried on to the ball itself",
       "the roll from there to the ball is not drawn"
       in (Path(__import__("app.services.greencrawl",
                           fromlist=["x"]).__file__).read_text()
           .replace("The roll", "the roll")), True)

    # ── IT IS REPORTED AS ITS OWN STEP ──────────────────────────────
    ok("the run counts the flights it found", 'out["n_flight"]' in src, True)
    # ONE SEARCH, ONE SUB-STAGE. 3c was the walk and 3e the flight
    # hunted forwards from its pitch mark; there is one pass now and
    # splitting its seconds across two rows would invent a boundary
    # that no longer exists.
    ok("  reported as one sub-stage, not two",
       '_sub("3c", "The chain that points at the ball' in src, True)
    ok("  with no separate flight stage left over",
       '_sub("3e"' in src, False)

    _adm = Path(__import__("app.routers.admin", fromlist=["x"]).__file__)
    _mod = _adm.read_text(encoding="utf-8")
    ok("the swing steps show the flight with its picture",
       '"name": "flight"' in _mod, True)
    ok("  and say why when there is none",
       '_st["name"] == "flight"' in _mod, True)

    _jsx = (_adm.resolve().parents[3] / "frontend" / "src" / "pages"
            / "AdminProduction.jsx").read_text(encoding="utf-8")
    ok("the production page renders the flight block",
       "walk.flight" in _jsx, True)
    # EVERY BLOCK AND THE CROP IT SEARCHED. The crops are the
    # argument: a crawl that stopped because the ball left the box
    # reads completely differently from one that stopped with the
    # ball sitting inside an empty box.
    ok("  with every block's crop, zoom and verdict",
       "fl.blocks" in _jsx and "what happened" in _jsx, True)
    ok("  and what the block that ended it refused",
       "b.rejects" in _jsx, True)
    ok("  with every chain it considered",
       "fl.chains" in _jsx and "setShowChains" in _jsx, True)

    # ── THE THREE FRAMES ARE STATED, NOT RECONSTRUCTED ──────────────
    #
    # ASKED DIRECTLY: which frame is the ball at rest, which is the
    # pitch mark, and which frames does each pass scan. All three
    # existed and none was stated -- they had to be pieced together
    # from a sentence, a block table and a constant.
    # THERE ARE THREE OF THEM AGAIN AND TWO ARE DIFFERENT. The walk
    # and the crawl each scanned their own ranges and both had to be
    # named; one search scans one window, so the list is the rest
    # frame, that window, and the pitch.
    ok("the report states the frames", '"frames": {' in src, True)
    for _k in ("rest", "searched", "pitch", "pitch_from"):
        ok("  including %s" % _k, '"%s"' % _k in src, True)
    ok("  and no walk or crawl ranges are still claimed",
       "crawl_blocks" in src or "walk_searched" in src, False)

    # ── AND THE REST FRAME IS THE ONE THE BALL WAS MEASURED IN ──────
    #
    # IT WAS NOT, UNDER A COMMENT SAYING IT WAS. The walk started at
    # impact + REST_PLATE_AFTER_SEC, a flat 14 seconds, while the ball
    # is found by walking GREEN_AFTER_LADDER_SEC = (8, 14, 20, 5) and
    # whichever rung answers is the frame it was seen in. On any clip
    # answering on rung 1, 3 or 4 the walk began six to fifteen
    # seconds from the measurement -- on a ball that may have been
    # marked, moved or putted out in between.
    ok("the search is bounded by the frame the ball was measured in",
       '_after = float(_win[1]) - _base' in src, True)
    ok("  and says which window that came from",
       'c["rest_frame_from"]' in src, True)

    _jsx2 = (_adm.resolve().parents[3] / "frontend" / "src" / "pages"
             / "AdminProduction.jsx").read_text(encoding="utf-8")
    ok("the page shows the frames", "The frames" in _jsx2, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
