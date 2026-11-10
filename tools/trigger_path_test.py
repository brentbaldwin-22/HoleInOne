#!/usr/bin/env python3
"""Dwell clips get the old descent search; audio clips get the new one.

TWO MECHANISMS FIRE THESE CLIPS AND THEY GIVE THE DESCENT COMPLETELY
DIFFERENT GROUND TO STAND ON.

An AUDIO trigger means the rig HEARD the strike, so the impact frame
is a measured instant -- and everything the new path does hangs off
it: the green window ladder is impact+8/14/20/5, the frame the ball is
measured at rest comes off whichever rung answered, the walk starts
there, the crawl works back from where the walk last saw the ball.
Anchors on a measurement, all the way down.

A DWELL trigger means somebody stood in a zone. Nothing heard
anything. The impact frame still exists but it is FITTED -- the ascent
chain run back through a deceleration to where it crosses the tee line
-- and admin.py's own note on the audio override says that fit "lands
early or late by however much the fit is wrong... on #1265 the
equivalent fit in the produce path landed eleven frames after the
strike". Anchor the ladder, the rest frame, the walk and the crawl on
that and a dozen frames of error moves every one of them.

The older search does not anchor on the impact frame at all: it
sweeps the flight windows for anything falling and matches what it
finds. That is exactly the imprecision it was scrapped for on audio
clips, and exactly what makes it the right tool where the anchor is
itself a guess.

    python3 tools/trigger_path_test.py
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


class _Ev:
    def __init__(self, detail):
        self.trigger_detail = detail


class _DB:
    def __init__(self, ev):
        self._ev = ev

    def get(self, _model, _pk):
        return self._ev


class _Row:
    camera_event_id = 7


def main():
    from app.routers.admin import _trigger_kind, _ascent_descents

    # ── THE KIND IS READ OFF THE CAMERA EVENT ───────────────────────
    ok("an audio trigger reads as audio",
       _trigger_kind(_DB(_Ev({"kind": "audio"})), _Row()), "audio")
    ok("a dwell trigger reads as dwell",
       _trigger_kind(_DB(_Ev({"kind": "dwell"})), _Row()), "dwell")
    ok("  case and padding do not matter",
       _trigger_kind(_DB(_Ev({"kind": " Dwell "})), _Row()), "dwell")

    # ── AND AN UNKNOWN ONE IS None, NOT A GUESS ─────────────────────
    #
    # A manual upload and a rig too old to describe itself both land
    # here. Neither is a reason to change what already runs, and
    # guessing "audio" would quietly give them anchors they have not
    # earned.
    ok("no detail at all is None",
       _trigger_kind(_DB(_Ev(None)), _Row()), None)
    ok("  a detail with no kind is None",
       _trigger_kind(_DB(_Ev({"zone": "tee"})), _Row()), None)
    ok("  no camera event is None",
       _trigger_kind(_DB(_Ev({"kind": "audio"})), type("R", (), {
           "camera_event_id": None})()), None)
    # NEVER RAISES. The descent is not worth a clip, and a lookup that
    # throws on a detached session must not cost one.
    class _Boom:
        def get(self, *_a):
            raise RuntimeError("detached")
    ok("  and a database that throws is None",
       _trigger_kind(_Boom(), _Row()), None)

    src = inspect.getsource(_ascent_descents)

    # ── THE SWITCH ──────────────────────────────────────────────────
    ok("the run picks its path from the trigger kind",
       "_kind = _trigger_kind(db, row)" in src, True)
    ok("  the old search is for dwell", '_old_way = (_kind == "dwell")' in src,
       True)
    ok("  and it is what turns the old search on",
       "_do_descent = _old_way" in src, True)

    # ── ONE PATH PER CLIP, NEVER BOTH ───────────────────────────────
    #
    # Running both and picking would be two answers that can disagree
    # about the same ball, which is the failure this project has paid
    # for over and over: the two ascent searches, the button and the
    # produce on the green window, the two ball pickers.
    ok("a dwell clip skips the walk and the crawl entirely",
       "for i, c in enumerate(_clips if not _old_way else []):" in src, True)

    # ── AND THE REPORT SAYS WHICH RAN AND WHY ───────────────────────
    #
    # A run that silently took a different path is the same problem as
    # a stage that reports a sentence: nobody can check it.
    ok("the report names the trigger kind",
       'out["trigger_kind"] = _kind' in src, True)
    ok("  and the method it chose",
       bool(re.search(r'out\["descent_method"\] = "search" if _old_way',
                      src)), True)
    ok("  and why it chose it",
       'out["descent_method_why"]' in src, True)

    # ── THE GREEN REST SEARCH RUNS FOR BOTH ─────────────────────────
    #
    # Only the DESCENT method differs. Where the ball came to rest is
    # worth having either way, and it is the gate the new path needs
    # and the old path's matcher is glad of.
    _i_rest = src.index('_sub("4b"')
    _i_walk = src.index("if not _old_way else []")
    ok("where it came to rest is still found on a dwell clip",
       _i_rest < _i_walk, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
