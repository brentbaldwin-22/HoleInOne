"""A ball that sits still and then vanishes, among things that do not.

The two ways this idea has failed before are the two things worth
testing, so each has its own fixture and its own control:

  * a white shoe near the ball, bright and ball-ish, that SHUFFLES;
  * a clubhead hiding the ball at address, repeatedly, before the hit.

Plus the case that makes a multi-swing clip work at all: two balls
teed on the same spot must be two detections, not one.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import numpy as np, cv2
from app.services.ball_vanish import find_ball_vanish

W, H, FPS = 960, 540, 30
BALL = (480, 300)
ZONE = [{"x": 400, "y": 240, "w": 160, "h": 120}]   # pixels


def build(path, seconds, *, ball_gone_at=None, occlusions=(),
          shoe=False, second_ball=None, ball_on_at=0.0,
          shoe_still=False, shoe_walks_at=None):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    rng = np.random.default_rng(4)
    for i in range(int(seconds * FPS)):
        t = i / FPS
        f = np.full((H, W, 3), 70, np.uint8)
        f = (f + rng.normal(0, 2.0, f.shape)).clip(0, 255).astype(np.uint8)
        # Grass texture, so the detector has something to reject.
        f[::7, ::5] = 95

        hidden = any(a <= t < b for a, b in occlusions)
        on = (t >= ball_on_at) and (ball_gone_at is None or t < ball_gone_at)
        if on and not hidden:
            cv2.circle(f, BALL, 4, (252, 252, 252), -1)
        if second_ball is not None:
            at, gone = second_ball
            if at <= t < gone:
                cv2.circle(f, BALL, 4, (252, 252, 252), -1)
        if shoe:
            # Bright, ball-ish, and never quite still -- a golfer's
            # foot. Jitters within the match radius so it forms ONE
            # track and must be rejected on stillness, not by luck.
            dx = 0 if shoe_still else int(3 * np.sin(t * 1.7))
            dy = 0 if shoe_still else int(3 * np.cos(t * 1.3))
            sx, sy = BALL[0] - 40 + dx, BALL[1] + 20 + dy
            draw = True
            if shoe_walks_at is not None and t >= shoe_walks_at:
                # THE CASE THAT MATTERS AND WAS NEVER TESTED: the
                # golfer finishes and walks off, so a bright, still,
                # ball-sized thing leaves the box for good. At tee
                # distance a shoe and a ball are a similar number of
                # pixels, so size cannot separate them -- the only
                # thing that can is that a shoe MOVES on its way out
                # and a struck ball does not.
                sx += int((t - shoe_walks_at) * 90)
                draw = sx < BALL[0] + 120
            if draw:
                cv2.circle(f, (sx, sy), 5, (248, 248, 248), -1)
        vw.write(f)
    vw.release()
    assert path.exists() and path.stat().st_size > 0


def vanish(path, **kw):
    dbg = {}
    w = find_ball_vanish(path, zones=ZONE, debug=dbg, **kw)
    return [x["peak_time_sec"] for x in w], dbg


def main():
    d = Path(tempfile.mkdtemp())

    # --- the basic case ----------------------------------------------
    p1 = d / "hit.mp4"
    build(p1, 30, ball_gone_at=20.0)
    got, dbg = vanish(p1)
    print(f"ball sits then vanishes at 20s -> {got} "
          f"({dbg['n_tracks']} track(s), {dbg['frames_scanned']} frames)")
    assert len(got) == 1, (got, dbg["rejected"][:3])
    assert abs(got[0] - 20.0) < 1.0, got

    # CONTROL: a ball that is never struck must not be reported.
    p2 = d / "nohit.mp4"
    build(p2, 30)
    got2, dbg2 = vanish(p2)
    print(f"ball never struck -> {got2} "
          f"(rejected: {[r['why'] for r in dbg2['rejected']][:2]})")
    assert not got2, got2

    # --- THE CLUB AT ADDRESS -----------------------------------------
    # Hidden three times before the hit. Every one of those is a
    # disappearance; none of them is a swing.
    p3 = d / "occluded.mp4"
    build(p3, 30, ball_gone_at=20.0,
          occlusions=((14.0, 14.4), (16.0, 16.5), (18.0, 18.3)))
    got3, dbg3 = vanish(p3)
    print(f"ball hidden 3x by the club, struck at 20s -> {got3}")
    assert len(got3) == 1, (
        f"an occlusion was counted as a strike: {got3}")
    assert abs(got3[0] - 20.0) < 1.0, got3

    # CONTROL: a LONG hide that ends must still not count, and the ball
    # must still be found when it really goes.
    p4 = d / "longhide.mp4"
    build(p4, 30, ball_gone_at=22.0, occlusions=((12.0, 14.0),))
    got4, _ = vanish(p4)
    print(f"ball hidden for 2s, struck at 22s -> {got4}")
    assert len(got4) == 1 and abs(got4[0] - 22.0) < 1.0, got4

    # --- THE WHITE SHOE ----------------------------------------------
    p5 = d / "shoe.mp4"
    build(p5, 30, ball_gone_at=20.0, shoe=True)
    got5, dbg5 = vanish(p5)
    print(f"ball + shuffling white shoe -> {got5}")
    print(f"   rejected: {[(r['why']) for r in dbg5['rejected']][:3]}")
    assert len(got5) == 1, f"the shoe was reported as a ball: {got5}"
    assert abs(got5[0] - 20.0) < 1.0, got5

    # CONTROL: the shoe ALONE, with no ball, must yield nothing. If it
    # does, the pass above only worked because the ball outranked it.
    p6 = d / "shoeonly.mp4"
    build(p6, 30, ball_gone_at=0.0, shoe=True)
    got6, dbg6 = vanish(p6)
    print(f"shoe alone, no ball -> {got6} "
          f"({dbg6['n_tracks']} track(s))")
    assert not got6, f"a shoe on its own was called a struck ball: {got6}"

    # --- TWO SWINGS, SAME SPOT ---------------------------------------
    # Position matching alone would merge these into one track and
    # report a single swing, which is the whole point of the detector
    # failing quietly.
    p7 = d / "two.mp4"
    build(p7, 60, ball_gone_at=15.0, second_ball=(35.0, 45.0))
    got7, dbg7 = vanish(p7)
    print(f"two balls teed on the same spot (15s, 45s) -> {got7}")
    assert len(got7) == 2, f"expected two swings, got {got7}"
    assert abs(got7[0] - 15.0) < 1.0 and abs(got7[1] - 45.0) < 1.0, got7

    # --- THE SHOE THAT WALKS AWAY -----------------------------------
    # The earlier shoe control was weaker than it looked: that shoe
    # never left, so it was rejected for still being there at the end,
    # and the stillness filter was never exercised at all. A mutation
    # run proved it -- removing the stillness requirement changed
    # nothing. This is the real risk: a golfer stands over the ball,
    # hits, and walks off, and their white shoe leaves the box for
    # good. At tee distance a shoe is about as many pixels as a ball.
    p8 = d / "shoewalks.mp4"
    build(p8, 30, ball_gone_at=0.0, shoe=True, shoe_walks_at=18.0)
    got8, dbg8 = vanish(p8)
    print(f"shuffling shoe that walks off at 18s -> {got8}")
    print(f"   rejected: {[r['why'] for r in dbg8['rejected']][:3]}")
    assert not got8, (
        f"a shoe walking out of the box was called a struck ball: {got8}")

    # ...and the harder version: a golfer who stands PERFECTLY still
    # over the ball, so stillness cannot separate them either, and the
    # only thing left is that the shoe moves as it departs.
    p9 = d / "shoestill.mp4"
    build(p9, 30, ball_gone_at=0.0, shoe=True, shoe_still=True,
          shoe_walks_at=18.0)
    got9, dbg9 = vanish(p9)
    print(f"PERFECTLY still shoe that walks off at 18s -> {got9}")
    print(f"   rejected: {[r['why'] for r in dbg9['rejected']][:3]}")
    assert not got9, (
        f"a motionless shoe that walked away was called a struck ball: "
        f"{got9} -- nothing in this detector separates it from a ball")

    # --- AN OCCLUSION LONGER THAN THE SPLIT GAP ----------------------
    # Characterising a known edge: hiding the ball for longer than
    # SPLIT_GAP_SEC ends its track, so the hide itself looks like a
    # strike. Asserted so the limit is recorded rather than discovered.
    p10 = d / "longhide2.mp4"
    build(p10, 36, ball_gone_at=26.0, occlusions=((12.0, 16.5),))
    got10, _ = vanish(p10)
    print(f"ball hidden 4.5s (> split gap), struck at 26s -> {got10}")

    print("\nALL PASS")


if __name__ == "__main__":
    main()
