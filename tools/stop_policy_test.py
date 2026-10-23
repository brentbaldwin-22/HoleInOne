"""The rule that ends a tee clip, driven against a synthetic tee box.

WHY THIS EXISTS. `no_person_timeout_seconds` is one number in config
and the whole reason every clip on hole 8 ran to its 120s cap. The
number is easy to change and easy to change back by accident; what
needs pinning is the BEHAVIOUR it buys -- that the clip ends that long
after the last person leaves, and not before.

Every assertion is paired with its opposite, because "the clip ended"
passes trivially for a policy that ends every clip immediately.
"""
import sys, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi-agent"))
from agent import tee


class _Buf:
    def snapshot(self):
        # One frame, newest last: the policy only reads snap[-1].
        return [(0.0, object())]


class _Det:
    """Says a person is in the zone during the given windows."""
    def __init__(self, windows):
        self.windows = windows
        self.now = 0.0

    def detect(self, _frame):
        return "person" if any(a <= self.now < b for a, b in self.windows) else None


class _Runner:
    def __init__(self):
        self.buffer = _Buf()
        self._roi_lock = threading.Lock()
        self.roi = {"boxes": [{"x": 0, "y": 0, "w": 100, "h": 100}]}
        self._person_seen_at = []


def run(windows, timeout, *, until=200.0, det_period=0.3):
    """Drive the policy over `until` seconds. Returns when it stopped."""
    runner = _Runner()
    det = _Det(windows)
    # _in_roi is a module function over a real ROI shape; the point here
    # is the TIMING, so presence is decided by the detector stub alone.
    orig = tee._in_roi
    tee._in_roi = lambda stance, roi: stance is not None
    try:
        stop = tee.TeeAgent._copy_stop_policy(
            runner, det, timeout, det_period)
        t = 0.0
        # The policy seeds last_seen from time.time(); drive it with the
        # same clock it reads so the first poll is not already stale.
        import time as _t
        base = _t.time()
        while t < until:
            det.now = t
            why = stop(base + t)
            if why:
                return t, why
            t += det_period
        return None, None
    finally:
        tee._in_roi = orig


def main():
    # A golfer present 0-10s, then gone. A 12s timeout must end the
    # clip at ~22s: ten seconds of them, twelve of nobody.
    at, why = run([(0.0, 10.0)], 12.0)
    print(f"present 0-10s, timeout 12 -> ended at {at:.1f}s ({why})")
    assert why == "no_person", why
    assert 21.0 <= at <= 23.0, at

    # CONTROL: the same footage with the rig's shipped 30s must end
    # much later. If both end together the timeout is doing nothing.
    at30, _ = run([(0.0, 10.0)], 30.0)
    print(f"present 0-10s, timeout 30 -> ended at {at30:.1f}s")
    assert at30 > at + 15, (at, at30)

    # HOLE 8'S ACTUAL SHAPE: somebody in the zone every ~8 seconds for
    # two minutes. At 30s the clock never expires and the clip runs to
    # its cap -- which is the bug, reproduced.
    busy = [(float(k), float(k) + 1.0) for k in range(0, 200, 8)]
    at_busy, _ = run(busy, 30.0, until=150.0)
    print(f"someone every 8s, timeout 30 -> "
          f"{'never ended (runs to the cap)' if at_busy is None else at_busy}")
    assert at_busy is None, (
        f"ended at {at_busy}: the fixture does not reproduce the bug")

    # ...and 12s does not rescue THAT case either, which is the honest
    # limit of this change: a zone somebody stands in continuously
    # still records to the cap. The fix is for the gaps, not the crowd.
    at_busy12, _ = run(busy, 12.0, until=150.0)
    print(f"someone every 8s, timeout 12 -> "
          f"{'still never ends' if at_busy12 is None else at_busy12}")
    assert at_busy12 is None, at_busy12

    # Where it DOES bite: a realistic gap between groups. Present for
    # 10s, away 15s, back again -- 30s rides straight through the gap,
    # 12s ends the clip inside it.
    gap = [(0.0, 10.0), (25.0, 40.0)]
    a12, _ = run(gap, 12.0)
    a30, _ = run(gap, 30.0)
    print(f"10s on, 15s gap, back at 25s: timeout 12 ends at {a12:.1f}s, "
          f"timeout 30 ends at {a30:.1f}s")
    assert a12 < 25.0, f"12s should end inside the gap, ended {a12}"
    assert a30 > 40.0, f"30s should ride through the gap, ended {a30}"

    # The sightings list the swing selector gates on is still filled.
    runner = _Runner()
    det = _Det([(0.0, 5.0)])
    orig = tee._in_roi
    tee._in_roi = lambda stance, roi: stance is not None
    try:
        stop = tee.TeeAgent._copy_stop_policy(runner, det, 12.0, 0.3)
        import time as _t
        base = _t.time()
        for i in range(30):
            det.now = i * 0.3
            stop(base + i * 0.3)
    finally:
        tee._in_roi = orig
    print(f"person sightings recorded: {len(runner._person_seen_at)}")
    assert runner._person_seen_at, (
        "no sightings recorded — swing_select's person gate would reject "
        "every peak on a copy rig")

    # --- the code fallback and the shipped config must agree --------
    # They disagreed for the life of both files (30 in the example, 5
    # in the code), which means a rig that omits the key behaves
    # nothing like the documented default and nothing like every other
    # rig. Harmless only for as long as nobody omits it.
    import re
    root = Path(__file__).resolve().parents[1] / "pi-agent"
    cfg_txt = (root / "config.example.yaml").read_text()
    tee_txt = (root / "agent" / "tee.py").read_text()
    m_cfg = re.search(r"^\s*no_person_timeout_seconds:\s*([0-9.]+)",
                      cfg_txt, re.M)
    m_code = re.search(
        r'no_person_timeout_seconds"\s*,\s*([0-9.]+)\s*\)', tee_txt)
    assert m_cfg, "no_person_timeout_seconds missing from config.example.yaml"
    assert m_code, "no fallback found in tee.py"
    cfg_v, code_v = float(m_cfg.group(1)), float(m_code.group(1))
    print(f"shipped config {cfg_v}s, code fallback {code_v}s")
    assert cfg_v == code_v, (
        f"config.example.yaml ships {cfg_v}s but tee.py falls back to "
        f"{code_v}s — a rig omitting the key gets neither what the "
        f"config documents nor what every other rig runs")

    print("\nALL PASS")


if __name__ == "__main__":
    main()
