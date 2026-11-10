#!/usr/bin/env python3
"""The flight is found forwards, anchored on the pitch mark.

PHASE B. The reverse walk is good at the roll and stops dead at the
bounce, every time, on every clip -- not for want of a better detector
but because a bounce is an IMPULSE that no smooth motion model crosses
in either direction. `greendescent_test` records that wall; this is
the way round it.

The flight is searched FORWARD and told where it ends. That turns a
hunt for an unknown object into one question -- which chain's own arc
lands on that pixel at that frame -- and everything that does not land
there is discarded without being judged on how straight it is or how
fast it fell. It is the same move that fixed the tee's ascent, and the
opposite of the unanchored green descent search the operator scrapped
as too inconsistent.

    python3 tools/greenflight_test.py
"""
import io
import sys
import tempfile
from pathlib import Path

import cv2  # noqa: F401  (the fixture builder needs it)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def _fixture():
    """greendescent_test's smeared green, reused rather than copied.

    ONE FIXTURE, FOR THE SAME REASON THERE IS ONE DETECTOR. Two
    fixtures of the same clip drift apart, and then the walk and the
    flight are tested against different footage and agree about
    nothing. This loads the builder out of the other file instead of
    growing a second copy of it.
    """
    src = io.open(ROOT / "tools" / "greendescent_test.py",
                  encoding="utf-8").read()
    ns = {"__name__": "fixture",
          "__file__": str(ROOT / "tools" / "greendescent_test.py")}
    exec(compile(src.split("def main(")[0], "fixture", "exec"), ns)
    return ns


def main():
    from app.services.greenflight import find_flight_to
    from app.services import greenflight as GF

    ns = _fixture()
    ENTER, LAND, REST, REST_FRAME = (ns["ENTER"], ns["LAND"], ns["REST"],
                                     ns["REST_FRAME"])
    truth = ns["truth"]
    pitch_f = ENTER + LAND
    pitch_xy = truth(LAND)

    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "green.mp4"
        ns["build_smeared"](clip)
        r = find_flight_to(clip, pitch_xy, pitch_f, out_dir=d, stem="t")

        ok("the flight is found", r["ok"], True)
        # IT REACHES THE FRAME THE BALL ENTERED. The reverse walk never
        # got within fifty frames of this: it stops at the bounce, and
        # the whole flight is on the far side of that.
        ok("  and reaches the frame the ball entered",
           r["first_frame"] <= ENTER + 2, True)
        ok("  with most of the flight measured, not carried",
           r["n_points"] >= 40, True)
        ok("  falling, a long way", r["fell_px"] > 500, True)
        ok("  and every point on the real arc", all(
            abs(q["x"] - truth(q["frame"] - ENTER)[0]) < 40
            and abs(q["y"] - truth(q["frame"] - ENTER)[1]) < 40
            for q in r["points"] if 0 <= q["frame"] - ENTER < LAND), True)

        # ── THE ANCHOR IS WHAT DOES THE WORK ────────────────────────
        #
        # Not a fall-rate band, not a drop fraction, not a straightness
        # bar set between two populations of five. Those were all
        # proxies for "is this the shot" and the pitch mark answers it
        # directly.
        _t = r["taken"]
        ok("the chain taken lands on the pitch mark",
           _t["miss_px"] <= GF.ANCHOR_PX + GF.ANCHOR_PX_PER_FRAME
           * _t["extrap_frames"], True)
        ok("  and fits its own parabola to well under a pixel",
           _t["resid_px"] < 1.5, True)
        ok("  curving the way gravity curves", _t["gravity"] > 0, True)

        # ── THE CHEAP DETECTOR ANSWERS FIRST ────────────────────────
        #
        # debug3's own measurement: diff is 2.5ms/frame against the
        # plate's 27.7, and the plate has no per-frame cap so on
        # treeline texture it returns hundreds of thousands of
        # detections and buries the ball in them. Escalating is a
        # fallback, not the normal path.
        ok("the cheap detector answers on the first rung", r["rung"], 1)
        ok("  and only one rung was paid for", len(r["ladder"]), 1)
        ok("  which was frame differencing", r["ladder"][0]["detectors"],
           ["diff"])

        # ── MEASUREMENT BEATS EXTRAPOLATION ─────────────────────────
        #
        # Ranking by miss alone took a 32-point chain that stopped a
        # third of the way down and was carried 26 frames, over one
        # measured the whole way, because the short one's arc landed
        # 15px out against the long one's 36. Carry is now charged at
        # exactly the rate the tolerance loosens for it.
        ok("the chain taken was carried only a little",
           _t["extrap_frames"] <= 10, True)
        _short = [q for q in r["chains"]
                  if q["ok"] and q["extrap_frames"] > _t["extrap_frames"]
                  and (q["miss_px"] or 1e9) < _t["miss_px"]]
        ok("  even where a shorter chain's arc landed nearer",
           all(q["score"] > _t["score"] for q in _short), True)

        # ── THE BOUNCE IS NOT FITTED ────────────────────────────────
        #
        # AFTER_PITCH_FRAMES exists so the LINKER can build a chain
        # whole, not so a parabola gets fitted through the first of the
        # bounce. Leaving those in moved the fit from 0.7px to 5.2 and
        # the landing by 20px, because the bounce goes back UP.
        ok("no point past the pitch mark is in the flight",
           max(q["frame"] for q in r["points"]) <= pitch_f, True)

        # ── EVERY CHAIN, WITH WHY ───────────────────────────────────
        #
        # The refused ones are what somebody reads when the answer is
        # wrong: a chain that missed by 60px is a tolerance question
        # and one that missed by 900 is a different object.
        ok("every chain considered is reported",
           len(r["chains"]) > 0, True)
        ok("  each with a reason",
           all(q.get("why") for q in r["chains"]), True)
        ok("  and the picture is drawn", bool(r["image"]), True)
        ok("  with counts beside it",
           set(("chains_considered", "landed_on_the_mark",
                "nearest_miss_px")) <= set(r["counts"]), True)

        # ── THE ANCHOR IS A MEASUREMENT, NOT AN INFERENCE ───────────
        #
        # THE OBVIOUS THING TO PASS IS THE WALK'S PITCH MARK AND IT IS
        # THE WRONG THING. That mark is derived -- "the first frame it
        # stopped falling" -- from a chain which, whenever the walk
        # gave up at the bounce, does not contain the fall at all, so
        # the rule names a point in the roll. The earliest frame the
        # walk actually SAW is a true position at a true frame.
        from app.services.greendescent import track_descent_back
        _w = track_descent_back(clip, REST, REST_FRAME)
        _seen = [q for q in _w["points"] if q.get("found")]
        _anch = _seen[0]
        ok("the walk's pitch mark is well off the real one",
           abs(_w["pitch_frame"] - pitch_f) > 8, True)
        ok("  while its earliest SEEN point is right on it",
           abs(_anch["frame"] - pitch_f) <= 3
           and abs(_anch["x"] - pitch_xy[0]) < 20
           and abs(_anch["y"] - pitch_xy[1]) < 20, True)
        _on_mark = find_flight_to(clip, _w["pitch_xy"], _w["pitch_frame"])
        _on_seen = find_flight_to(clip, (_anch["x"], _anch["y"]),
                                  int(_anch["frame"]))
        ok("  anchored on the inference it finds the roll",
           _on_mark["n_points"] < 15, True)
        ok("  anchored on the measurement it finds the flight",
           _on_seen["n_points"] >= 40, True)
        ok("    reaching the frame the ball entered",
           _on_seen["first_frame"] <= ENTER + 4, True)

        # ── AND THE TWO HALVES MAKE ONE COMET ───────────────────────
        #
        # THE PAYOFF, AND THE REASON ANY OF THIS EXISTS. The walk
        # alone covers the roll and stops at the bounce; with the
        # flight in front of it the comet shows the ball coming down.
        #
        # MERGED ON FRAME, NOT CONCATENATED. The pitch mark is not the
        # seam: the walk runs from the resting place backwards PAST
        # the pitch to wherever it gave up, so the pitch sits in the
        # middle of it, and cutting the flight at the pitch and
        # appending the whole walk duplicated seven frames and sent
        # the comet backwards in time.
        _m = {int(q["frame"]): (int(q["x"]), int(q["y"]))
              for q in _on_seen["points"]}
        _n_fl = len(_m)
        for q in _w["points"]:
            if q.get("found"):
                _m[int(q["frame"])] = (int(q["x"]), int(q["y"]))
        _comet = sorted(_m.items())
        _fr = [k for k, _ in _comet]
        ok("the comet runs in time order with no duplicates",
           (all(b > a for a, b in zip(_fr, _fr[1:])),
            len(_fr) - len(set(_fr))), (True, 0))
        ok("  and the flight contributes most of its length",
           _n_fl >= 40, True)
        _span_walk = _w["last_frame"] - _w["first_frame"]
        _span_all = _fr[-1] - _fr[0]
        ok("  covering far more than the walk did alone",
           _span_all > _span_walk * 1.5, True)
        ok("  and reaching back to the ball entering the frame",
           _fr[0] <= ENTER + 5, True)

        # ── NOTHING TO ANCHOR ON, NO SEARCH ─────────────────────────
        #
        # The contract, and the same one the reverse walk has. The old
        # unanchored search is what produced landings on clips where
        # the shot missed the green.
        n = find_flight_to(clip, None, pitch_f)
        ok("no anchor means no flight", n["ok"], False)
        ok("  and it says so", "nothing to anchor on" in (n["why"] or ""),
           True)
        n2 = find_flight_to(clip, pitch_xy, None)
        ok("no anchor frame either", n2["ok"], False)

        # ── AND A MARK WITH NO FLIGHT BEHIND IT ─────────────────────
        #
        # Pointed at a pixel nothing ever fell onto, it must come back
        # empty rather than attach whatever chain came closest.
        far = find_flight_to(clip, (200, 1000), pitch_f)
        ok("a mark with no flight behind it yields nothing",
           far["ok"], False)
        ok("  and says how near anything came",
           far["counts"].get("nearest_miss_px") is not None
           or far["counts"].get("chains_considered") == 0, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
