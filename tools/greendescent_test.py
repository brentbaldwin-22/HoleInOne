#!/usr/bin/env python3
"""The descent, walked backwards from a ball we already measured.

WHY BACKWARDS, AND WHY THIS IS TESTABLE AT ALL. The old search
segmented every frame of a window of green footage, built chains out
of everything that moved, and tried to pick the ball out of them with
gates -- on a green ringed with brush, where the wind moves more of
the frame than the ball does. There was no ground truth to test it
against because the whole problem was identifying the subject.

Walking back from a MEASURED resting place turns it into a tracking
problem with a known start, which has an answer you can check. So the
fixture plants a ball on a known parabola, bounces it once, rolls it
to a known pixel, and the test asks for that parabola back.

THREE FAILURES THIS LOCKS DOWN, each of which cost a debugging round:

  * velocity measured from the running position rather than the last
    point actually SEEN. After a miss the running position has
    drifted, so the next hit reads that drift as motion, the next
    prediction overshoots, and the walk settles into a limit cycle
    that finds the ball every fifth frame. 26 found and 66
    extrapolated, on a fixture where the ball is plainly visible in
    every frame.

  * the detector's area bounds applied in the ZOOMED zone's pixels.
    At 6x a 50px ball is 1800px of area and dies on a ceiling written
    for 400 -- inside the detector, before the caller can see it.
    Zero detections on twenty-two frames of a stationary ball.

  * the top-hat kernel left at 15px while the crop was scaled up.
    Once the ball is wider than the kernel the opening keeps its own
    interior and the top-hat returns a thin rim, which fragments into
    specks and fails roundness. One blob at 2x, twenty at 3x.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_fails = []

W, H, FPS, N = 1920, 1080, 60, 300
ENTER = 110          # the frame the ball enters, falling
LAND = 58            # frames after ENTER that it first touches down
ROLL = LAND + 14     # ...and finishes its one bounce
REST = (1689, 811)
REST_FRAME = 260


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def truth(t):
    """Where the ball is `t` frames after it entered."""
    return 1300 + 2.2 * t, 120 + 0.26 * t * t


def build(path):
    """A green clip: sky, treeline, striped turf, a waving branch.

    The branch is the control. It is the only other thing in the
    picture that moves, it moves every frame, and the walk must never
    once consider it -- not because a gate rejects it, but because it
    is never inside the window.
    """
    rng = np.random.default_rng(5)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    seen = {}
    for f in range(N):
        img = np.full((H, W, 3), 60, np.uint8)
        img[:330] = (175, 182, 192)
        img[330:600] = (42, 58, 44)
        for y in range(600, H):
            b = 80 + 7 * ((y // 26) % 2)
            img[y, :] = (b - 12, b + 10, b - 6)
        img[600:] = cv2.add(
            img[600:], rng.integers(0, 7, (H - 600, W, 3), dtype=np.uint8))
        bx = 500 + int(26 * np.sin(f / 4.0))
        cv2.line(img, (bx, 380), (bx + 50, 430), (150, 160, 150), 3)
        t = f - ENTER
        xy = None
        if t < 0:
            pass
        elif t < LAND:
            xy = truth(t)
        elif t < ROLL:
            u = t - LAND
            xy = (truth(LAND)[0] + 6.0 * u,
                  truth(LAND)[1] - 16.0 * u + 1.6 * u * u)
        else:
            u = min(1.0, (t - ROLL) / 26.0)
            x0 = truth(LAND)[0] + 6.0 * 14
            y0 = truth(LAND)[1]
            xy = (x0 + (REST[0] - x0) * u, y0 + (REST[1] - y0) * u)
        if xy:
            seen[f] = xy
            cv2.circle(img, (int(xy[0]), int(xy[1])), 4, (240, 242, 244), -1)
        vw.write(img)
    vw.release()
    return seen


def build_smeared(path):
    """The same flight, drawn the way a real exposure draws it.

    THE OLD FIXTURE DREW A PERFECT CIRCLE ON EVERY FRAME, which is
    why the round gates passed it and why nothing in this file ever
    caught what the field kept reporting. A camera does not work that
    way: the shutter is open while the ball moves, so a ball crossing
    thirty pixels in one frame is painted as a capsule thirty pixels
    long. Here that is drawn literally -- a line from where the ball
    was to where it is -- which near the landing is a smear of about
    30px against a 8px ball.

    That single change is the difference between a fixture that
    always passed and the clip that never worked.
    """
    rng = np.random.default_rng(5)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    def _at(f):
        t = f - ENTER
        if t < 0:
            return None
        if t < LAND:
            return truth(t)
        if t < ROLL:
            u = t - LAND
            return (truth(LAND)[0] + 6.0 * u,
                    truth(LAND)[1] - 16.0 * u + 1.6 * u * u)
        u = min(1.0, (t - ROLL) / 26.0)
        x0 = truth(LAND)[0] + 6.0 * 14
        y0 = truth(LAND)[1]
        return (x0 + (REST[0] - x0) * u, y0 + (REST[1] - y0) * u)

    seen = {}
    for f in range(N):
        img = np.full((H, W, 3), 60, np.uint8)
        img[:330] = (175, 182, 192)
        img[330:600] = (42, 58, 44)
        for y in range(600, H):
            b = 80 + 7 * ((y // 26) % 2)
            img[y, :] = (b - 12, b + 10, b - 6)
        img[600:] = cv2.add(
            img[600:], rng.integers(0, 7, (H - 600, W, 3), dtype=np.uint8))
        bx = 500 + int(26 * np.sin(f / 4.0))
        cv2.line(img, (bx, 380), (bx + 50, 430), (150, 160, 150), 3)
        xy = _at(f)
        prev = _at(f - 1)
        if xy:
            seen[f] = xy
            if prev:
                # THE EXPOSURE. From where it was to where it is.
                cv2.line(img, (int(prev[0]), int(prev[1])),
                         (int(xy[0]), int(xy[1])), (240, 242, 244), 8,
                         cv2.LINE_AA)
            else:
                cv2.circle(img, (int(xy[0]), int(xy[1])), 4,
                           (240, 242, 244), -1)
        vw.write(img)
    vw.release()
    return seen


def main():
    from app.services.greendescent import track_descent_back

    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "green.mp4"
        seen = build(clip)
        r = track_descent_back(clip, REST, REST_FRAME,
                               out_dir=d, stem="t")

        ok("the walk finds a descent", r["ok"], True)
        # IT REACHES THE FRAME THE BALL ENTERED. Anything later means
        # it lost the ball on the way up and stopped early, which is
        # the failure every bug above produced.
        ok("  and walks all the way back to the entry frame",
           r["first_frame"], ENTER)
        ok("  with a point for every frame between", r["n_points"],
           REST_FRAME - ENTER + 1)
        ok("  and barely any of them extrapolated",
           r["n_missed"] <= 3, True)
        ok("  falling, not rising", r["fall_px"] > 400, True)

        # ── THE PARABOLA COMES BACK ─────────────────────────────────
        err = []
        for q in r["points"]:
            t = q["frame"] - ENTER
            if 0 <= t < LAND and q["found"]:
                tx, ty = truth(t)
                err.append(((q["x"] - tx) ** 2 + (q["y"] - ty) ** 2) ** 0.5)
        ok("every in-flight frame is tracked", len(err), LAND)
        ok("  to within two pixels of where the ball was drawn",
           max(err) < 2.0, True)

        # ── THE PITCH MARK IS THE FIRST CONTACT, NOT THE LAST ───────
        # The difference is a whole bounce. "The last frame it was
        # falling fast" is the end of the bounce, several feet from
        # where the ball actually pitched, and what everything
        # downstream calls the landing is the first contact.
        tx, ty = truth(LAND)
        ok("the pitch mark is the first contact's frame",
           abs(r["pitch_frame"] - (ENTER + LAND)) <= 1, True)
        ok("  and its pixel, within two",
           (abs(r["pitch_xy"][0] - tx) <= 2,
            abs(r["pitch_xy"][1] - ty) <= 2), (True, True))

        # ── THE BRANCH IS NEVER CONSIDERED ──────────────────────────
        # Not rejected by a gate: never inside the window. That is
        # the whole argument for walking backwards from a known ball
        # rather than searching the green for an unknown one.
        ok("nothing is tracked anywhere near the waving branch",
           any(q["x"] < 700 and q["y"] < 500 for q in r["points"]), False)

        # ── AND THE PICTURES EXIST ──────────────────────────────────
        ok("the chain picture is drawn", bool(r["image"]), True)
        ok("  and a handful of the search windows",
           1 <= len(r["windows"]) <= 8, True)
        ok("  each at the zoom it was searched at",
           all(w["zoom"] >= 1.0 for w in r["windows"]), True)

        # ── A LONG REST BEFORE THE WALK EVEN STARTS ─────────────────
        #
        # The walk begins at the frame the ball was MEASURED at, and
        # that is wherever the green window ladder found it -- eight,
        # fourteen, twenty seconds after the strike. The ball has
        # been lying still for most of that. With one budget for the
        # whole walk, the budget was spent on the rest: a real clip
        # reported "241 points, falling 0px over 240 frames" and
        # stopped, having watched a stationary ball the entire way
        # and never reached the descent at all.
        #
        # A stationary frame is nearly free -- one small window, the
        # ball exactly where it was -- so the rest gets its own
        # generous allowance and the four seconds that matter start
        # counting from the frame the ball is first seen to move.
        r_late = track_descent_back(clip, REST, 299)
        ok("a walk started long after the ball settled still reaches it",
           r_late["first_frame"], ENTER)
        ok("  and says when it first saw it move",
           r_late.get("moved_at_frame") is not None, True)
        ok("  and finds the same pitch mark",
           abs(r_late["pitch_frame"] - (ENTER + LAND)) <= 1, True)
        ok("  and the same fall", r_late["fall_px"], r["fall_px"])

        # ── NO BALL AT REST, NO DESCENT ─────────────────────────────
        # The contract. The old behaviour -- hunt the green and attach
        # whatever best survived the gates -- is what produced
        # landings on clips where the shot missed the green.
        r2 = track_descent_back(clip, None, REST_FRAME)
        ok("no resting place means no walk", r2["ok"], False)
        ok("  and it says so", "no ball at rest" in (r2["why"] or ""), True)

        # ── A RESTING PLACE WITH NOTHING BEHIND IT ──────────────────
        # Pointed at bare turf, the walk must come back empty rather
        # than wander off following noise.
        r3 = track_descent_back(clip, (300, 950), 60)
        ok("a spot with no ball on it yields no chain",
           r3["n_points"] <= 3, True)

        # ── AND IT SHOWS ITS WORK, ON BOTH BRANCHES ─────────────────
        #
        # ASKED DIRECTLY, FIVE ROUNDS IN: is it zooming, is it every
        # frame or every few, and is the detector MOG, a model or
        # brightness. All three were answerable only by reading the
        # module, which means the walk was not showing its work --
        # and the branch where the question gets asked is the branch
        # that FAILED, which is the one that used to carry least.
        for _lbl, _r in (("on a walk that worked", r),
                         ("on a walk that found nothing", r3)):
            ok("the work log is there %s" % _lbl,
               len(_r.get("log") or []) > 0, True)
            ok("  and the totals beside it",
               bool(_r.get("counts") or {}), True)
            ok("  counting every frame it looked at",
               (_r["counts"].get("frames") or 0) > 0, True)
            ok("  and saying so in a sentence",
               "every frame, backwards" in (_r.get("work") or ""), True)
            # THE THREE ANSWERS, AS DATA RATHER THAN PROSE.
            _d = _r.get("detector") or {}
            ok("  the detector names itself brightness",
               _d.get("kind"), "brightness")
            _not = (_d.get("not") or "").lower()
            ok("  and says it is not MOG and not a model",
               ("mog2" in _not and "no model" in _not
                and "no ai" in _not), True)
            ok("  and that it zooms", "capped at" in (_d.get("zoom") or ""),
               True)
            ok("  and that it reads every frame",
               "every frame" in (_d.get("cadence") or ""), True)
            # EVERY ROW SAYS WHAT WAS IN THE WINDOW AND WHY IT WAS
            # TAKEN OR NOT -- including the rows that took nothing.
            ok("  every row carries a window, a zoom and a reason",
               all(q.get("window_px") and q.get("zoom")
                   and q.get("why") for q in _r["log"]), True)

        # A FRAME WITH CANDIDATES THAT WERE ALL TOO FAR IS A DIFFERENT
        # FAILURE FROM A FRAME WITH NONE, and the fixes are different:
        # the first is the velocity model, the second is the gates.
        # Counted separately so the report can say which happened.
        for _k in ("none_at_all", "too_far", "taken", "with_cands",
                   "widest_window_px", "min_zoom", "max_zoom"):
            ok("the totals break down %s" % _k,
               _k in r["counts"], True)
        ok("  and the zoom it reports is a real zoom",
           (r["counts"]["max_zoom"] or 0) > 1.0, True)

        # ── THE WALK DOES NOT HOLD A CROP PER FRAME ─────────────────
        #
        # THIS WAS UNBOUNDED AND IT IS THE LIKELIEST REASON A LONG
        # CLIP CAME BACK WITH NOTHING. Eight window pictures are
        # drawn, so the loop kept one zoomed crop per frame and chose
        # eight at the end. A 48px window at 6x is 332KB; the still
        # budget is thirty seconds, which at 60fps is 1,800 frames,
        # which is 600MB of grayscale crops held for the length of
        # the walk -- and an allocation failure in there surfaces as
        # the generic "the reverse descent walk failed".
        from app.services import greendescent as _gd
        ok("the held-tile count is capped",
           _gd.MAX_TILES_HELD <= 128, True)
        ok("  and the log row count with it",
           _gd.MAX_LOG_ROWS <= 600, True)
        # A WALK LONG ENOUGH TO TRIP THE CAP STILL SPANS THE CHAIN.
        # Thinning drops every second row rather than the oldest, so
        # the descent -- the far end, the part being looked for -- is
        # still represented.
        _long = track_descent_back(clip, REST, 299, out_dir=d, stem="u")
        ok("a long walk thins rather than grows without bound",
           len(_long["log"]) <= _gd.MAX_LOG_ROWS, True)
        ok("  and still reaches the entry frame",
           _long["first_frame"], ENTER)
        ok("  with its log still spanning the whole walk",
           (min(q["frame"] for q in _long["log"]) <= ENTER + 3
            and max(q["frame"] for q in _long["log"]) >= 295), True)
        ok("  and still draws its windows",
           1 <= len(_long["windows"]) <= 8, True)

        # ── WHERE IT LOST IT, AS ITS OWN ANSWER ─────────────────────
        #
        # THE QUESTION A 316-POINT WALK ACTUALLY RAISES. On a real
        # clip the walk reported "316 point(s) walked back from the
        # ball at rest, falling 7px over 315 frame(s)" and stopped --
        # a walk that followed a ball ROLLING for five seconds and
        # never reached the flight. Nothing in the report said where
        # it stopped, how fast the ball was going when it lost the
        # thread, or how wide the window had grown; the chain picture
        # labelled the resting place and the pitch mark, both at the
        # near end, and left the far end bare.
        for _lbl, _r in (("on a walk that worked", r),
                         ("on a walk that found nothing", r3),
                         ("on a long walk", _long)):
            _l = _r.get("lost_at") or {}
            ok("the walk says where it gave up %s" % _lbl, bool(_l), True)
            ok("  on a named frame", isinstance(_l.get("frame"), int), True)
            ok("  at a named pixel",
               bool(_l.get("xy")) and len(_l["xy"]) == 2, True)
            # THE NUMBER THE WHOLE THING TURNS ON. Losing a ball at
            # 1px/frame is losing a ball that was ROLLING, so the
            # descent is still ahead of the search and it never
            # reached the part it exists to find. Losing one at
            # 40px/frame is losing it in the air, which is a
            # completely different fix.
            ok("  with the speed it was going when it lost it",
               _l.get("speed_px_per_frame") is not None, True)
            ok("  and how wide the box had grown",
               (_l.get("window_px") or 0) > 0, True)
            ok("  and whether that box was at its cap",
               "window_was_capped" in _l, True)
            ok("  and the frames either side, unthinned",
               len(_l.get("log") or []) > 0, True)
            ok("  said in a sentence", "last actually seen" in
               (_l.get("why") or ""), True)

        # ── AND IT DRAWS THE PLACE IT GAVE UP ───────────────────────
        #
        # Eight tiles spread over 316 frames are eight pictures of a
        # ball sitting still: the stride was forty-two and the twenty
        # frames it died on were at one end, so not one of them was
        # drawn. Its own gallery, not a share of the same eight slots
        # -- they answer different questions and the spread won every
        # time.
        ok("the windows at the loss are their own gallery",
           1 <= len(r.get("lost_windows") or []) <= 6, True)
        ok("  and they are the frames at or before the last sighting",
           all(w["frame"] <= r["lost_at"]["frame"]
               for w in r["lost_windows"]), True)
        ok("  on a long walk too",
           1 <= len(_long.get("lost_windows") or []) <= 6, True)
        # THE TAIL IS NEVER THINNED AWAY. `del seq[1::2]` on an
        # even-length list drops the LAST entry -- which, on a walk
        # that runs backwards, is the earliest frame and the only one
        # anybody asks about.
        ok("  and the thinning cannot drop the frames at the loss",
           _long["lost_windows"][0]["frame"]
           <= _long["lost_at"]["frame"], True)

        # ── THE ELONGATION GATE, ON A BALL THAT IS ACTUALLY BLURRED
        #
        # THE FIXTURE ABOVE PAINTS A PERFECT CIRCLE ON EVERY FRAME,
        # which is why the round gates pass it and why nothing in this
        # file ever caught what the field kept reporting. A real
        # exposure smears a ball crossing thirty pixels into a
        # thirty-pixel capsule, and MAX_ASPECT rejects exactly that --
        # from one side if the smear lies along an axis, and from the
        # other, via MIN_FILL, if it lies diagonally.
        #
        # TESTED AT THE DETECTOR, WHICH IS WHAT CHANGED. The walk is
        # tested separately below, and does not reach the flight for a
        # reason that has nothing to do with the gate.
        from app.services import ball_vanish as _bv
        from app.services.greendescent import _detect_in_window
        clip2 = Path(d) / "smeared.mp4"
        build_smeared(clip2)
        _cap = cv2.VideoCapture(str(clip2))
        _round_found = _motion_found = _streaks = _worse = 0
        _offs = []
        for _t in range(3, LAND, 3):
            _cap.set(cv2.CAP_PROP_POS_FRAMES, ENTER + _t)
            _okf, _fr = _cap.read()
            if not _okf:
                continue
            _g = cv2.cvtColor(_fr, cv2.COLOR_BGR2GRAY)
            _x, _y = truth(_t)
            _px, _py = truth(_t - 1)
            _a, _, _, _ = _detect_in_window(_g, _x, _y, 60)
            _sh = []
            _b, _, _, _ = _detect_in_window(_g, _x, _y, 60,
                                            motion=(_x - _px, _y - _py),
                                            shapes_out=_sh)
            _round_found += 1 if _a else 0
            _motion_found += 1 if _b else 0
            if len(_b) < len(_a):
                _worse += 1
            if _sh and _sh[0].get("streak"):
                _streaks += 1
                _offs.append(_sh[0]["off_by_deg"])
        _cap.release()

        ok("the round gates lose the blurred ball as it speeds up",
           _round_found < 14, True)
        ok("  the elongation gate finds it on every frame",
           _motion_found, len(range(3, LAND, 3)))
        ok("  and recovers it as a SMEAR, not by loosening roundness",
           _streaks >= 6, True)
        # THE PROPERTY THAT MAKES THIS SAFE TO SHIP. The motion branch
        # is reached only by a blob the round gates have already
        # refused, so a caller that passes `motion` gets a strict
        # SUPERSET of what the same call returns without it. Written
        # as two independent tests this was quietly violated at the
        # boundary -- a capsule measuring 2.23:1 on its own axes
        # failed the motion branch while the same blob measured 2.2:1
        # on its bounding box and passed the round one.
        ok("  and never finds LESS than the round gates did", _worse, 0)
        # AND IT IS THE ALIGNMENT DOING THE WORK, not a blanket
        # relaxation: every smear accepted lies along the travel.
        ok("  every smear it took lies along the direction of travel",
           max(_offs) <= 5.0, True)

        # ── AND THE WALK STILL CANNOT CROSS THE BOUNCE ──────────────
        #
        # THE GATE IS NECESSARY AND NOT SUFFICIENT, recorded here so
        # it is not mistaken for finished. Walking backwards, the ball
        # goes roll -> bounce -> flight, and a bounce is an IMPULSE:
        # the velocity reverses in y and jumps in magnitude over one
        # frame, which no smooth motion model crosses in either
        # direction. On this fixture both walks stop at the frame
        # before the bounce, so the gate never gets a frame to fire
        # on, and `streaks` is zero for both.
        #
        # The fix is structural, not a constant: find the flight
        # FORWARD as a ballistic arc anchored on the pitch mark this
        # walk hands over, the way the tee's ascent sweep is anchored
        # on the measured ball. Until that exists this assertion
        # documents the wall rather than pretending it is not there.
        _w = track_descent_back(clip2, REST, REST_FRAME)
        _sv = _bv.STREAK_MIN_SPEED
        try:
            _bv.STREAK_MIN_SPEED = 1e9          # round gates alone
            _wo = track_descent_back(clip2, REST, REST_FRAME)
        finally:
            _bv.STREAK_MIN_SPEED = _sv
        ok("the walk stops at the bounce, with the gate or without",
           (_w["first_frame"] >= ENTER + LAND - 2,
            _wo["first_frame"] >= ENTER + LAND - 2), (True, True))
        ok("  so it never reaches a frame fast enough to smear",
           _w["counts"]["streaks"], 0)
        # NO WORSE, THOUGH. The gate must not cost the walk anything
        # on the part it already handled.
        ok("  and the gate costs the walk nothing it had",
           _w["first_frame"] <= _wo["first_frame"], True)
        ok("  nor any of its points",
           _w["n_points"] >= _wo["n_points"], True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
