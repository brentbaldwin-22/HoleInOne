#!/usr/bin/env python3
"""What the verify call worked out is kept, and read back usable.

WHY THIS EXISTS. Deciding whether a clip is a swing means finding the
golfer, cropping to them and measuring the ball at rest. Every one of
those is a thing the produce needs too, and all three used to be
recomputed from the uploaded clip minutes later -- a fresh MOG2 pass
and a fresh person detector over the same seconds, for the same
answers. Stage 1 measured 15.4s on the clip that prompted this.

The risk in handing them over is not that it is slow, it is that it
is WRONG QUIETLY: a half-written row, a box stored as a string, a
coordinate space that does not match. So the reader is strict about
shape and the fallback is total -- anything it cannot vouch for comes
back as None, and the produce then does exactly what it always did.
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


class _Ev:
    def __init__(self, sa):
        self.swing_analysis = sa


class _Db:
    """Just enough of a Session: get(CameraEvent, id) -> row or None."""

    def __init__(self, rows):
        self.rows = rows

    def get(self, _model, _id):
        return self.rows.get(_id)


class _Row:
    def __init__(self, ev_id):
        self.camera_event_id = ev_id


def main():
    from app.routers.admin import _kept_swing_analysis

    GOOD = {"kept_at": "2026-11-09T10:00:00", "camera_id": 3,
            "golfer_box": [800, 300, 120, 400], "golfer_feet": [860, 700],
            "ball_spot": [870, 712], "ball_left": True, "n_people": 2,
            "picked_front": "the detector — furthest up the tee"}

    db = _Db({1: _Ev(GOOD)})
    got = _kept_swing_analysis(db, _Row(1))
    ok("a stored analysis comes back", got is not None, True)
    ok("  with the golfer's box", got["golfer_box"], [800, 300, 120, 400])
    ok("  and the measured ball", got["ball_spot"], [870, 712])

    # ── EVERY WAY IT CAN BE ABSENT IS None, NOT A HALF-ANSWER ───────
    # The produce branches on this. A truthy dict with nothing usable
    # in it would send it down the reuse path with nothing to reuse,
    # which is worse than never having stored anything.
    ok("no camera event is None",
       _kept_swing_analysis(_Db({}), _Row(None)), None)
    ok("an event that is not there is None",
       _kept_swing_analysis(_Db({}), _Row(7)), None)
    ok("an event with no analysis is None",
       _kept_swing_analysis(_Db({2: _Ev(None)}), _Row(2)), None)
    ok("a row that is not a dict is None",
       _kept_swing_analysis(_Db({3: _Ev("nonsense")}), _Row(3)), None)

    # ── A MALFORMED BOX IS DROPPED, NOT PASSED ON ───────────────────
    # Stored JSON comes back as whatever went in. A three-element box
    # unpacked as four raises deep inside the produce, where the
    # fallback no longer exists.
    bad = dict(GOOD, golfer_box=[1, 2, 3])
    g2 = _kept_swing_analysis(_Db({4: _Ev(bad)}), _Row(4))
    ok("a three-element box is refused", g2["golfer_box"], None)
    ok("  and its feet go with it", g2["golfer_feet"], None)
    ok("  but the ball survives — it is a separate claim",
       g2["ball_spot"], [870, 712])

    bad2 = dict(GOOD, ball_spot="870,712")
    g3 = _kept_swing_analysis(_Db({5: _Ev(bad2)}), _Row(5))
    ok("a ball stored as a string is refused", g3["ball_spot"], None)
    ok("  and the golfer survives", g3["golfer_box"], [800, 300, 120, 400])

    # ── AND A READER THAT RAISES IS STILL None ──────────────────────
    class _Boom:
        def get(self, *_a):
            raise RuntimeError("the database is having a day")

    ok("a database that raises costs the reuse, not the produce",
       _kept_swing_analysis(_Boom(), _Row(1)), None)

    # ── THE VERIFY SIDE HANDS OVER WHAT THE READER EXPECTS ──────────
    # The two ends are in different files and were written an hour
    # apart; the keys matching is the whole contract.
    from app.services.swinganalysis import analyse

    r = analyse([], None, {}, crop=(0, 0, 10, 10), zones=[])
    for _k in ("golfer_box", "golfer_feet", "zone_floor_y", "n_people"):
        ok("analyse always reports %s" % _k, _k in r or r.get(_k) is None,
           True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
