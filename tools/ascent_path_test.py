#!/usr/bin/env python3
"""Dwell sweeps. Audio uses its impact. Nothing in between.

THE OPERATOR, 13 Nov: "i thought on dwell, we don't search audio and
just sweep for ascents (like we had a couple weeks ago before the
changes). so 2 completely different paths for dwell and audio
triggered clips."

4269bce (28 Oct) is the change meant: it added an audio pass so a
dwell clip's ascent search could be narrowed to the seconds around a
strike heard in the clip itself. The argument was that eighty-eight
of a dwell's ninety seconds are birds, carts and branches -- true --
and that "a missed impact only widens the search back to the whole
clip".

THAT LAST PART IS ONLY TRUE WHEN NOTHING IS HEARD AT ALL. When the
swing is missed and something else is heard, the search does not
widen: it narrows onto the WRONG two seconds, finds no ball there,
and reports a clip with no ball in it. Silently, and in the direction
that costs a swing.

And quiet swings are missed. Those gates are deliberately looser than
the rig's (ratio 4, attack 25ms) and a real strike still comes in
under them -- upload #1487 had nothing at all over x4.

So: an audio-triggered clip uses the impact the RIG heard, which is
exact, free and judged. A dwell clip sweeps. A manual upload keeps
the audio pass, having no rig impact to fall back on and being one
deliberate swing rather than ninety seconds of a hole.

    python3 tools/ascent_path_test.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    src = (ROOT / "backend/app/routers/admin.py").read_text()
    _fn = src[src.index("def _ascent_windows("):]
    _fn = _fn[:_fn.index("\ndef ", 10)]

    # ── THE GATE EXISTS, AND IS ON THE RIGHT PASS ───────────────────
    ok("a dwell clip is recognised", '_kind = _trigger_kind(db, row)' in _fn,
       True)
    ok("  and returns before any audio is read",
       0 < _fn.index('if _kind == "dwell"')
       < _fn.index("detect_swings_from_audio"), True)
    ok("  returning None, which means 'sweep the whole clip'",
       "return None" in _fn[_fn.index('if _kind == "dwell"'):
                            _fn.index("detect_swings_from_audio")], True)

    # ── BUT NOT ON THE RIG'S OWN IMPACT ─────────────────────────────
    #
    # Path 1 is not a search of the clip; it is what the rig heard and
    # the judge confirmed. Gating it would throw away the one exact
    # answer in the system.
    ok("the rig's own impact is read BEFORE the gate",
       0 < _fn.index("_rig_impact_sec(") < _fn.index('_kind = _trigger_kind'),
       True)

    # ── AND THE OPERATOR IS TOLD WHICH PATH RAN ─────────────────────
    #
    # "the whole clip" and "two seconds around 31.8s" are different
    # runs, and a narrowed search reads as a broken one unless it says
    # so. The modal shows `window_detail`.
    _note = _fn[_fn.index('if _kind == "dwell"'):
                _fn.index("detect_swings_from_audio")]
    ok("the dwell path says so on the line the modal shows",
       "_note(" in _note and "dwell clip" in _note, True)

    # ── THE THREE KINDS, THROUGH THE REAL FUNCTION ──────────────────
    #
    # `_trigger_kind` reads trigger_detail off the camera event, and
    # returns None for a manual upload -- a real answer, not a missing
    # one, which is what keeps the upload on the audio pass.
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.routers.admin import _trigger_kind
    except Exception as exc:  # noqa: BLE001
        print("     (skipped the live check: %s)" % exc)
        _trigger_kind = None

    if _trigger_kind is not None:
        class _Ev:
            def __init__(self, kind):
                self.trigger_detail = None if kind is None else {"kind": kind}

        class _Db:
            def __init__(self, ev):
                self._ev = ev

            def get(self, _cls, _id):
                return self._ev

        class _Row:
            def __init__(self, eid):
                self.camera_event_id = eid

        ok("an audio event reads as audio",
           _trigger_kind(_Db(_Ev("audio")), _Row(1)), "audio")
        ok("a dwell event reads as dwell",
           _trigger_kind(_Db(_Ev("dwell")), _Row(1)), "dwell")
        ok("a manual upload reads as neither",
           _trigger_kind(_Db(None), _Row(None)), None)
        ok("  and so does a rig too old to say",
           _trigger_kind(_Db(_Ev(None)), _Row(1)), None)
        # None must NOT be treated as dwell: that would take the audio
        # pass away from the one row type that has nothing else.
        ok("None is not dwell", _trigger_kind(_Db(None), _Row(None)) == "dwell",
           False)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
