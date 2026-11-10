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
flight's logic passes throughout (greenflight_test does, and did while
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

    _call = first(r"_gfly\.crawl_flight_back\(")
    ok("produce calls the crawl", _call is not None, True)
    # ── THE CRAWL IS THE METHOD, THE CHAINS ARE A FALLBACK RUNG ─────
    #
    # The chain search does not work on real footage: on the
    # operator's own clip it considered 383 chains over the four
    # seconds before the pitch mark and took none, nearest arc 5px
    # away. It stays as a rung beneath the crawl, not as a second
    # opinion running beside it -- one answer is produced, by
    # whichever ran. Two searches reporting separately is what cost
    # five rounds on the tee.
    _alt = first(r"_gfly\.find_flight_to\(")
    ok("  with the chain search only beneath it",
       _alt is not None and _alt > _call, True)
    ok("  and only when the crawl found nothing",
       'if not _flight.get("ok"):' in src, True)
    ok("  producing one answer, not two",
       "_flight = _alt" in src, True)

    # ── AND NOT BEHIND AN EARLY RETURN ──────────────────────────────
    #
    # Every `return out` above the call is a way for it never to run.
    # The walk it depends on is above it, which is correct -- the
    # flight is anchored on the walk's pitch mark -- so what matters is
    # that nothing between the two can bail out.
    _walk = first(r"_gdsc\.track_descent_back\(")
    ok("  after the walk that gives it the pitch mark",
       _walk is not None and _call > _walk, True)
    _returns = [i for i, ln in enumerate(lines)
                if re.match(r"\s{4}return out\b", ln)]
    ok("  with no function-level early return before it",
       [i for i in _returns if i < _call], [])

    # ── THE CONTRACT: NO PITCH MARK, NO FLIGHT ──────────────────────
    #
    # The same contract the walk has, and the opposite of the search
    # the operator scrapped: that one hunted the green and attached
    # whatever best survived the gates, which is how a landing appeared
    # on clips where the shot missed entirely.
    ok("the flight is only searched where the walk saw the ball",
       bool(re.search(r'_seen = \[q for q in \(_walk\.get\("points"\)',
                      src)) and "if _anchor:" in src, True)
    # ── ANCHORED ON A MEASUREMENT, NOT ON AN INFERENCE ──────────────
    #
    # The obvious thing to hand over is the walk's PITCH MARK, and it
    # is the wrong thing: that mark is derived from a chain which,
    # whenever the walk gave up at the bounce, does not contain the
    # fall at all. On the smeared fixture it came out thirteen frames
    # and ninety pixels from the truth, and anchored on it the search
    # returned eight points of roll instead of fifty-two of flight.
    ok("  anchored on the earliest frame it was really seen",
       "_anchor = _seen[0] if _seen else None" in src, True)
    ok("  and not on the computed pitch mark",
       bool(re.search(r'find_flight_to\(\s*\n\s*_gp, _walk\["pitch_xy"\]',
                      src)), False)

    # ── AND THE COMET CARRIES IT ────────────────────────────────────
    #
    # The whole payoff. A flight found and then not drawn is the same
    # as a flight not found: the comet still starts at the bounce.
    _track = first(r'c\["green_track"\] = ')
    ok("the comet is built after the flight is found",
       _track is not None and _track > _call, True)
    ok("  and the flight's points are in it",
       '_fpts = list(((_flight or {}).get("points") or []))' in src, True)
    # MEASURED POINTS ONLY, on both halves. An extrapolated point is a
    # position nobody measured and a comet is a claim about where the
    # ball was.
    ok("  measured points only, from the walk",
       'if q.get("found"):' in src, True)
    # ── MERGED ON FRAME NUMBER, NOT CONCATENATED ────────────────────
    #
    # I joined these by cutting the flight at `landing_frame` and
    # appending the whole walk, assuming the walk BEGINS at the pitch
    # mark. It does not: the walk runs from the resting place
    # backwards PAST the pitch to wherever it gave up, so the pitch
    # sits in the middle of it. That produced seven duplicated frames
    # and a comet that went backwards in time.
    ok("  merged on frame number so the two cannot overlap",
       "_by_frame = {" in src and "sorted(_by_frame.items())" in src, True)
    # THE WALK WINS A SHARED FRAME. Both are measurements, but the
    # walk tracked a known ball through a moving window while the
    # flight chain is detections a linker joined up.
    _fi = src.index("_by_frame = {")
    _wi = src.index('_by_frame[int(q["frame"])] = ', _fi)
    ok("  with the walk overwriting the flight where they share one",
       _wi > _fi, True)

    # ── IT IS REPORTED AS ITS OWN STEP ──────────────────────────────
    ok("the run counts the flights it found", 'out["n_flight"]' in src, True)
    ok("  and reports it as its own sub-stage",
       '_sub("4e"' in src, True)

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

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
