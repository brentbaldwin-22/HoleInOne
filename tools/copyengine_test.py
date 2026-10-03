#!/usr/bin/env python3
"""Is the second capture engine really optional, and really parallel?

The whole promise of this change is that a rig keeps behaving exactly
as it did unless its config asks otherwise, and that when the new path
cannot be trusted the old one still records the swing. Both are easy
to break silently, so both are pinned here.

    python3 tools/copyengine_test.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.copyengine import CopyEngine, engine_name  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


RTSP = {"camera": {"device": "rtsp://cam/profile2/media.smp"}}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())

    # ---- the engine is OFF unless asked for -------------------------
    check("no config at all", engine_name({}), "decode")
    check("an unrelated config", engine_name({"buffer_seconds": 5}), "decode")
    check("capture_engine: decode",
          engine_name({"capture_engine": "decode"}), "decode")
    check("a typo does not silently enable it",
          engine_name({"capture_engine": "cpy"}), "decode")
    for spelling in ("copy", "stream-copy", "streamcopy", "COPY", " Copy "):
        check(f"capture_engine: {spelling!r}",
              engine_name({"capture_engine": spelling}), "copy")
    check("camera.engine also selects it",
          engine_name({"camera": {"engine": "copy"}}), "copy")

    check("from_config returns None when not asked",
          CopyEngine.from_config(dict(RTSP), tmp), None)

    # ---- and refuses rather than breaks a camera it cannot serve ----
    for dev in ("auto", "/dev/video0", "0", ""):
        got = CopyEngine.from_config(
            {"capture_engine": "copy", "camera": {"device": dev}}, tmp)
        check(f"a non-RTSP camera ({dev!r}) falls back", got, None)

    # ---- when it IS asked for, it is built and sized sanely ---------
    eng = CopyEngine.from_config(
        {"capture_engine": "copy", "buffer_seconds": 5,
         "max_clip_seconds": 120, **RTSP}, tmp)
    check("an RTSP camera gets an engine", eng is not None, True)
    if eng is not None:
        # The window must outlast the longest clip plus its pre-roll, or
        # the front of a long session is swept while its tail records.
        check("window spans pre-roll + longest clip",
              eng.ring.window_seconds >= 5 + 120, True)
        check("segment default is the camera's 1s GOV",
              eng.ring.segment_seconds, 1.0)

        # ---- ready() is the gate that protects a swing --------------
        # Nothing started: the ring has no video, so the runner must be
        # told to use the path that does.
        check("a ring that was never started is not ready",
              eng.ready(), False)

        eng2 = CopyEngine.from_config(
            {"capture_engine": "copy", "copy_window_seconds": 20, **RTSP}, tmp)
        # A ring pointed at a camera that does not exist: ffmpeg fails,
        # and the engine must NOT claim it can record.
        eng2.ring.ffmpeg = "definitely-not-ffmpeg"
        eng2.start()
        time.sleep(1.0)
        check("a ring whose ffmpeg will not run is not ready",
              eng2.ready(), False)
        eng2.stop()

        st = eng.status()
        check("status names the engine", st["engine"], "copy")
        check("status starts with no clips", st["clips_extracted"], 0)

    # ---- the runners branch on ready(), not on existence ------------
    import re
    for runner in ("tee", "green"):
        src = (ROOT / "pi-agent" / "agent" / f"{runner}.py").read_text()
        check(f"{runner}.py asks ready() before committing a swing",
              bool(re.search(r"_copy is not None and _copy\.ready\(\)", src)),
              True)
        check(f"{runner}.py still has the decode path intact",
              "def _record_and_upload(" in src, True)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
