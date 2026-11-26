#!/usr/bin/env python3
"""The green pass is not behind an early return.

THE BUG. The ball-on-the-green search and the reverse descent walk
sat below the descent MATCHING, after an `if not _events: return
out`. That made sense while the old whole-frame descent search ran
and produced chains to match. It was scrapped -- `_do_descent` is
False -- so `_events` is empty on EVERY run, the early return fires
every time, and everything below it never executed. No ball on the
green, no reverse walk, no before/after pictures, on any clip, ever.

Three rounds of work on the window ladder, the pictures and the
empty-box reasons changed nothing visible, because none of that code
was being reached. The operator kept reporting the same empty boxes
and I kept fixing things further down the function.

A SOURCE-ORDER TEST, because the bug was entirely about statement
order and nothing about behaviour. Anything that reads like a test of
the green's logic would have passed throughout.
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

    # Where the green work starts: the camera pair is looked up for it
    # and nothing else in this function wants it.
    _green = first(r"_t_id, _g_id = _camera_pair_for")
    ok("the green pass is in this function", _green is not None, True)

    # ── THE RULE, PRECISELY ─────────────────────────────────────────
    #
    # Four returns ABOVE the green pass are preconditions for it, not
    # obstacles to it: no swings, no green camera, the green video
    # missing on disk, the clip would not open. The green cannot run
    # in any of those, each sets a reason that now reaches the empty
    # box, and demanding the green come first would be demanding it
    # run without a clip.
    #
    # What must not happen is a return between the clip being OPEN
    # and the green being searched. That is where the dead one was.
    _opened = first(r'_sub\("3a", "Opening the green clip and reading')
    ok("the clip-open step is identifiable", _opened is not None, True)
    _returns = [i for i, ln in enumerate(lines)
                if ln.strip() == "return out"]
    ok("there are early returns in this function", len(_returns) > 1, True)
    _between = [i for i in _returns
                if _opened < i < len(lines) - 4 and i < _green]
    ok("nothing returns between opening the clip and searching the green",
       _between, [])
    ok("  and some return still comes after the green pass — otherwise "
       "this test is passing by proving nothing",
       any(i > _green for i in _returns), True)

    # The one that actually bit: "no ball seen coming down".
    _noball = first(r"no ball seen coming down")
    ok("the no-descent bail is still there", _noball is not None, True)
    ok("  and the green pass is above it", _green < _noball, True)

    # ...and so are both halves of it.
    _rest = first(r'_sub\("3b", "Finding where it came to rest"')
    # ── 3c IS NO LONGER A WALK ─────────────────────────────────────
    #
    # The reverse walk and the crawl behind it were half the runtime
    # of a produce and the crawl still turned off the fall onto the
    # treeline. 3c is now one pass that weighs every chain in the
    # flight window and keeps the one AIMED at the ball on the green.
    # What this test is about is unchanged and is the thing that
    # actually bit: whatever 3c is, it has to run before the
    # no-descent bail.
    _walk = first(r'_sub\("3c", "The chain that points at the ball')
    ok("the resting-ball search runs before that bail",
       _rest is not None and _rest < _noball, True)
    ok("the chain search runs before it too",
       _walk is not None and _walk < _noball, True)
    ok("  and the walk runs after the rest, which it needs",
       _rest < _walk, True)

    # ── AND IT DOES NOT DEPEND ON THE SEARCH HAVING FOUND ANYTHING ──
    #
    # THE ORIGINAL BUG IN ONE LINE. The green pass sat below an `if
    # not _events: return out`, and `_events` is whatever the old
    # whole-window descent search produced -- so once that search was
    # scrapped the condition was true on every run and the green pass
    # never executed at all.
    #
    # The old search is no longer unconditionally off: a DWELL clip
    # gets it, because a dwell heard nothing and the impact frame the
    # newer path anchors on is a fit rather than a measurement. So
    # `_events` can be non-empty again, and the thing this test is
    # really for -- that the green pass runs whatever the search did
    # or did not find -- is the ordering above, not the constant.
    #
    # AND THE CHOICE IS NO LONGER THE TRIGGER KIND. It was
    # `_do_descent = _old_way`, i.e. "search on a dwell, walk
    # otherwise" — which on a rig with the audio trigger off switched
    # the walk off on every clip there is. Then it became per SWING
    # and about evidence.
    #
    # IT IS NOW OFF ALTOGETHER IN THE PRODUCE. The unanchored search
    # hunts the green and attaches the best thing that survives the
    # gates, and on a swing with no ball found out there it has
    # nothing to be right about — so what it attaches is a landing for
    # a shot that may never have arrived. The descent is now a chain
    # of three measurements that stops where one is missing.
    #
    # What must stay true is unchanged and is the thing that bit: the
    # green pass — the before/after test that FINDS the ball — is
    # above every return that could gate it.
    _dod = first(r"_do_descent = False")
    ok("the old unanchored search no longer runs in the produce",
       _dod is not None, True)
    ok("  and says why rather than just vanishing",
       any("to be right about" in ln for ln in lines), True)
    ok("  and the green pass still runs before that point",
       _rest < _dod, True)
    ok("  and the green pass does not wait for it", _green < _dod, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
