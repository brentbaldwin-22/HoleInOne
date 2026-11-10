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

    python3 tools/greencrawl_test.py
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


def _build_noisy(ns, path, seed=7, leaves_n=400):
    """The smeared flight, over a treeline that will not hold still.

    THE CLEAN FIXTURE CANNOT FAIL. Its only moving thing is the ball,
    so any rule at all finds it -- which is why this suite was green
    while the operator's clip was taking, in order, three points
    hundreds of pixels apart. Four hundred bright specks jittering
    every frame is what a real treeline looks like to both detectors,
    and telling the ball from them is the whole job.
    """
    import numpy as np
    W, H, FPS, N = ns["W"], ns["H"], ns["FPS"], ns["N"]
    ENTER, LAND, ROLL = ns["ENTER"], ns["LAND"], ns["ROLL"]
    REST, truth = ns["REST"], ns["truth"]
    rng = np.random.default_rng(seed)
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

    leaves = rng.integers([0, 200], [W, 620], size=(leaves_n, 2))
    for f in range(N):
        img = np.full((H, W, 3), 60, np.uint8)
        img[:330] = (175, 182, 192)
        img[330:600] = (42, 58, 44)
        for y in range(600, H):
            b = 80 + 7 * ((y // 26) % 2)
            img[y, :] = (b - 12, b + 10, b - 6)
        img[600:] = cv2.add(
            img[600:], rng.integers(0, 7, (H - 600, W, 3), dtype=np.uint8))
        for lx, ly in leaves:
            jx, jy = rng.integers(-3, 4, 2)
            cv2.circle(img, (int(lx + jx), int(ly + jy)), 2,
                       (205, 210, 205), -1)
        xy = _at(f)
        prev = _at(f - 1)
        if xy:
            if prev:
                cv2.line(img, (int(prev[0]), int(prev[1])),
                         (int(xy[0]), int(xy[1])), (240, 242, 244), 8,
                         cv2.LINE_AA)
            else:
                cv2.circle(img, (int(xy[0]), int(xy[1])), 4,
                           (240, 242, 244), -1)
        vw.write(img)
    vw.release()


def main():
    from app.services.greencrawl import find_flight_to
    from app.services import greencrawl as GF

    ns = _fixture()
    ENTER, LAND, REST, REST_FRAME = (ns["ENTER"], ns["LAND"], ns["REST"],
                                     ns["REST_FRAME"])
    ROLL = ns["ROLL"]
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

        # ── THE CRAWL: UP AND BACK, A BLOCK AT A TIME ───────────────
        #
        # THE CHAIN SEARCH ABOVE DOES NOT WORK ON REAL FOOTAGE AND THE
        # OPERATOR'S OWN CLIP SAYS SO: 383 chains considered over the
        # four seconds before the pitch mark, NONE taken, nearest arc
        # 5px away. Detecting everything in a four-second window over
        # a treeline gives thousands of detections, the linker builds
        # hundreds of chains, and the ball's dozen faint smudges are
        # one candidate among them with no particular claim.
        #
        # The crawl has NO MOTION MODEL AT ALL, which is exactly why
        # it crosses the bounce: nothing is predicted, so there is
        # nothing for an impulse to break.
        from app.services.greencrawl import crawl_flight_back
        cr = crawl_flight_back(clip, (_anch["x"], _anch["y"]),
                               int(_anch["frame"]), out_dir=d, stem="cr")
        ok("the crawl finds the flight", cr["ok"], True)
        ok("  reaching the frame the ball entered",
           cr["first_frame"] <= ENTER + 3, True)
        ok("  with a point on most frames of it",
           cr["n_points"] >= 40, True)
        ok("  rising a long way", cr["rose_px"] > 500, True)
        # EVERY POINT ON THE REAL ARC. The chain search managed 27px
        # at worst; this does better because it never has to pick one
        # chain out of hundreds -- it only ever looks where the ball
        # must be.
        _err = []
        for q in cr["points"]:
            _t = q["frame"] - ENTER
            if 0 <= _t < LAND:
                _tx, _ty = truth(_t)
                _err.append(((q["x"] - _tx) ** 2 + (q["y"] - _ty) ** 2) ** .5)
        ok("  every one of them on the real arc",
           len(_err) == cr["n_points"] and max(_err) < 35, True)
        # AND MOST OF THEM FAR CLOSER THAN THAT. The loose ones are
        # the frame difference's: its blob lights up where the ball
        # WAS and where it IS, so the centroid sits about half a step
        # behind the truth. They are kept because they are on frames
        # the top-hat could not see at all, and a reading 27px out
        # beats no reading.
        _sorted = sorted(_err)
        ok("    with the median within a few pixels",
           _sorted[len(_sorted) // 2] < 12, True)
        # AND THE ELONGATION GATE EARNS ITS KEEP HERE. A ball in
        # flight is a capsule; without the gate these frames are
        # refused for not being round.
        ok("  and a good share of them are smears",
           cr["counts"]["smears"] >= 8, True)

        # ── TWO DETECTORS, UNIONED ──────────────────────────────────
        #
        # THE OPERATOR POINTED AT THE SECOND ONE AND WAS RIGHT. The
        # add/edit wizard's click-to-plot map finds this descent on
        # footage where everything here failed, using a plain frame
        # difference against the previous frame -- no zoom, no top-hat.
        # The two answer different questions: brighter than its own
        # NEIGHBOURHOOD finds a ball that is not moving, brighter than
        # the SAME PIXEL a frame ago finds one that is. Neither covers
        # the other, so they are unioned, the way debug3 unions MOG2
        # with its plate medians for the same measured reason.
        ok("both detectors are reported separately",
           set(("by_tophat", "by_diff")) <= set(cr["counts"]), True)
        ok("  and the frame difference contributes frames of its own",
           cr["counts"]["by_diff"] > 0, True)
        ok("  with every point saying which found it",
           all(q.get("src") in ("tophat", "diff") for q in cr["points"]),
           True)
        # THE TOP-HAT WINS A FRAME THEY BOTH SEE, because it reads the
        # BALL while the difference reads the ball's smear across two
        # frames. Letting the difference win took the worst error from
        # 13px to 27px while adding nothing on those frames.
        _f_diff = {q["frame"] for q in cr["points"] if q["src"] == "diff"}
        _f_hat = {q["frame"] for q in cr["points"] if q["src"] == "tophat"}
        ok("  and never the same frame twice",
           _f_diff & _f_hat, set())

        # ── IT STOPS WHEN THE BALL IS NOT THERE, NOT BEFORE ─────────
        ok("it stops where the ball entered the frame",
           cr["first_frame"] >= ENTER - 2, True)
        ok("  and says which block ended it",
           "found nothing" in (cr["counts"]["stopped_because"] or ""), True)
        # THE DEEP RETRY RUNS ON THE BLOCK THAT FAILED, and only
        # there: depth costs the crop's area times the square of the
        # zoom, and run everywhere it took 1.3s to 41.5s on this very
        # fixture for not one extra point.
        _last = cr["blocks"][-1]
        ok("  having looked again, deeper, before giving up",
           (_last.get("deep_retry") or {}).get("zoom", 0) > _last["zoom"],
           True)
        ok("  and only on that block",
           sum(1 for b in cr["blocks"] if b.get("deep_retry")), 1)

        # ── THE CROPS ARE THE ARGUMENT ──────────────────────────────
        #
        # A line of dots proves nothing when the answer is wrong. What
        # settles it is where the search actually LOOKED: a crawl that
        # stopped because the ball left the box reads completely
        # differently from one that stopped with the ball sitting
        # inside an empty box.
        ok("every block reports the crop it searched",
           all(b.get("crop") and b.get("zoom") and b.get("why")
               for b in cr["blocks"]), True)
        ok("  and the crop puts the known point at its bottom edge",
           all(b["crop"][1] + b["crop"][3] >= b["from"][1] - 2
               for b in cr["blocks"]), True)
        ok("  with the picture drawn", bool(cr["image"]), True)

        # ── AND THE REAL TEST: A CROP FULL OF SPECKLE ───────────────
        #
        # THE CLEAN FIXTURE CANNOT FAIL THIS AND THE OPERATOR'S CLIP
        # DID. On real footage a 460x380 crop over a treeline returns
        # 243 candidates in one block and 737 in the next, and the
        # rule here was "take the highest" -- so it took, in order,
        # f514 at x=1513, f504 at x=1249, f494 at x=1327. That is not
        # a ball. Every block after the first was anchored on noise.
        #
        # This fixture reproduces that: four hundred bright specks
        # that jitter every frame, which both detectors report and
        # neither can tell from a ball on its own.
        noisy = Path(d) / "noisy.mp4"
        _build_noisy(ns, noisy)
        _w2 = track_descent_back(noisy, REST, REST_FRAME)
        _a2 = [q for q in _w2["points"] if q.get("found")][0]
        nz = crawl_flight_back(noisy, (_a2["x"], _a2["y"]),
                               int(_a2["frame"]))
        ok("the crawl survives a crop full of speckle", nz["ok"], True)
        ok("  finding the flight anyway", nz["n_points"] >= 40, True)
        ok("  and reaching the frame the ball entered",
           nz["first_frame"] <= ENTER + 3, True)
        _ne = []
        for q in nz["points"]:
            _t = q["frame"] - ENTER
            if 0 <= _t < LAND:
                _tx, _ty = truth(_t)
                _ne.append(((q["x"] - _tx) ** 2 + (q["y"] - _ty) ** 2) ** .5)
        # NOT ONE POINT OFF THE ARC. Before the linking, the chain
        # went 595px wrong on this fixture.
        ok("  with every point still on the real arc",
           len(_ne) == nz["n_points"] and max(_ne) < 35, True)
        # AND IT REALLY WAS DROWNING IN CANDIDATES -- otherwise this
        # fixture is not reproducing the thing it exists for.
        _busiest = max(b.get("n_found", 0) for b in nz["blocks"])
        ok("  out of hundreds of candidates in a block",
           _busiest > 200, True)
        ok("  linked into a path that is near dead straight",
           max(b.get("straightness_px", 0) for b in nz["blocks"]
               if b.get("took")) < 2.0, True)

        # ── AND IT SAYS WHICH FRAMES IT SEARCHED ────────────────────
        #
        # Asked directly, and the answer was only ever in a column of
        # a table that rendered unreadable. The crawl runs BACKWARDS
        # from the anchor, so the range is BEFORE that frame -- the
        # opposite of the natural guess.
        ok("the run says which frames it searched",
           bool(nz.get("searched")) and len(nz["searched"]) == 2, True)
        ok("  and they are before the anchor, not after",
           nz["searched"][1] < nz["anchor_frame"], True)
        ok("  with the anchor named too",
           nz.get("anchor_frame") == int(_a2["frame"]), True)

        # ── ANCHORED IN THE ROLL, AS THE REAL CLIP ACTUALLY IS ──────
        #
        # EVERY TEST ABOVE ANCHORS ONE FRAME PAST THE PITCH MARK,
        # which is where the clean fixture's walk happens to stop, and
        # that is not where a real walk stops. The operator read a
        # typical clip frame by frame: first contact f465, a bounce to
        # f509, another hop, rolling from f523, not still until f590.
        # The walk gives up at f524 -- the frame the roll begins,
        # sixty frames past the pitch mark -- because going backwards
        # from there the ball HOPS, and no smooth model crosses that.
        #
        # So the crawl's real job starts inside the roll and has to
        # cross the bounces to reach the descent. A failed link is a
        # BOUNCE, not the end of the ball: the continuity rule is
        # right inside an arc and exactly wrong at a contact, where
        # the vertical direction reverses over one frame.
        _af = ENTER + ROLL + 8
        _u = min(1.0, (_af - ENTER - ROLL) / 26.0)
        _x0 = truth(LAND)[0] + 6.0 * 14
        _y0 = truth(LAND)[1]
        _axy = (_x0 + (REST[0] - _x0) * _u, _y0 + (REST[1] - _y0) * _u)
        rl = crawl_flight_back(clip, _axy, _af)
        ok("anchored in the roll it still reaches the flight",
           rl["ok"] and rl["first_frame"] <= ENTER + 3, True)
        ok("  by crossing the bounce rather than stopping at it",
           (rl["counts"].get("bounces_crossed") or 0) >= 1, True)
        ok("  and a block is marked where it did",
           sum(1 for b in rl["blocks"] if b.get("bounce")) >= 1, True)

        # ── AND IT PRODUCES THE PITCH MARK ──────────────────────────
        #
        # THE DEPENDENCY INVERTED, AND THE WHOLE POINT. The pitch mark
        # used to come from the reverse walk -- "the first frame it
        # stopped falling" -- applied to a chain containing no fall,
        # so it named a point in the roll and everything downstream
        # was anchored on it. The crawl has to cross the contacts to
        # get where it is going, so it knows where they are, and the
        # earliest is the first time the ball touched the green.
        ok("the crawl names the pitch mark", rl.get("pitch_frame") is not None,
           True)
        ok("  within a frame or two of the truth",
           abs(rl["pitch_frame"] - (ENTER + LAND)) <= 3, True)
        ok("  and within a few pixels of it",
           (abs(rl["pitch_xy"][0] - truth(LAND)[0]) < 25
            and abs(rl["pitch_xy"][1] - truth(LAND)[1]) < 30), True)
        # AND IT IS BETTER THAN THE RULE IT REPLACES, measured on the
        # same clip rather than asserted: the walk's own pitch mark,
        # from an anchor it reached by itself.
        _wp = track_descent_back(clip, REST, REST_FRAME)
        ok("  beating the walk's inference on the same clip",
           abs(rl["pitch_frame"] - (ENTER + LAND))
           < abs((_wp.get("pitch_frame") or 0) - (ENTER + LAND)), True)

        # ── AND A ROLLING BALL IS NOT REFUSED FOR BEING LEVEL ───────
        #
        # "The ball came from above" is true of a flight and false of
        # everything after the first bounce: a rolling ball is at the
        # same height as where it ends up, and a bounce arc comes down
        # TO the green. With no slack, every candidate in the first
        # blocks of a real crawl was thrown out for not being high
        # enough.
        from app.services import greencrawl as _GC
        ok("there is slack below the known point",
           _GC.CRAWL_BELOW_SLACK > 0, True)

        # ── NOTHING TO ANCHOR ON ────────────────────────────────────
        ok("no anchor means no crawl",
           crawl_flight_back(clip, None, 100)["ok"], False)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
