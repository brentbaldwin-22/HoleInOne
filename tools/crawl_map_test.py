#!/usr/bin/env python3
"""The crawl's candidates reach the click-to-plot map.

WHY THE MAP AND NOT ANOTHER PICTURE. The crawl already draws its own:
numbered boxes for every crop, the chain it linked, the blocks that
came back empty. That picture cannot do the two things that matter
when the answer is wrong -- it cannot show what the crawl ALMOST took,
and it cannot let anybody click the ball it missed. The click-to-plot
map does both, and it is the view the operator checks everything else
against, so a second screen would be a second thing to keep in step.

THE DOTS ARE THE POINT, not the chain. Seeing the ball sitting among
the candidates the crawl refused is the one thing that separates "the
detector never saw it" from "the detector saw it and the linker would
not take it", and those need opposite fixes. So they come back in the
same {frame, x, y, area} shape the region scan returns and drop into
the same state the map already draws.

    python3 tools/crawl_map_test.py
"""
import inspect
import re
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
    from app.routers import admin as A
    from app.services import greencrawl as GC

    # ── THE SERVICE HANDS BACK ITS CANDIDATES ───────────────────────
    #
    # Off by default: produce does not need them and a treeline makes
    # them large -- 748 in a single block on the operator's own clip.
    _sig = inspect.signature(GC.crawl_flight_back).parameters
    ok("the crawl can return every candidate it weighed",
       "keep_candidates" in _sig, True)
    ok("  and does not by default",
       _sig["keep_candidates"].default, False)
    ok("  capped so a treeline cannot make the reply enormous",
       0 < GC.CRAWL_MAX_CANDIDATES <= 20000, True)

    # ── THE ENDPOINT EXISTS AND RUNS THE SAME SEARCH ────────────────
    ok("there is an endpoint for the map to call",
       hasattr(A, "crawl_green_descent"), True)
    _src = inspect.getsource(A.crawl_green_descent)
    ok("  running the crawl itself, not a copy of it",
       "gc.crawl_flight_back(" in _src, True)
    ok("  asking for the candidates",
       "keep_candidates=True" in _src, True)
    # BACKWARDS FROM A POINT IT IS TOLD. The same contract the crawl
    # has everywhere else: no anchor, no search. The old unanchored
    # descent search is what put landings on clips where the shot
    # missed the green.
    ok("  and refusing to run without an anchor",
       bool(re.search(r'if _af is None or not _xy:', _src)), True)

    # ── IN THE SHAPE THE MAP ALREADY DRAWS ──────────────────────────
    #
    # The region scan returns {frame, x, y, area}; anything else would
    # need a second code path in the map to draw it, and a second code
    # path is a second thing to keep in step.
    ok("the dots come back in the region scan's own shape",
       '"dots": out.get("candidates")' in _src, True)
    ok("  with the linked chain beside them",
       '"points": [' in _src, True)
    ok("  and the pitch mark it measured",
       '"pitch_frame": out.get("pitch_frame")' in _src, True)
    ok("  and which frames it searched",
       '"searched": out.get("searched")' in _src, True)

    # ── AND THE MAP CALLS IT ────────────────────────────────────────
    _api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    ok("the client has a call for it", "greenCrawl:" in _api, True)
    ok("  pointed at the right route", "/green-crawl" in _api, True)

    _jsx = (ROOT / "frontend" / "src" / "pages"
            / "AdminProduction.jsx").read_text(encoding="utf-8")
    ok("the map has a button for it", "Crawl descent" in _jsx, True)
    ok("  wired to a handler", "async function crawlDescent(" in _jsx, True)
    ok("  that calls the endpoint", "api.greenCrawl(" in _jsx, True)
    # THE CANDIDATES GO ON THE SAME STATE THE REGION SCAN FILLS, which
    # is what makes them clickable without touching the map's drawing
    # code at all.
    ok("  putting the dots where the region scan puts its own",
       bool(re.search(r'if \(out\?\.dots\?\.length\) setGreenDots\(out\.dots\)',
                      _jsx)), True)
    ok("  and the chain into the marks, to be corrected by hand",
       bool(re.search(r'setGreenMarks\(\(m\) => \{', _jsx)), True)

    # ── THE DOTS GO ON EVEN WHEN NOTHING LINKED ─────────────────────
    #
    # THE FAILING RUN IS THE ONE SOMEBODY IS LOOKING AT. A crawl that
    # came back empty with its dots on screen is readable: the ball is
    # either among them or it is not. A crawl that came back empty
    # with nothing on screen is just a sentence, which is what this
    # whole stage has been for weeks.
    _h = _jsx[_jsx.index("async function crawlDescent("):]
    _h = _h[:_h.index("async function findComet(")]
    ok("the dots are drawn before the chain is checked",
       _h.index("setGreenDots(") < _h.index("out?.points?.length"), True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
