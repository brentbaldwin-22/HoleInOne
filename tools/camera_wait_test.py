#!/usr/bin/env python3
"""Does a rig with an unplugged camera stay up and keep reporting?

It did not. open_camera() is the first thing run() does, so a missing
camera took the process down before the heartbeat that carries the
real firmware string, before the uploader, and before the uplink and
temperature readers. systemd restarted it and it died again --
observed, 1062 restarts in twelve hours, with the camera card showing
`fw bootstrap` and a snapshot from the night before while the rig was
up and reachable the whole time.

The diagnostics you most want are the ones a broken rig stops sending,
so the fix is not "restart less" but "keep answering while waiting".

    python3 tools/camera_wait_test.py
"""
from __future__ import annotations

import signal
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.common import CameraUnavailable  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class FakeClock:
    """A clock the test drives, so backoff costs no wall time."""

    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, secs):
        self.now += secs
        self.slept += secs
        if self.on_sleep:
            self.on_sleep()


class FakeClient:
    def __init__(self):
        self.beats: list[dict] = []

    def heartbeat(self, firmware_version="", extra=None):
        self.beats.append({"fw": firmware_version, "extra": extra or {}})
        return {"assigned_role": "green", "enabled": True, "camera_id": 1}


# main() refuses to start without a config FILE on disk, before it
# ever calls load_config -- so the stub needs something to point at.
CFG = Path(tempfile.mkdtemp()) / "config.yaml"
CFG.write_text("backend_url: http://x\nauth_token: t\n")


def build(fail_times: int):
    """Load the entry point with every edge of it stubbed."""
    import importlib

    import golfreelz_agent as ga
    importlib.reload(ga)

    state = {"runs": 0, "stopped": 0}

    class FakeAgent:
        def __init__(self, _cfg):
            pass

        def run(self):
            state["runs"] += 1
            if state["runs"] <= fail_times:
                raise CameraUnavailable("no RTSP stream")

        def stop(self):
            state["stopped"] += 1

    green = types.ModuleType("agent.green")
    green.FIRMWARE = "green-0.1.0+abc1234"
    green.GreenAgent = FakeAgent
    sys.modules["agent.green"] = green

    curfew = types.ModuleType("agent.curfew")
    curfew.CurfewThread = lambda *a, **k: types.SimpleNamespace(
        start=lambda: None)
    sys.modules["agent.curfew"] = curfew

    client = FakeClient()
    clock = FakeClock()
    ga.time = clock
    ga.install_dns_cache = lambda: None
    ga.load_config = lambda p: {"backend_url": "http://x", "auth_token": "t"}
    ga.BackendClient = lambda *a, **k: client
    ga.camera_free_telemetry = lambda: {"throttled": '{"temp_c": 61.0}'}
    return ga, state, client, clock


def main() -> int:
    # ---- it waits, and comes up when the camera appears -------------
    ga, state, client, clock = build(fail_times=2)
    check("main does not exit on a missing camera", ga.main(["x", str(CFG)]), 0)
    check("it retried until the camera came back", state["runs"], 3)

    waiting = [b for b in client.beats if b["fw"] != "bootstrap"]
    check("it heartbeated once per wait", len(waiting), 2)

    # THE WHOLE POINT. A waiting rig must report as ITSELF, not as the
    # bootstrap stub -- `fw bootstrap` on the card is precisely the
    # symptom this fixes.
    check("and as the real firmware, not 'bootstrap'",
          {b["fw"] for b in waiting}, {"green-0.1.0+abc1234"})
    check("carrying the telemetry that needs no camera",
          all("throttled" in b["extra"] for b in waiting), True)

    # Backoff doubles rather than hammering: 5 then 10.
    check("it backs off instead of spinning", clock.slept, 15.0)

    # ---- the control: a healthy camera never waits ------------------
    # Without this, "it retries" would pass on code that always slept.
    ga2, state2, client2, clock2 = build(fail_times=0)
    check("a working camera runs first time", ga2.main(["x", str(CFG)]), 0)
    check("exactly one run, no retries", state2["runs"], 1)
    check("and no waiting heartbeats at all",
          [b for b in client2.beats if b["fw"] != "bootstrap"], [])
    check("and nothing slept", clock2.slept, 0.0)

    # ---- a stop during the wait is honoured -------------------------
    # The backoff reaches 60s. If the sleep were one call, systemd's
    # stop would hang behind it; it is sliced so the flag is seen.
    ga3, state3, client3, clock3 = build(fail_times=10_000)

    def _stop_it():
        handler = signal.getsignal(signal.SIGTERM)
        if callable(handler):
            handler(signal.SIGTERM, None)
    clock3.on_sleep = _stop_it

    check("a stop breaks the wait loop", ga3.main(["x", str(CFG)]), 0)
    check("it did not keep retrying after the stop", state3["runs"], 1)
    check("and the agent was told to stop", state3["stopped"] >= 1, True)
    check("and it stopped within one slice, not a whole backoff",
          clock3.slept <= 1.0, True)

    # ---- an ordinary bug still takes the process down ---------------
    # CameraUnavailable is a separate type precisely so that everything
    # else still crashes loudly and gets a clean restart.
    ga4, state4, _c4, _k4 = build(fail_times=0)
    boom = types.ModuleType("agent.green")
    boom.FIRMWARE = "green-0.1.0+abc1234"

    class Exploding:
        def __init__(self, _cfg):
            pass

        def run(self):
            raise ValueError("a real bug")

        def stop(self):
            pass
    boom.GreenAgent = Exploding
    sys.modules["agent.green"] = boom
    try:
        ga4.main(["x", str(CFG)])
        check("a non-camera error is not swallowed", "no raise", "ValueError")
    except ValueError:
        check("a non-camera error is not swallowed", "ValueError", "ValueError")

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
