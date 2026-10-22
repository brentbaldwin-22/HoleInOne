"""The Pi's detector, replayed by the backend tester, on built footage.

Every assertion is paired with a control that must come out the other
way, because a detector biased toward keeping footage passes a "did it
find the swing?" test by finding a swing in anything.
"""
import sys, subprocess, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import numpy as np, cv2
from app.services.pi_swingfind import detect_swings_pi, motion_trace, pi_defaults

W, H, FPS = 320, 180, 30


def _write(path, n_sec, bursts, noise=1.0, walk=None):
    """A clip of quiet frames with motion bursts at given times.

    `bursts` are (at_sec, width_sec, amplitude). `walk` is a slow
    plateau -- a group crossing the frame -- which is the thing the
    detector must NOT call a swing.
    """
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    rng = np.random.default_rng(7)
    for i in range(int(n_sec * FPS)):
        t = i / FPS
        frame = np.full((H, W, 3), 40, np.uint8)
        frame = (frame + rng.normal(0, noise, frame.shape)).clip(0, 255).astype(np.uint8)
        for at, width, amp in bursts:
            if abs(t - at) < width / 2:
                # A block that jumps position each frame: large frame
                # differences concentrated in a short span.
                x = 100 + int(40 * np.sin(i * 2.7))
                frame[60:120, x:x + 40] = int(min(255, 40 + amp))
        if walk is not None:
            # ENTERS FROM OFF-FRAME. A block that appears at full size
            # is a step change, and a step change is a spike -- the
            # first version of this fixture made the walker look like
            # a swing (burst width 0.27 s) and failed the detector for
            # something it does not do. Sliding in from x < 0 gives the
            # gradual onset that makes locomotion a plateau.
            w_at, w_width = walk
            if abs(t - w_at) < w_width / 2:
                frac = (t - (w_at - w_width / 2)) / w_width
                x = int(-60 + frac * (W + 60))
                x0, x1 = max(0, x), max(0, min(W, x + 60))
                if x1 > x0:
                    frame[50:150, x0:x1] = 230
        vw.write(frame)
    vw.release()
    assert path.exists() and path.stat().st_size > 0, "fixture never got written"


def main():
    d = Path(tempfile.mkdtemp())
    p = pi_defaults()
    print("rig defaults:", p)

    # --- the signal is really being measured -------------------------
    swing = d / "swing.mp4"
    _write(swing, 30, [(15.0, 0.4, 180)])
    tr = motion_trace(swing, sample_hz=20.0)
    assert len(tr["times"]) > 100, f"too few samples: {len(tr['times'])}"
    peak_at = tr["times"][int(np.argmax(tr["motion"]))]
    assert abs(peak_at - 15.0) < 1.0, f"peak at {peak_at}, expected ~15"
    print(f"  motion peak at {peak_at:.1f}s  ({len(tr['times'])} samples, "
          f"{tr['decoded']} frames decoded of {tr['frames']})")

    # CONTROL: quiet footage must not produce a peak worth finding.
    flat = d / "flat.mp4"
    _write(flat, 30, [])
    dbg_f = {}
    w_flat = detect_swings_pi(flat, debug=dbg_f, **p)
    print(f"  flat clip -> {len(w_flat)} window(s), "
          f"best ratio {dbg_f.get('best_ratio')}")
    assert not w_flat, f"found {len(w_flat)} swings in quiet footage"

    # --- one swing is found, and cut where the swing is --------------
    dbg = {}
    w = detect_swings_pi(swing, debug=dbg, **p)
    assert len(w) == 1, f"expected 1 window, got {len(w)}: {w}"
    win = w[0]
    assert abs(win["peak_time_sec"] - 15.0) < 1.0, win
    # The window is the rig's 4.5 before / 9.0 after.
    assert abs((win["peak_time_sec"] - win["start_sec"]) - p["before_sec"]) < 0.3, win
    assert abs((win["end_sec"] - win["peak_time_sec"]) - p["after_sec"]) < 0.3, win
    print(f"  swing  -> {win['start_sec']:.1f}-{win['end_sec']:.1f}s "
          f"ratio {win['ratio']} conf {win['confidence']}")

    # --- two swings stay two, and do not merge -----------------------
    two = d / "two.mp4"
    _write(two, 60, [(15.0, 0.4, 180), (40.0, 0.4, 180)])
    w2 = detect_swings_pi(two, **p)
    assert len(w2) == 2, f"expected 2 windows, got {len(w2)}: {w2}"
    print(f"  two swings -> {[(round(x['start_sec'],1), round(x['end_sec'],1)) for x in w2]}")

    # CONTROL: two bursts closer than min_separation are ONE swing
    # (backswing and follow-through both spike).
    close = d / "close.mp4"
    _write(close, 30, [(15.0, 0.4, 180), (17.0, 0.4, 180)])
    wc = detect_swings_pi(close, **p)
    assert len(wc) == 1, f"bursts {p['min_separation_sec']}s apart should be one swing, got {len(wc)}"
    print(f"  two bursts 2s apart -> {len(wc)} window (correctly merged)")

    # --- the threshold really is load-bearing ------------------------
    # A weak burst must fall out at the rig's threshold and come back
    # when it is lowered. Without this, the knob could do nothing.
    # Tuned to land at ratio ~2.1 -- inside the "people milling 2-4"
    # band the module documents, which is exactly where the threshold
    # has to bite. A stronger burst passes at every setting and proves
    # nothing; a weaker one fails at every setting and proves nothing.
    weak = d / "weak.mp4"
    _write(weak, 30, [(15.0, 0.4, 20)], noise=3.0)
    hi = detect_swings_pi(weak, **p)
    lo = detect_swings_pi(weak, **{**p, "min_ratio": 1.5})
    print(f"  weak burst: {len(hi)} at ratio {p['min_ratio']}, "
          f"{len(lo)} at ratio 1.5")
    assert len(lo) > len(hi), (
        "lowering min_ratio changed nothing — the threshold is not "
        f"load-bearing in this fixture ({len(hi)} vs {len(lo)})")

    # --- walking is rejected on burst WIDTH, not amplitude -----------
    # This is the detector's whole claim: a group crossing the frame
    # moves more pixels than a swing and must still lose.
    walkclip = d / "walk.mp4"
    _write(walkclip, 40, [], walk=(20.0, 9.0))
    dbg_w = {}
    ww = detect_swings_pi(walkclip, debug=dbg_w, **p)
    print(f"  walking -> {len(ww)} window(s), best ratio "
          f"{dbg_w.get('best_ratio')}, {dbg_w.get('rejected_too_wide')} "
          f"rejected for width (widest {dbg_w.get('widest_rejected_sec')}s)")
    assert not ww, (
        "locomotion was accepted as a swing — the width filter is not "
        f"doing its job: {ww}")

    # --- the trace is returned for plotting --------------------------
    assert dbg.get("trace", {}).get("motion"), "no trace returned"
    assert len(dbg["trace"]["times"]) == len(dbg["trace"]["motion"])
    assert dbg.get("person_gate") == "off (tester)", dbg.get("person_gate")
    print(f"  trace: {len(dbg['trace']['times'])} points, "
          f"coverage {dbg.get('coverage'):.2f}")

    print("\nALL PASS")


if __name__ == "__main__":
    main()
