#!/usr/bin/env python3
"""There is exactly ONE ascent search, and it is the whole-band sweep.

THE BUG, OR RATHER THE SHAPE OF FIVE OF THEM. Produce used to run a
second ascent search of its own, in stage 1: `find_ascents` narrowed
to a column round the measured ball, with the whole-band sweep
suppressed whenever that column "answered". The idea was sound -- a
known ball should not need a whole-frame search -- and every single
round of making the two agree produced the same user-visible symptom:
the produce report and the Find ascents dialog disagreed about the
same clip.

    round 1  the column ran at sensitivity 2 where the sweep runs 3
    round 2  it kept 30 detections a frame where the sweep keeps 40
    round 3  it three-frame differenced where the sweep had stopped
    round 4  the column was too narrow for a flight that drifts
    round 5  widening it re-enabled the 120px start gate it had been
             exempt from, and it back-ran the chain LINEARLY to the
             measured impact frame -- 31 frames of extrapolation,
             overshooting the ball by 1361px, so 66 chains were
             considered and NONE taken on a clip where the sweep
             found the flight as a clean 44-point chain

So the column search is gone. The sweep is the one and only ascent
search, the picture in stage 2 and the picture in step 2 of the swing
steps are the SAME picture, and the measured ball from stage 1 does
the one job it is reliably good at: saying WHICH of the sweep's chains
belongs to this swing.

A SOURCE TEST, because the regression this guards against is somebody
reintroducing a second search, which no behavioural test would catch
until the two disagreed again on a real clip.
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
    from app.routers import admin as A

    src = inspect.getsource(A._ascent_run)

    # ── PRODUCE CALLS find_ascents NOWHERE ──────────────────────────
    #
    # The narrowed search was `find_ascents`. `sweep_ascents` is the
    # whole-band one. Produce may call the second and must not call
    # the first: a call to it from here IS the second search coming
    # back, whatever it is named locally.
    ok("produce never calls the narrowed find_ascents",
       bool(re.search(r"\bfind_ascents\s*\(", src)), False)
    ok("  and does call the whole-band sweep",
       bool(re.search(r"\bsweep_ascents\s*\(", src)), True)

    # ── AND NOTHING SUPPRESSES THE SWEEP ────────────────────────────
    #
    # The suppression was the actual damage: a clip whose column
    # search "answered" never ran the sweep, so the dialog and the
    # report were looking at different searches and there was no way
    # to compare them.
    for _dead in ("skipped_for_column", "_col_entry", "_col_weak",
                  "_COL_TRUSTED_POINTS", "_ascent_column_image"):
        ok("no trace of %s" % _dead, _dead in src, False)
    ok("the sweep is not behind a conditional on stage 1",
       bool(re.search(r"if\s+.*_col.*:", src)), False)

    # ── THE WHOLE MODULE IS CLEAN OF THE OLD HELPER ─────────────────
    _mod = Path(A.__file__).read_text(encoding="utf-8")
    ok("the column image helper is deleted outright",
       "_ascent_column_image" in _mod, False)
    ok("  and nothing reads a column result off the report",
       bool(re.search(r"ascent_column\"?\]?\s*\)?\.get\(", _mod)), False)

    # ── THE FRONTEND SHOWS THE SWEEP'S PICTURE, NOT A SECOND ONE ────
    #
    # Step 2 of the swing steps used to point at the column search's
    # own image, which is how the two could disagree on screen.
    _jsx = (Path(A.__file__).resolve().parents[3] / "frontend" / "src"
            / "pages" / "AdminProduction.jsx").read_text(encoding="utf-8")
    ok("the production page has no column-search block left",
       "ascent_column" in _jsx, False)

    # ── AND THE MEASURED BALL IS WHAT PICKS AMONG THE CHAINS ────────
    #
    # This is the whole replacement. Without it the sweep's first
    # chain wins by position in a list, which on a busy tee is
    # whichever ball happened to be detected first.
    ok("the measured ball orders the sweep's chains",
       "ordered_by_measured_ball" in src, True)
    ok("  by distance from its own traced-back tee spot",
       bool(re.search(r"from_x.*_mb\[0\]", src, re.S)), True)

    # ── STAGE 1 NO LONGER CLAIMS AN ASCENT ──────────────────────────
    _i_stage1 = src.index('_stage(1,')
    _i_stage2 = src.index('_stage(2,')
    _s1 = src[_i_stage1:_i_stage2]
    ok("stage 1's title stops at the impact frame",
       "The golfer, the ball and the impact frame" in _s1, True)
    ok("  and it counts balls measured, not ascent points",
       "ascent point(s)" in _s1, False)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
