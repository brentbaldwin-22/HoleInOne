"""Can the signal comparison tell signals apart, and is it honest?

NO ASSERTION HERE PICKS A WINNER. Which signal suits hole 8 is a
question about hole 8, and a fixture that settled it would only be
proving that the fixture was built to. What is tested is that the
comparison WORKS: every candidate is computed, scored against known
times, and ranked -- and that the score means something, which is
checked by feeding it times where no swing is.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import numpy as np, cv2
from app.services.pi_swingfind import compare_signals, pi_defaults

W, H, FPS = 640, 360, 30


def build(path, swings, n_walkers=1, swing_px=16, swing_speed=320):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (W, H))
    rng = np.random.default_rng(5)
    for i in range(int(50 * FPS)):
        t = i / FPS
        f = np.full((H, W, 3), 50, np.uint8)
        f = (f + rng.normal(0, 0.8, f.shape)).clip(0, 255).astype(np.uint8)
        for k in range(n_walkers):
            sp = 16.0 + 4 * k
            x = int(60 + 260 * abs(((t + 3 * k) % sp) / sp * 2 - 1))
            f[170:280, x:x + 60] = 205
        for s in swings:
            dt = t - (s - 0.2)
            if 0 <= dt < 0.4:
                xx = 380 + int(dt * swing_speed)
                yy = 190 - int(dt * swing_speed * 0.45)
                f[yy:yy + swing_px, xx:xx + swing_px] = 250
        vw.write(f)
    vw.release()


def main():
    d = Path(tempfile.mkdtemp())
    p = pi_defaults()
    SW = [15.0, 35.0]
    clip = d / "cmp.mp4"
    build(clip, SW)

    out = compare_signals(clip, SW, sample_hz=20.0, **p)
    names = {r["signal"] for r in out["signals"]}
    print("signals scored:", sorted(names))
    for r in out["signals"]:
        b = r["best"]
        print(f"   {r['signal']:<10} sep {str(r['separation']):>6}  "
              f"best {b['min_ratio']}/{b['max_burst_sec']} -> "
              f"{b['caught']}/{b['of']}, {b['false_positives']} false")

    assert {"meandiff", "topk", "localdiff", "flowmax"} <= names, names
    # Ranked by separation, best first.
    seps = [r["separation"] or 0 for r in out["signals"]]
    assert seps == sorted(seps, reverse=True), seps
    # Some signal must find a workable setting when the swing is
    # genuinely there, or the sweep is not reaching far enough.
    assert any(r["best"]["caught"] == len(SW) and
               r["best"]["false_positives"] == 0 for r in out["signals"]), \
        "no signal caught both swings cleanly on an easy clip"

    # CONTROL: THE SCORE HAS TO MEAN SOMETHING. Name times where no
    # swing is and separation must fall -- otherwise "separation 2.0"
    # is a number the tool prints whatever it is shown, and every
    # ranking above it is decoration.
    bogus = [6.0, 27.0]
    out_b = compare_signals(clip, bogus, sample_hz=20.0, **p)
    real = {r["signal"]: (r["separation"] or 0) for r in out["signals"]}
    fake = {r["signal"]: (r["separation"] or 0) for r in out_b["signals"]}
    print("\nseparation at the real swings vs at times with none:")
    for k in sorted(real):
        print(f"   {k:<10} real {real[k]:>6}   bogus {fake.get(k, 0):>6}")
    best_real = max(real.values())
    best_fake = max(fake.values())
    assert best_real > best_fake, (
        f"naming times with no swing scored as well as the real ones "
        f"({best_fake} vs {best_real}) — the separation number is vacuous")

    print(f"\nbest real {best_real} > best bogus {best_fake}")
    print("ALL PASS")


if __name__ == "__main__":
    main()
