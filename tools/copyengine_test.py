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

from agent.copyengine import (  # noqa: E402
    CopyEngine, copy_configured, engine_name,
)
from agent.tee import preroll_seconds  # noqa: E402

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

    # ---- the bugs a real 156s clip off the green camera exposed ----
    src = (ROOT / "pi-agent" / "agent" / "common.py").read_text()

    # A clip spooled WITHOUT an attempt (the link was busy) took a
    # different route to disk than _send, and that route re-encoded the
    # camera's 1080p H.264 down to 720p despite precompressed=True.
    import re
    pre_spool = re.search(
        r"if self\._kbps_now > 0 and not _precompressed:", src)
    check("the pre-spool compression honours precompressed",
          bool(pre_spool), True)

    # Every uploader-queue unpack must take the full tuple. The drain
    # path only runs while shutting down with clips still queued, so a
    # stale unpack there throws exactly where footage is being saved.
    unpacks = re.findall(r"self\._q\.get(?:_nowait)?\(", src)
    check("every uploader queue read was found", len(unpacks) >= 2, True)
    check("no 4-field unpack of the uploader queue survives",
          bool(re.search(r"\w+, \w+, \w+, \w+ = self\._q\.get", src)), False)

    # And the spool must record what is true, not what used to be.
    check("spool records the real compression state",
          '"compressed": bool(compressed)' in src, True)

    cap = (ROOT / "pi-agent" / "agent" / "copycap.py").read_text()
    check("staging links rather than copies", "os.link(p, dst)" in cap, True)
    check("extract reports what the clip HOLDS, not the span it covers",
          '"seconds": round(len(parts) * self.segment_seconds, 3)' in cap,
          True)
    check("extract reports the gap", '"missing_seconds": missing' in cap, True)

    # ---- the regression that cost green 269MB clips all day ------
    # The decode path sends /event-stop from inside _record_and_upload.
    # The copy engine REPLACES that method, so switching engines
    # silently stopped the tee telling its partner the swing was over —
    # and a green that is never told records to its 150s cap. Measured:
    # 139 polls, 0 failures, length_cap. The polling was perfect; there
    # was simply nothing to hear.
    import re
    tee_src = (ROOT / "pi-agent" / "agent" / "tee.py").read_text()
    eng_src = (ROOT / "pi-agent" / "agent" / "copyengine.py").read_text()

    check("the tee hands the engine a way to signal the end",
          bool(re.search(r"on_recording_ended\s*=", tee_src)), True)
    check("and that way calls event_stop",
          "def _signal_event_stop" in tee_src
          and "self.client.event_stop(session_id)" in tee_src, True)
    check("the engine accepts it", "on_recording_ended" in eng_src, True)

    # It must fire BEFORE the cut, not after: extraction takes 1-17s
    # and the green keeps rolling for every one of them.
    i_signal = eng_src.index("on_recording_ended()")
    i_extract = eng_src.index("self.ring.extract(")
    check("and fires before the clip is cut, not after",
          i_signal < i_extract, True)

    # A green must never send it — that is the tee's job alone.
    green_src = (ROOT / "pi-agent" / "agent" / "green.py").read_text()
    check("the green does not signal stop for itself",
          "on_recording_ended" in green_src, False)

    # ---- the pre-roll ring is sized by whoever will RECORD ----------
    # Both engines were filling a pre-roll at once: the copy engine's
    # 155s segment ring on disk AND 301 decoded 1080p60 frames in RAM
    # (~1.9GB) that nothing read, because clips are cut from the
    # segments. The RAM ring still has to exist for the detector and for
    # the decode fallback, so it shrinks rather than disappearing.
    copy_cfg = {"capture_engine": "copy", "buffer_seconds": 5, **RTSP}
    check("decode keeps the full pre-roll",
          preroll_seconds({"buffer_seconds": 5}), 5.0)
    check("copy drops to the fallback depth",
          preroll_seconds(copy_cfg), 2.0)
    check("and the fallback depth is configurable",
          preroll_seconds({**copy_cfg, "copy_fallback_preroll_seconds": 3}),
          3.0)
    # The gate is BOTH conditions. `capture_engine: copy` on a USB camera
    # leaves the agent decoding, so shrinking its ring there would cut
    # the pre-roll off every clip it records.
    check("copy asked for on a non-RTSP camera still decodes",
          copy_configured({"capture_engine": "copy",
                           "camera": {"device": "/dev/video0"}}), False)
    check("so that config keeps the full pre-roll",
          preroll_seconds({"capture_engine": "copy", "buffer_seconds": 5,
                           "camera": {"device": "/dev/video0"}}), 5.0)
    # One source of truth: the sizing and the engine must agree, or a
    # rig decodes with a 2s ring.
    check("copy_configured agrees with from_config",
          copy_configured(copy_cfg),
          CopyEngine.from_config(copy_cfg, tmp) is not None)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
