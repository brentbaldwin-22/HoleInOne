#!/usr/bin/env python3
"""The pick-best window belongs to the GOLFER, not to the cut queue.

THE FIELD FAILURE. A rig heard eleven strikes across a morning and the
production page showed almost nothing. The journal said the ears were
fine -- strikes at x4.0 through x60.5 against a x4.0 gate -- and said
`cutting ...` eleven times, so the cuts were being made.

Nine of those cuts came off the queue between 10:46:45 and 10:46:53:
eight seconds of wall clock carrying impacts that spanned 10:44:38 to
10:46:43, a two-minute backlog flushing at thirty times real time.

`_hold_candidate` dated its window `time.time() + hold_sec` AT THE
MOMENT THE CUT WAS PROCESSED. So all nine landed inside one 12s
window: four were allowed in, `_PICK_BEST_MAX_ENTRANTS` shipped the
leader, and the rest were parked as "rehearsals" for a swing they had
nothing to do with. Five separate swings became one event, and every
log line about it was true and reassuring.

THE FIX. Which candidates contest is a question about the golfer, so
it is asked in IMPACT time. When to ship stays in wall time, because
shipping happens in the world -- and it needs no backlog arithmetic,
because a cut whose impact is past the window ends that window on the
spot.

    python3 tools/pick_best_window_test.py
"""
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


class _Client:
    def event_trigger(self, session_id, **kw):
        return {}


class _Uploader:
    def __init__(self):
        self.sent = []

    def enqueue(self, session_id, clip_path, start, **kw):
        self.sent.append(session_id)


class _Runner:
    """Just the attributes `_ship_impact` reaches for."""

    def __init__(self):
        self.client = _Client()
        self.uploader = _Uploader()
        self._audio_trigger = None


def _engine(tmp):
    """A CopyEngine with only the fields this contest touches.

    Built without __init__ deliberately: that one stands up a
    SegmentRing against a camera, and the window logic has nothing to
    do with either.
    """
    from agent.copyengine import CopyEngine
    e = object.__new__(CopyEngine)
    e._held = None
    e._held_lock = threading.Lock()
    e.clips_dir = Path(tmp)
    return e


def _run(tmp, cuts, hold_sec=12.0):
    """Feed cuts through the real `_hold_candidate`, in order.

    Each cut is (session_id, impact_ts, confidence, ratio). Returns
    (events in the order they shipped, sessions parked).
    """
    from agent import copyengine as ce
    e, r = _engine(tmp), _Runner()
    parked = []
    e._park = lambda cand: parked.append(cand["session_id"])
    for sid, impact, conf, ratio in cuts:
        clip = Path(tmp) / ("%s.mp4" % sid)
        clip.write_bytes(b"x")
        e._hold_candidate(
            r, sid, clip, impact, 0.0, {"ratio": ratio},
            {"start": 0.0, "seconds": 13.0, "bytes": 6e6},
            conf, hold_sec)
    # The queue's worker closes a window that never got a successor.
    # Far enough ahead that the wall deadline has certainly passed.
    import time as _t
    e.flush_pick_best(r, now=_t.time() + 10_000)
    return r.uploader.sent, parked


def main():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # ── THE REPORTED BUG ────────────────────────────────────────
        #
        # Two swings 70s apart at the tee, processed back to back off
        # a backlogged queue. They are not candidates for each other
        # and must not be ranked against each other.
        _sent, _parked = _run(tmp, [
            ("A", 1000.0, "HIGH", 8.0),
            ("B", 1070.0, "HIGH", 8.0),
        ])
        ok("two swings 70s apart are two events", _sent, ["A", "B"])
        ok("  and neither is parked as the other's rehearsal", _parked, [])

        # The whole backlog, as it actually arrived: nine cuts, five
        # swings, impacts spanning two minutes.
        _sent, _parked = _run(tmp, [
            ("s1", 1000.0, "MEDIUM", 5.0),   # waggle
            ("s1b", 1004.0, "HIGH", 9.0),    #   then the strike
            ("s2", 1040.0, "HIGH", 7.0),
            ("s3", 1075.0, "MEDIUM", 4.0),
            ("s3b", 1079.0, "HIGH", 11.0),
            ("s4", 1105.0, "HIGH", 6.0),
            ("s5", 1120.0, "HIGH", 9.0),
        ])
        ok("a two-minute backlog ships one event per swing",
           _sent, ["s1b", "s2", "s3b", "s4", "s5"])
        ok("  parking only the rehearsals", sorted(_parked), ["s1", "s3"])

        # ── A WAGGLE AND A STRIKE STILL CONTEST ─────────────────────
        #
        # The point of the window in the first place: event 1492, a
        # waggle judged MEDIUM and the real strike judged HIGH.
        _sent, _parked = _run(tmp, [
            ("waggle", 1000.0, "MEDIUM", 5.0),
            ("strike", 1005.0, "HIGH", 9.0),
        ])
        ok("a waggle and a strike 5s apart are one event",
           _sent, ["strike"])
        ok("  and the waggle is parked", _parked, ["waggle"])

        # And where the judge cannot separate them, the louder wins.
        _sent, _parked = _run(tmp, [
            ("quiet", 1000.0, "HIGH", 4.0),
            ("loud", 1003.0, "HIGH", 12.0),
        ])
        ok("between equal verdicts the louder strike wins",
           _sent, ["loud"])

        # ── A LATE CHALLENGER CANNOT EXTEND THE WINDOW ──────────────
        #
        # The window is dated from the FIRST impact in it, so it does
        # not creep forward each time a new candidate is let in.
        _sent, _parked = _run(tmp, [
            ("a", 1000.0, "HIGH", 5.0),      # window runs to 1012
            ("b", 1010.0, "HIGH", 6.0),      #   inside it
            ("c", 1015.0, "HIGH", 7.0),      #   past it: a new swing
        ])
        ok("the window is dated from its first impact, not its leader",
           _sent, ["b", "c"])
        ok("  and the one it beat is parked", _parked, ["a"])

        # ── MAX ENTRANTS STILL CLOSES A WINDOW ──────────────────────
        #
        # Unchanged: a genuine burst inside one window is still cut
        # off, so a noise nobody can explain cannot hold the link.
        _sent, _parked = _run(tmp, [
            ("e1", 1000.0, "HIGH", 5.0),
            ("e2", 1001.0, "HIGH", 5.0),
            ("e3", 1002.0, "HIGH", 5.0),
            ("e4", 1003.0, "HIGH", 5.0),
            ("e5", 1004.0, "HIGH", 5.0),
        ])
        ok("five candidates in one real window still close it",
           _sent, ["e1"])
        ok("  and the rest are parked", sorted(_parked),
           ["e2", "e3", "e4", "e5"])

        # ── AND NOTHING SHIPS TWICE ─────────────────────────────────
        _sent, _parked = _run(tmp, [("only", 1000.0, "HIGH", 5.0)])
        ok("a lone swing ships exactly once", _sent, ["only"])

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
